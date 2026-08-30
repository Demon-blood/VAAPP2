from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations import enable_banking
from app.models.entities import (
    BankAccount,
    Bill,
    Payment,
    PaymentRecoveryEvidence,
    Task,
)
from app.services.audit import write_audit

_OPEN_TASK_STATUSES = ("open", "waiting")
_UNCERTAIN_STATUS = "creation_uncertain"
_MATCH_WINDOW_DAYS = 4


def _naive_utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value)).copy_abs().quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _transaction_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[Any] = []
    for key in ("transactions", "booked", "items", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            candidates.extend(value)
        elif isinstance(value, dict):
            for nested in ("transactions", "booked", "items"):
                nested_value = value.get(nested)
                if isinstance(nested_value, list):
                    candidates.extend(nested_value)
    return [row for row in candidates if isinstance(row, dict)]


def _transaction_amount(row: dict[str, Any]) -> tuple[Decimal | None, str]:
    currency = ""
    for key in ("transaction_amount", "amount", "booking_amount"):
        value = row.get(key)
        raw_amount: Any = value
        if isinstance(value, dict):
            raw_amount = value.get("amount") or value.get("value")
            currency = str(value.get("currency") or value.get("currency_code") or currency)
        amount = _decimal(raw_amount)
        if amount is not None:
            return amount, currency.upper()[:3]
    return None, currency.upper()[:3]


def _transaction_date(row: dict[str, Any]) -> datetime | None:
    for key in ("booking_date", "value_date", "transaction_date", "date"):
        raw = str(row.get(key) or "").strip()
        if not raw:
            continue
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            try:
                parsed = datetime.fromisoformat(raw[:10])
            except ValueError:
                continue
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(UTC).replace(tzinfo=None)
        return parsed
    return None


def _transaction_id(row: dict[str, Any]) -> str:
    for key in ("transaction_id", "entry_reference", "id"):
        value = str(row.get(key) or "").strip()
        if value:
            return value[:255]
    return ""


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _transaction_text(row: dict[str, Any]) -> str:
    values: list[str] = []
    for key in (
        "creditor_name",
        "debtor_name",
        "remittance_information",
        "remittance_information_unstructured",
        "remittance_information_structured",
        "reference",
        "merchant_name",
        "description",
        "additional_information",
        "creditor_account",
    ):
        value = row.get(key)
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, list):
            values.extend(str(item) for item in value)
        elif isinstance(value, dict):
            values.extend(str(item) for item in value.values())
    return _normalize(" ".join(values))


def _creditor_tokens(name: str) -> set[str]:
    ignored = {
        "bank",
        "belgium",
        "company",
        "corp",
        "gmbh",
        "limited",
        "ltd",
        "payments",
        "service",
        "services",
    }
    return {
        token
        for token in re.findall(r"[a-z0-9]+", name.casefold())
        if len(token) >= 5 and token not in ignored
    }


def _match_basis(bill: Bill, row: dict[str, Any]) -> str:
    text = _transaction_text(row)
    if not text:
        return ""

    for label, value in (("reference", bill.reference), ("invoice", bill.invoice_number)):
        candidate = _normalize(value or "")
        if len(candidate) >= 4 and candidate in text:
            return label

    iban = _normalize(bill.iban or "")
    if len(iban) >= 8 and iban[-8:] in text:
        return "creditor_iban"

    tokens = _creditor_tokens(bill.creditor_name or "")
    matched = sorted(token for token in tokens if token in text)
    if len(matched) >= 2:
        return "creditor_name"
    if len(matched) == 1 and len(matched[0]) >= 7:
        return "creditor_name"
    return ""


async def _close_legacy_uncertainty_tasks(db: AsyncSession, payment_id: int) -> int:
    rows = list(
        (
            await db.execute(
                select(Task).where(
                    Task.source_type == "payment_creation_uncertain",
                    Task.source_id == str(payment_id),
                    Task.status.in_(_OPEN_TASK_STATUSES),
                )
            )
        ).scalars()
    )
    for row in rows:
        row.status = "completed"
    return len(rows)


async def reconcile_uncertain_payment(
    db: AsyncSession,
    payment: Payment,
) -> dict[str, Any]:
    """Resolve an uncertain payment creation only from independent bank evidence.

    A missing provider response is a VA-owned system/provider uncertainty, not a human
    authorization boundary. The payment remains active so no automatic retry can create
    a duplicate. Completion requires exactly one booked bank transaction with matching
    amount/currency/time and strong creditor/reference evidence.
    """

    if payment.status != _UNCERTAIN_STATUS or payment.external_payment_id:
        return {"payment_id": payment.id, "state": "not_applicable", "matched": 0}

    payment.requires_user_action = False
    closed_tasks = await _close_legacy_uncertainty_tasks(db, payment.id)

    bill = await db.get(Bill, payment.bill_id)
    account = await db.get(BankAccount, payment.bank_account_id) if payment.bank_account_id else None
    if bill is None or account is None or not account.external_account_id:
        await db.commit()
        return {
            "payment_id": payment.id,
            "state": "system_owned_missing_context",
            "matched": 0,
            "closed_legacy_tasks": closed_tasks,
        }

    date_from = (payment.created_at - timedelta(days=2)).date().isoformat()
    try:
        payload = await enable_banking.get_account_transactions(
            db,
            account.external_account_id,
            date_from=date_from,
        )
    except (enable_banking.EnableBankingConfigurationError, httpx.RequestError, json.JSONDecodeError) as exc:
        await write_audit(
            db,
            "payment_creation_reconciliation_provider_failed",
            entity_type="payment",
            entity_id=str(payment.id),
            result="failed",
            details={"ownership": "va", "error": str(exc)[:1000]},
        )
        await db.commit()
        return {
            "payment_id": payment.id,
            "state": "provider_unavailable",
            "matched": 0,
            "closed_legacy_tasks": closed_tasks,
        }

    candidates: list[tuple[dict[str, Any], str, str, datetime]] = []
    for row in _transaction_rows(payload if isinstance(payload, dict) else {}):
        amount, currency = _transaction_amount(row)
        if amount is None or amount != _decimal(payment.amount):
            continue
        if currency and currency != payment.currency.upper():
            continue
        transaction_date = _transaction_date(row)
        if transaction_date is None:
            continue
        if abs((transaction_date.date() - payment.created_at.date()).days) > _MATCH_WINDOW_DAYS:
            continue
        basis = _match_basis(bill, row)
        if not basis:
            continue
        transaction_id = _transaction_id(row)
        if not transaction_id:
            continue
        candidates.append((row, transaction_id, basis, transaction_date))

    if len(candidates) != 1:
        state = "ambiguous" if len(candidates) > 1 else "waiting_for_evidence"
        await write_audit(
            db,
            "payment_creation_reconciliation_waiting",
            entity_type="payment",
            entity_id=str(payment.id),
            result="blocked",
            details={
                "ownership": "va",
                "candidate_count": len(candidates),
                "state": state,
                "automatic_retry": False,
            },
        )
        await db.commit()
        return {
            "payment_id": payment.id,
            "state": state,
            "matched": 0,
            "candidate_count": len(candidates),
            "closed_legacy_tasks": closed_tasks,
        }

    _, transaction_id, basis, transaction_date = candidates[0]
    evidence = (
        await db.execute(
            select(PaymentRecoveryEvidence).where(
                PaymentRecoveryEvidence.payment_id == payment.id,
                PaymentRecoveryEvidence.transaction_id == transaction_id,
            )
        )
    ).scalar_one_or_none()
    if evidence is None:
        evidence = PaymentRecoveryEvidence(
            payment_id=payment.id,
            bank_account_id=account.id,
            transaction_id=transaction_id,
            match_basis=basis,
            observed_at=_naive_utc_now(),
            details_json=json.dumps(
                {
                    "amount": str(payment.amount),
                    "currency": payment.currency,
                    "booking_date": transaction_date.date().isoformat(),
                    "match_basis": basis,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
        db.add(evidence)

    payment.status = "completed"
    payment.requires_user_action = False
    payment.authorization_url = None
    payment.failure_reason = ""
    bill.status = "paid"
    await write_audit(
        db,
        "payment_creation_uncertainty_recovered",
        entity_type="payment",
        entity_id=str(payment.id),
        details={
            "bank_account_id": account.id,
            "transaction_id": transaction_id,
            "match_basis": basis,
            "completion_evidence": "booked_bank_transaction",
        },
    )
    await db.commit()
    return {
        "payment_id": payment.id,
        "state": "recovered",
        "matched": 1,
        "transaction_id": transaction_id,
        "match_basis": basis,
        "closed_legacy_tasks": closed_tasks,
    }


async def reconcile_all_uncertain_payments(
    db: AsyncSession,
    *,
    limit: int = 50,
) -> dict[str, int]:
    rows = list(
        (
            await db.execute(
                select(Payment)
                .where(
                    Payment.status == _UNCERTAIN_STATUS,
                    Payment.external_payment_id.is_(None),
                )
                .order_by(Payment.created_at.asc(), Payment.id.asc())
                .limit(max(1, min(limit, 250)))
            )
        ).scalars()
    )
    result = {
        "reviewed": len(rows),
        "recovered": 0,
        "waiting": 0,
        "ambiguous": 0,
        "provider_unavailable": 0,
    }
    for payment in rows:
        outcome = await reconcile_uncertain_payment(db, payment)
        state = str(outcome.get("state") or "")
        if state == "recovered":
            result["recovered"] += 1
        elif state == "ambiguous":
            result["ambiguous"] += 1
        elif state == "provider_unavailable":
            result["provider_unavailable"] += 1
        else:
            result["waiting"] += 1
    return result
