from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations import enable_banking
from app.integrations.kraken_api import (
    KrakenConfigurationError,
    get_orders_by_client_order_id,
)
from app.models.entities import (
    BankAccount,
    InvestmentFundingRecoveryEvidence,
    InvestmentFundingTransfer,
    InvestmentTradeIntent,
    Task,
)
from app.services.audit import write_audit

_OPEN_TASK_STATUSES = ("open", "waiting")
_MATCH_WINDOW_DAYS = 4


def _money(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value)).copy_abs().quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _iban(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).upper()


def _norm_text(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def _transaction_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[Any] = []
    for key in ("transactions", "booked", "items", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            rows.extend(value)
        elif isinstance(value, dict):
            for nested in ("transactions", "booked", "items"):
                nested_value = value.get(nested)
                if isinstance(nested_value, list):
                    rows.extend(nested_value)
    return [row for row in rows if isinstance(row, dict)]


def _transaction_amount(row: dict[str, Any]) -> tuple[Decimal | None, str]:
    currency = ""
    for key in ("transaction_amount", "amount", "booking_amount"):
        value = row.get(key)
        raw: Any = value
        if isinstance(value, dict):
            raw = value.get("amount") or value.get("value")
            currency = str(value.get("currency") or value.get("currency_code") or currency)
        amount = _money(raw)
        if amount is not None:
            return amount, currency.upper()[:3]
    return None, currency.upper()[:3]


def _transaction_date(row: dict[str, Any]) -> datetime | None:
    for key in ("booking_date", "booking_date_time", "value_date", "value_date_time"):
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


def _transaction_direction(row: dict[str, Any]) -> str:
    value = str(row.get("credit_debit_indicator") or row.get("direction") or "").upper()
    if value.startswith("DB") or value == "DEBIT":
        return "debit"
    if value.startswith("CR") or value == "CREDIT":
        return "credit"
    return ""


def _destination_iban(row: dict[str, Any]) -> str:
    for key in ("creditor_account", "counterparty_account"):
        value = row.get(key)
        if isinstance(value, dict):
            iban = _iban(value.get("iban") or value.get("identification"))
            if iban:
                return iban
    for key in ("creditor_iban", "counterparty_iban"):
        iban = _iban(row.get(key))
        if iban:
            return iban
    return ""


def _remittance_text(row: dict[str, Any]) -> str:
    values: list[str] = []
    for key in (
        "remittance_information_unstructured",
        "remittance_information",
        "remittance",
        "reference",
        "end_to_end_id",
    ):
        value = row.get(key)
        if isinstance(value, list):
            values.extend(str(item) for item in value)
        elif isinstance(value, dict):
            values.extend(str(item) for item in value.values())
        elif value not in (None, ""):
            values.append(str(value))
    return " ".join(values)


async def _close_legacy_uncertainty_tasks(db: AsyncSession, transfer_id: int) -> int:
    rows = list(
        (
            await db.execute(
                select(Task).where(
                    Task.source_type == "kraken_funding_uncertain",
                    Task.source_id == str(transfer_id),
                    Task.status.in_(_OPEN_TASK_STATUSES),
                )
            )
        ).scalars()
    )
    for row in rows:
        row.status = "completed"
    return len(rows)


async def reconcile_uncertain_kraken_funding(
    db: AsyncSession,
    transfer: InvestmentFundingTransfer,
) -> dict[str, Any]:
    """Recover an unbound funding payment only from one unique booked debit."""
    if transfer.status != "creation_uncertain" or transfer.external_payment_id:
        return {"transfer_id": transfer.id, "state": "not_applicable", "matched": 0}

    transfer.requires_user_action = False
    transfer.authorization_url = None
    closed_tasks = await _close_legacy_uncertainty_tasks(db, transfer.id)
    source = await db.get(BankAccount, transfer.source_bank_account_id)
    if source is None or not source.external_account_id or not _iban(transfer.creditor_iban):
        await db.commit()
        return {
            "transfer_id": transfer.id,
            "state": "system_owned_missing_context",
            "matched": 0,
            "closed_legacy_tasks": closed_tasks,
        }

    try:
        payload = await enable_banking.get_account_transactions(
            db,
            source.external_account_id,
            date_from=(transfer.created_at - timedelta(days=2)).date().isoformat(),
        )
    except (enable_banking.EnableBankingConfigurationError, httpx.RequestError, json.JSONDecodeError) as exc:
        transfer.failure_reason = f"Kraken funding evidence check deferred: {exc}"[:2000]
        await write_audit(
            db,
            "kraken_funding_creation_reconciliation_provider_failed",
            entity_type="investment_funding_transfer",
            entity_id=str(transfer.id),
            result="failed",
            details={"ownership": "va", "error": str(exc)[:1000]},
        )
        await db.commit()
        return {
            "transfer_id": transfer.id,
            "state": "provider_unavailable",
            "matched": 0,
            "closed_legacy_tasks": closed_tasks,
        }

    creditor_iban = _iban(transfer.creditor_iban)
    expected_reference = _norm_text(transfer.reference)
    candidates: list[tuple[str, datetime]] = []
    for row in _transaction_rows(payload if isinstance(payload, dict) else {}):
        if _transaction_direction(row) != "debit":
            continue
        amount, currency = _transaction_amount(row)
        if amount is None or amount != _money(transfer.amount):
            continue
        if currency and currency != transfer.currency.upper():
            continue
        if _destination_iban(row) != creditor_iban:
            continue
        if expected_reference and expected_reference not in _norm_text(_remittance_text(row)):
            continue
        booked = _transaction_date(row)
        if booked is None:
            continue
        if abs((booked.date() - transfer.created_at.date()).days) > _MATCH_WINDOW_DAYS:
            continue
        transaction_id = _transaction_id(row)
        if not transaction_id:
            continue
        binding = (
            await db.execute(
                select(InvestmentFundingRecoveryEvidence).where(
                    InvestmentFundingRecoveryEvidence.bank_account_id == source.id,
                    InvestmentFundingRecoveryEvidence.transaction_id == transaction_id,
                )
            )
        ).scalar_one_or_none()
        if binding is not None and binding.transfer_id != transfer.id:
            continue
        candidates.append((transaction_id, booked))

    if len(candidates) != 1:
        state = "ambiguous" if len(candidates) > 1 else "waiting_for_evidence"
        transfer.failure_reason = (
            "Kraken funding creation remains under VA-owned bank reconciliation; "
            f"{len(candidates)} unique booked candidates matched"
        )[:2000]
        await write_audit(
            db,
            "kraken_funding_creation_reconciliation_waiting",
            entity_type="investment_funding_transfer",
            entity_id=str(transfer.id),
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
            "transfer_id": transfer.id,
            "state": state,
            "matched": 0,
            "candidate_count": len(candidates),
            "closed_legacy_tasks": closed_tasks,
        }

    transaction_id, booked = candidates[0]
    evidence = (
        await db.execute(
            select(InvestmentFundingRecoveryEvidence).where(
                InvestmentFundingRecoveryEvidence.transfer_id == transfer.id
            )
        )
    ).scalar_one_or_none()
    if evidence is None:
        evidence = InvestmentFundingRecoveryEvidence(
            transfer_id=transfer.id,
            bank_account_id=source.id,
            transaction_id=transaction_id,
            match_basis=(
                "source_amount_currency_creditor_iban_reference_date"
                if expected_reference
                else "source_amount_currency_creditor_iban_date"
            ),
            observed_at=datetime.now(UTC).replace(tzinfo=None),
            details_json=json.dumps(
                {
                    "amount": str(transfer.amount),
                    "currency": transfer.currency,
                    "creditor_iban": creditor_iban,
                    "reference_present": bool(expected_reference),
                    "booking_date": booked.date().isoformat(),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
        db.add(evidence)

    transfer.status = "awaiting_deposit"
    transfer.requires_user_action = False
    transfer.authorization_url = None
    transfer.failure_reason = ""
    await write_audit(
        db,
        "kraken_funding_creation_uncertainty_recovered",
        entity_type="investment_funding_transfer",
        entity_id=str(transfer.id),
        details={
            "source_account_id": source.id,
            "transaction_id": transaction_id,
            "booking_date": booked.date().isoformat(),
            "completion_evidence": "booked_bank_transaction",
            "automatic_retry": False,
        },
    )
    await db.commit()
    return {
        "transfer_id": transfer.id,
        "state": "recovered",
        "matched": 1,
        "transaction_id": transaction_id,
        "closed_legacy_tasks": closed_tasks,
    }


def stable_trade_client_order_id(transfer: InvestmentFundingTransfer) -> str:
    digest = hashlib.sha256(transfer.idempotency_key.encode("utf-8")).hexdigest()
    return f"vatr{digest[:14]}"


def legacy_trade_client_order_id(transfer: InvestmentFundingTransfer) -> str:
    return f"va{transfer.id}{int(transfer.created_at.timestamp())}"[:18]


async def prepare_kraken_trade_intent(
    db: AsyncSession,
    transfer: InvestmentFundingTransfer,
    *,
    pair: str,
    eur_amount: Decimal,
    legacy_recovery: bool = False,
) -> InvestmentTradeIntent:
    existing = (
        await db.execute(
            select(InvestmentTradeIntent).where(
                InvestmentTradeIntent.transfer_id == transfer.id
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    client_order_id = (
        legacy_trade_client_order_id(transfer)
        if legacy_recovery
        else stable_trade_client_order_id(transfer)
    )
    intent = InvestmentTradeIntent(
        transfer_id=transfer.id,
        client_order_id=client_order_id,
        pair=pair[:40],
        eur_amount=eur_amount,
        status="creation_uncertain" if legacy_recovery else "prepared",
    )
    db.add(intent)
    await db.commit()
    await db.refresh(intent)
    return intent


async def reconcile_kraken_trade_intent(
    db: AsyncSession,
    transfer: InvestmentFundingTransfer,
    intent: InvestmentTradeIntent,
) -> dict[str, Any]:
    """Bind an ambiguous AddOrder to one provider order without replaying it."""
    if intent.provider_order_id:
        transfer.trade_order_id = intent.provider_order_id
        transfer.status = "invested"
        transfer.failure_reason = ""
        intent.status = "verified"
        if intent.verified_at is None:
            intent.verified_at = datetime.now(UTC).replace(tzinfo=None)
        await db.commit()
        return {"state": "verified", "order_id": intent.provider_order_id}

    try:
        orders = await get_orders_by_client_order_id(db, intent.client_order_id)
    except (KrakenConfigurationError, httpx.RequestError, TimeoutError) as exc:
        transfer.status = "trade_pending"
        transfer.failure_reason = f"Kraken trade evidence check deferred: {exc}"[:2000]
        await db.commit()
        return {"state": "provider_unavailable", "order_id": ""}

    candidates: dict[str, dict[str, Any]] = {}
    for item in orders:
        order_id = str(item.get("order_id") or "").strip()
        if order_id:
            candidates[order_id] = item
    if len(candidates) != 1:
        transfer.status = "trade_pending"
        intent.status = "creation_uncertain"
        transfer.failure_reason = (
            "Kraken market-order outcome remains under VA-owned reconciliation; "
            f"{len(candidates)} provider orders matched client order id {intent.client_order_id}"
        )[:2000]
        await write_audit(
            db,
            "kraken_trade_reconciliation_waiting",
            entity_type="investment_funding_transfer",
            entity_id=str(transfer.id),
            result="blocked",
            details={
                "client_order_id": intent.client_order_id,
                "candidate_count": len(candidates),
                "automatic_retry": False,
            },
        )
        await db.commit()
        return {
            "state": "ambiguous" if len(candidates) > 1 else "waiting_for_evidence",
            "order_id": "",
            "candidate_count": len(candidates),
        }

    order_id, observed = next(iter(candidates.items()))
    intent.provider_order_id = order_id[:255]
    intent.provider_status = str(observed.get("status") or "")[:40]
    intent.observed_order_json = json.dumps(observed, ensure_ascii=False, default=str)
    intent.status = "verified"
    intent.verified_at = datetime.now(UTC).replace(tzinfo=None)
    transfer.trade_order_id = order_id[:255]
    transfer.status = "invested"
    transfer.failure_reason = ""
    await write_audit(
        db,
        "kraken_trade_uncertainty_recovered",
        entity_type="investment_funding_transfer",
        entity_id=str(transfer.id),
        details={
            "client_order_id": intent.client_order_id,
            "order_id": order_id,
            "provider_status": intent.provider_status,
            "automatic_retry": False,
        },
    )
    await db.commit()
    return {"state": "recovered", "order_id": order_id}
