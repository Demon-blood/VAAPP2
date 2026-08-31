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
    OwnAccountTransfer,
    OwnAccountTransferRecoveryEvidence,
    Task,
)
from app.services.audit import write_audit

_OPEN_TASK_STATUSES = ("open", "waiting")
_UNCERTAIN_STATUS = "creation_uncertain"
_MATCH_WINDOW_DAYS = 4


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value)).copy_abs().quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _iban(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).upper()


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
    for key in ("booking_date", "booking_date_time", "value_date", "value_date_time"):
        raw = str(row.get(key) or "").strip()
        if not raw:
            continue
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
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
    indicator = str(row.get("credit_debit_indicator") or row.get("direction") or "").strip().upper()
    if indicator.startswith("DB") or indicator == "DEBIT":
        return "debit"
    if indicator.startswith("CR") or indicator == "CREDIT":
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


async def _close_legacy_uncertainty_tasks(db: AsyncSession, transfer_id: int) -> int:
    rows = list(
        (
            await db.execute(
                select(Task).where(
                    Task.source_type == "bank_transfer_uncertain",
                    Task.source_id == str(transfer_id),
                    Task.status.in_(_OPEN_TASK_STATUSES),
                )
            )
        ).scalars()
    )
    for row in rows:
        row.status = "completed"
    return len(rows)


async def reconcile_uncertain_own_account_transfer(
    db: AsyncSession,
    transfer: OwnAccountTransfer,
) -> dict[str, Any]:
    """Resolve uncertain transfer creation only from unique booked bank evidence."""

    if transfer.status != _UNCERTAIN_STATUS or transfer.external_payment_id:
        return {"transfer_id": transfer.id, "state": "not_applicable", "matched": 0}

    transfer.requires_user_action = False
    transfer.authorization_url = None
    closed_tasks = await _close_legacy_uncertainty_tasks(db, transfer.id)

    source = await db.get(BankAccount, transfer.source_account_id)
    destination = await db.get(BankAccount, transfer.destination_account_id)
    if (
        source is None
        or destination is None
        or not source.external_account_id
        or not _iban(destination.iban)
    ):
        await db.commit()
        return {
            "transfer_id": transfer.id,
            "state": "system_owned_missing_context",
            "matched": 0,
            "closed_legacy_tasks": closed_tasks,
        }

    date_from = (transfer.created_at - timedelta(days=2)).date().isoformat()
    try:
        payload = await enable_banking.get_account_transactions(
            db,
            source.external_account_id,
            date_from=date_from,
        )
    except (enable_banking.EnableBankingConfigurationError, httpx.RequestError, json.JSONDecodeError) as exc:
        await write_audit(
            db,
            "own_account_transfer_creation_reconciliation_provider_failed",
            entity_type="own_account_transfer",
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

    destination_iban = _iban(destination.iban)
    candidates: list[tuple[str, datetime]] = []
    for row in _transaction_rows(payload if isinstance(payload, dict) else {}):
        if _transaction_direction(row) != "debit":
            continue
        amount, currency = _transaction_amount(row)
        if amount is None or amount != _decimal(transfer.amount):
            continue
        if currency and currency != transfer.currency.upper():
            continue
        if _destination_iban(row) != destination_iban:
            continue
        transaction_date = _transaction_date(row)
        if transaction_date is None:
            continue
        if abs((transaction_date.date() - transfer.created_at.date()).days) > _MATCH_WINDOW_DAYS:
            continue
        transaction_id = _transaction_id(row)
        if not transaction_id:
            continue
        binding = (
            await db.execute(
                select(OwnAccountTransferRecoveryEvidence).where(
                    OwnAccountTransferRecoveryEvidence.bank_account_id == source.id,
                    OwnAccountTransferRecoveryEvidence.transaction_id == transaction_id,
                )
            )
        ).scalar_one_or_none()
        if binding is not None and binding.transfer_id != transfer.id:
            continue
        candidates.append((transaction_id, transaction_date))

    if len(candidates) != 1:
        state = "ambiguous" if len(candidates) > 1 else "waiting_for_evidence"
        await write_audit(
            db,
            "own_account_transfer_creation_reconciliation_waiting",
            entity_type="own_account_transfer",
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

    transaction_id, transaction_date = candidates[0]
    evidence = (
        await db.execute(
            select(OwnAccountTransferRecoveryEvidence).where(
                OwnAccountTransferRecoveryEvidence.transfer_id == transfer.id
            )
        )
    ).scalar_one_or_none()
    if evidence is None:
        evidence = OwnAccountTransferRecoveryEvidence(
            transfer_id=transfer.id,
            bank_account_id=source.id,
            transaction_id=transaction_id,
            match_basis="source_amount_currency_destination_iban_date",
            observed_at=datetime.now(UTC).replace(tzinfo=None),
            details_json=json.dumps(
                {
                    "amount": str(transfer.amount),
                    "currency": transfer.currency,
                    "destination_iban": destination_iban,
                    "booking_date": transaction_date.date().isoformat(),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
        db.add(evidence)

    transfer.status = "completed"
    transfer.requires_user_action = False
    transfer.authorization_url = None
    transfer.failure_reason = ""
    await write_audit(
        db,
        "own_account_transfer_creation_uncertainty_recovered",
        entity_type="own_account_transfer",
        entity_id=str(transfer.id),
        details={
            "source_account_id": source.id,
            "destination_account_id": destination.id,
            "transaction_id": transaction_id,
            "booking_date": transaction_date.date().isoformat(),
            "match_basis": "source_amount_currency_destination_iban_date",
            "completion_evidence": "booked_bank_transaction",
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


async def reconcile_all_uncertain_own_account_transfers(
    db: AsyncSession,
    *,
    limit: int = 50,
) -> dict[str, int]:
    rows = list(
        (
            await db.execute(
                select(OwnAccountTransfer)
                .where(
                    OwnAccountTransfer.status == _UNCERTAIN_STATUS,
                    OwnAccountTransfer.external_payment_id.is_(None),
                )
                .order_by(OwnAccountTransfer.created_at.asc(), OwnAccountTransfer.id.asc())
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
    for transfer in rows:
        outcome = await reconcile_uncertain_own_account_transfer(db, transfer)
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
