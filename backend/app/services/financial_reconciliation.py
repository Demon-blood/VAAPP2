from __future__ import annotations

import json
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations import enable_banking
from app.models.entities import (
    BankAccount,
    Bill,
    EmailMessage,
    FinancialRecord,
    Payment,
    Task,
)
from app.schemas.api import AutomationDecision
from app.services.audit import write_audit
from app.services.financial_document_policy import (
    PAID_RECEIPT,
    STATEMENT_OR_NOTICE,
    FinancialDocumentAssessment,
    assess_financial_document,
    infer_recurring_subscription,
)

_NONPAYABLE_BILL_STATUS = "reclassified_nonpayable"
_OPEN_TASK_STATUSES = ("open", "waiting")
_PAYMENT_TERMINAL_FAILURES = ("failed", "cancelled", "rejected")


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value)).copy_abs().quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _decision_has_other_action(decision: AutomationDecision) -> bool:
    return any(
        value is not None
        for value in (
            decision.task,
            decision.calendar_event,
            decision.reply,
            decision.support_case,
        )
    )


def _analysis_bill(record: EmailMessage | None) -> dict[str, Any] | None:
    if record is None:
        return None
    try:
        decision = AutomationDecision.model_validate_json(record.analysis_json or "{}")
        return decision.bill
    except Exception:
        return None


async def upsert_financial_record(
    db: AsyncSession,
    *,
    message_id: str,
    assessment: FinancialDocumentAssessment,
    description: str,
    amount: Decimal | None,
    currency: str = "EUR",
    occurred_at: datetime | None = None,
    account_scope: str = "personal",
    order_number: str = "",
    subscription_id: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> FinancialRecord | None:
    if assessment.document_type not in {PAID_RECEIPT, STATEMENT_OR_NOTICE}:
        return None

    row = (
        await db.execute(
            select(FinancialRecord).where(FinancialRecord.source_message_id == message_id)
        )
    ).scalar_one_or_none()
    if row is None:
        row = FinancialRecord(source_message_id=message_id, record_type=assessment.document_type)
        db.add(row)

    row.record_type = assessment.document_type
    row.provider_name = assessment.provider_name
    row.description = description.strip()
    row.order_number = order_number or assessment.order_number
    row.amount = amount
    row.currency = (currency or "EUR").upper()[:3]
    row.occurred_at = occurred_at
    row.status = "paid" if assessment.document_type == PAID_RECEIPT else "informational"
    row.account_scope = account_scope or "personal"
    if subscription_id is not None:
        row.subscription_id = subscription_id
    row.metadata_json = json.dumps(
        {
            **(metadata or {}),
            "confidence": assessment.confidence,
            "reasons": list(assessment.reasons),
            "recurring": assessment.recurring,
        },
        ensure_ascii=False,
        default=str,
    )
    await db.flush()
    await write_audit(
        db,
        "financial_document_recorded",
        entity_type="financial_record",
        entity_id=str(row.id),
        details={
            "record_type": row.record_type,
            "provider": row.provider_name,
            "order_number": row.order_number,
            "amount": str(row.amount) if row.amount is not None else None,
            "currency": row.currency,
            "subscription_id": row.subscription_id,
        },
    )
    return row


async def _close_bill_exception_tasks(
    db: AsyncSession,
    *,
    bill: Bill,
    message_id: str | None,
    close_generic_email_action: bool = False,
) -> int:
    ids = [str(bill.id)]
    if message_id:
        ids.append(message_id)
    source_types = ["bill_review", "creditor_review", "bill_payment"]
    if close_generic_email_action:
        source_types.append("email_action")
    rows = list(
        (
            await db.execute(
                select(Task).where(
                    Task.source_type.in_(source_types),
                    Task.source_id.in_(ids),
                    Task.status.in_(_OPEN_TASK_STATUSES),
                )
            )
        ).scalars()
    )
    for task in rows:
        task.status = "completed"
    return len(rows)


async def reclassify_existing_nonpayable_bills(
    db: AsyncSession,
    *,
    limit: int = 500,
) -> dict[str, int]:
    """Repair historical false-positive bills without deleting their audit trail.

    Automatic reclassification is intentionally conservative. Bills with a non-terminal
    payment already attached are never hidden automatically; those require explicit review.
    """

    bills = list(
        (
            await db.execute(
                select(Bill)
                .where(
                    Bill.status.not_in(["paid", "cancelled", _NONPAYABLE_BILL_STATUS]),
                )
                .order_by(Bill.id.desc())
                .limit(max(1, min(limit, 5000)))
            )
        ).scalars()
    )
    result = {"reclassified": 0, "skipped_active_payment": 0, "reviewed": len(bills), "tasks_closed": 0}

    for bill in bills:
        message = None
        if bill.source_message_id:
            message = (
                await db.execute(
                    select(EmailMessage).where(
                        EmailMessage.provider_message_id == bill.source_message_id
                    )
                )
            ).scalar_one_or_none()

        analysis_bill = _analysis_bill(message) or {}
        synthetic_body = "\n".join(
            value
            for value in (
                message.snippet if message else "",
                f"Invoice: {bill.invoice_number}" if bill.invoice_number else "",
                f"Reference: {bill.reference}" if bill.reference else "",
                f"IBAN: {bill.iban}" if bill.iban else "",
            )
            if value
        )
        extraction = {
            "amount_candidates": [str(bill.amount)],
            "invoice_number": bill.invoice_number,
            "reference": bill.reference,
            "iban_candidates": [bill.iban] if bill.iban else [],
            "due_date_candidates": [bill.due_at.isoformat()] if bill.due_at else [],
            "cues": ["invoice"] if bill.invoice_number else [],
        }
        assessment = assess_financial_document(
            sender=(message.sender if message else bill.creditor_name),
            subject=(message.subject if message else bill.creditor_name),
            body=synthetic_body,
            extraction=extraction,
            bill={
                **analysis_bill,
                "amount": str(bill.amount),
                "iban": bill.iban,
                "due_at": bill.due_at.isoformat() if bill.due_at else None,
            },
        )
        if not assessment.is_nonpayable:
            continue

        # Historical cleanup requires stronger evidence than live suppression. The
        # Google GPA path is 0.995; common receipts are >=0.90.
        if assessment.confidence < 0.90:
            continue

        active_payment = (
            await db.execute(
                select(Payment.id)
                .where(
                    Payment.bill_id == bill.id,
                    Payment.status.not_in(_PAYMENT_TERMINAL_FAILURES),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if active_payment is not None:
            result["skipped_active_payment"] += 1
            existing_review = (
                await db.execute(
                    select(Task).where(
                        Task.source_type == "financial_reclassification_review",
                        Task.source_id == str(bill.id),
                        Task.status.in_(_OPEN_TASK_STATUSES),
                    )
                )
            ).scalar_one_or_none()
            if existing_review is None:
                db.add(
                    Task(
                        title=f"Review payment for possible receipt: {bill.creditor_name}",
                        description=(
                            "This record looks non-payable, but a non-terminal payment already exists. "
                            "The VA will not hide or alter the payment automatically."
                        ),
                        source_type="financial_reclassification_review",
                        source_id=str(bill.id),
                        priority="urgent",
                        requires_approval=True,
                    )
                )
            await write_audit(
                db,
                "bill_reclassification_blocked_active_payment",
                entity_type="bill",
                entity_id=str(bill.id),
                result="blocked",
                details={
                    "payment_id": active_payment,
                    "candidate_type": assessment.document_type,
                    "confidence": assessment.confidence,
                },
            )
            continue

        old_status = bill.status
        bill.status = _NONPAYABLE_BILL_STATUS
        bill.risk_reason = (
            "Reclassified as a paid purchase/receipt; no payment is outstanding."
            if assessment.document_type == PAID_RECEIPT
            else "Reclassified as an informational financial notice; no payable evidence was found."
        )

        subscription_id = None
        close_generic_email_action = False
        if message is not None:
            try:
                decision = AutomationDecision.model_validate_json(message.analysis_json or "{}")
            except Exception:
                decision = None
            subscription_data = None
            if decision is not None:
                decision.bill = None
                decision.financial_document_type = assessment.document_type
                category_lower = decision.category.lower()
                routine_finance_context = (
                    "finance" in category_lower
                    or "geldzaken" in category_lower
                    or category_lower in {"ai review required", "unclassified"}
                )
                if routine_finance_context and not _decision_has_other_action(decision):
                    decision.action_required = False
                if not decision.action_required:
                    decision.labels = [
                        label for label in decision.labels if label != "Mail/00 Status/Actie nodig"
                    ]
                if decision.subscription:
                    subscription_data = decision.subscription
                message.analysis_json = decision.model_dump_json()
                if not decision.action_required:
                    message.action_required = False
                    close_generic_email_action = True
            if subscription_data is None:
                subscription_data = infer_recurring_subscription(
                    subject=message.subject,
                    body=synthetic_body,
                    assessment=assessment,
                    amount=str(bill.amount),
                    currency=bill.currency,
                    account_scope=bill.account_scope,
                )
            if subscription_data is not None:
                from app.services.operations_service import upsert_subscription

                subscription = await upsert_subscription(
                    db, message_id=message.provider_message_id, data=subscription_data
                )
                if subscription is not None:
                    subscription_id = subscription.id

        await upsert_financial_record(
            db,
            message_id=bill.source_message_id or f"legacy-bill:{bill.id}",
            assessment=assessment,
            description=(message.subject if message else bill.creditor_name),
            amount=bill.amount,
            currency=bill.currency,
            occurred_at=(message.received_at if message else bill.created_at),
            account_scope=bill.account_scope,
            order_number=assessment.order_number or bill.invoice_number,
            subscription_id=subscription_id,
            metadata={"reclassified_from_bill_id": bill.id, "previous_bill_status": old_status},
        )
        closed = await _close_bill_exception_tasks(
            db,
            bill=bill,
            message_id=bill.source_message_id,
            close_generic_email_action=close_generic_email_action,
        )
        result["tasks_closed"] += closed
        result["reclassified"] += 1
        await write_audit(
            db,
            "bill_reclassified_nonpayable",
            entity_type="bill",
            entity_id=str(bill.id),
            details={
                "old_status": old_status,
                "new_status": bill.status,
                "financial_document_type": assessment.document_type,
                "confidence": assessment.confidence,
                "reasons": list(assessment.reasons),
                "tasks_closed": closed,
            },
        )

    await db.commit()
    return result


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
        if isinstance(value, dict):
            raw_amount = value.get("amount") or value.get("value")
            currency = str(value.get("currency") or value.get("currency_code") or currency)
        else:
            raw_amount = value
        parsed = _decimal(raw_amount)
        if parsed is not None:
            return parsed, currency.upper()[:3]
    return None, currency.upper()[:3]


def _transaction_date(row: dict[str, Any]) -> datetime | None:
    for key in ("booking_date", "value_date", "transaction_date", "date"):
        value = row.get(key)
        if not value:
            continue
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            try:
                return datetime.fromisoformat(str(value)[:10])
            except ValueError:
                continue
    return None


def _transaction_text(row: dict[str, Any]) -> str:
    values: list[str] = []
    for key in (
        "creditor_name",
        "debtor_name",
        "remittance_information",
        "remittance_information_unstructured",
        "reference",
        "merchant_name",
        "description",
        "additional_information",
    ):
        value = row.get(key)
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, list):
            values.extend(str(item) for item in value)
        elif isinstance(value, dict):
            values.extend(str(item) for item in value.values())
    return " ".join(values).lower()


def _transaction_id(row: dict[str, Any]) -> str:
    for key in ("transaction_id", "entry_reference", "id", "bank_transaction_code"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def _provider_tokens(provider: str) -> set[str]:
    ignored = {"limited", "ltd", "inc", "llc", "commerce", "store", "payments"}
    return {
        token
        for token in provider.lower().replace(".", " ").split()
        if len(token) >= 4 and token not in ignored
    }


async def reconcile_receipts_with_bank_transactions(
    db: AsyncSession,
    *,
    days: int = 45,
    limit: int = 100,
) -> dict[str, int]:
    """Match receipts to real bank transactions without changing payment state.

    Matching is evidence-only and has no side effect on bank accounts or payments. A
    receipt is reconciled only when exactly one transaction matches amount, currency,
    date proximity and provider/order evidence.
    """

    since = datetime.utcnow() - timedelta(days=max(1, min(days, 120)))
    receipts = list(
        (
            await db.execute(
                select(FinancialRecord)
                .where(
                    FinancialRecord.record_type == PAID_RECEIPT,
                    FinancialRecord.amount.is_not(None),
                    FinancialRecord.matched_transaction_id == "",
                    or_(FinancialRecord.occurred_at.is_(None), FinancialRecord.occurred_at >= since),
                )
                .order_by(FinancialRecord.occurred_at.desc().nullslast(), FinancialRecord.id.desc())
                .limit(max(1, min(limit, 500)))
            )
        ).scalars()
    )
    if not receipts:
        return {"reviewed": 0, "matched": 0, "ambiguous": 0, "accounts_checked": 0}

    accounts = list(
        (
            await db.execute(
                select(BankAccount).where(BankAccount.external_account_id != "")
            )
        ).scalars()
    )
    transaction_pool: list[tuple[BankAccount, dict[str, Any]]] = []
    date_from = since.date().isoformat()
    for account in accounts:
        try:
            payload = await enable_banking.get_account_transactions(
                db, account.external_account_id, date_from=date_from
            )
        except Exception as exc:
            await write_audit(
                db,
                "receipt_bank_reconciliation_account_failed",
                entity_type="bank_account",
                entity_id=str(account.id),
                result="failed",
                details={"error": str(exc)},
            )
            continue
        for row in _transaction_rows(payload if isinstance(payload, dict) else {}):
            transaction_pool.append((account, row))

    result = {
        "reviewed": len(receipts),
        "matched": 0,
        "ambiguous": 0,
        "accounts_checked": len(accounts),
    }
    for receipt in receipts:
        tokens = _provider_tokens(receipt.provider_name)
        occurred = receipt.occurred_at
        candidates: list[tuple[BankAccount, dict[str, Any]]] = []
        for account, row in transaction_pool:
            amount, currency = _transaction_amount(row)
            if amount is None or amount != _decimal(receipt.amount):
                continue
            if currency and currency != receipt.currency.upper():
                continue
            tx_date = _transaction_date(row)
            if occurred and tx_date and abs((tx_date.date() - occurred.date()).days) > 4:
                continue
            text = _transaction_text(row)
            provider_match = not tokens or any(token in text for token in tokens)
            order_match = bool(receipt.order_number and receipt.order_number.lower() in text)
            if not provider_match and not order_match:
                continue
            candidates.append((account, row))

        if len(candidates) != 1:
            if len(candidates) > 1:
                result["ambiguous"] += 1
            continue

        account, row = candidates[0]
        transaction_id = _transaction_id(row)
        if not transaction_id:
            continue
        receipt.matched_bank_account_id = account.id
        receipt.matched_transaction_id = transaction_id
        receipt.matched_at = datetime.utcnow()
        receipt.status = "reconciled"
        result["matched"] += 1
        await write_audit(
            db,
            "receipt_reconciled_with_bank_transaction",
            entity_type="financial_record",
            entity_id=str(receipt.id),
            details={
                "bank_account_id": account.id,
                "transaction_id": transaction_id,
                "amount": str(receipt.amount),
                "currency": receipt.currency,
            },
        )

    await db.commit()
    return result
