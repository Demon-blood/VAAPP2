from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import get_settings
from app.models.entities import (
    AuditLog,
    BankAccount,
    Bill,
    CommunicationAction,
    CommunicationEvent,
    Device,
    DocumentRecord,
    EmailMessage,
    FinancialRecord,
    OAuthConnection,
    OrderRecord,
    OwnAccountTransfer,
    Payment,
    ServiceConnector,
    SubscriptionRecord,
    SupportCase,
    Task,
    WorkflowJob,
)
from app.services.briefing_delivery import issue_briefing_delivery_token, resolve_briefing_window_start
from app.services.runtime_config import get_runtime_value
from app.services.briefing_policy import briefing_period_schedule, filter_needs_you, human_briefing_summary
from app.services.workflow_engine import failure_recovery_class, failure_signature

settings = get_settings()


def _decode(value: str | None, fallback: Any) -> Any:
    try:
        decoded = json.loads(value or "")
    except (json.JSONDecodeError, TypeError):
        return fallback
    return decoded


def _trim(value: Any, limit: int = 280) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


def _money(value: Any, currency: str = "EUR") -> str:
    if value is None or value == "":
        return ""
    try:
        amount = Decimal(str(value))
        return f"{amount:.2f} {currency or 'EUR'}"
    except Exception:
        return f"{value} {currency or 'EUR'}".strip()


def _event_label(event_type: str) -> str:
    labels = {
        "calendar_event_created": "Calendar event created",
        "email_reply_sent": "Reply sent",
        "document_archived": "Document archived",
        "bill_detected": "Bill detected",
        "bill_validated": "Bill validated",
        "payment_initiated": "Payment initiated",
        "payment_authorization_returned": "Bank authorization returned",
        "bank_connected": "Bank connected",
        "order_upserted": "Order updated",
        "subscription_upserted": "Subscription updated",
        "support_case_upserted": "Support case updated",
        "financial_record_created": "Receipt/financial record filed",
        "financial_document_recorded": "Receipt/financial record filed",
        "communication_ingested": "Message/call handled",
        "communication_action_result": "Message reply handled",
        "own_account_transfer_initiated": "Own-account transfer initiated",
        "own_account_transfer_creation_failed": "Own-account transfer failed",
        "own_account_transfer_creation_uncertain": "Own-account transfer needs reconciliation",
    }
    if event_type in labels:
        return labels[event_type]
    return event_type.replace("_", " ").strip().capitalize()


def _analysis_summary(record: EmailMessage) -> tuple[dict[str, Any], str]:
    analysis = _decode(record.analysis_json, {})
    if not isinstance(analysis, dict):
        analysis = {}
    summary = _trim(analysis.get("reasoning_summary") or record.snippet or record.subject, 360)
    return analysis, summary


def _mail_item(record: EmailMessage, audit_events: list[str] | None = None) -> dict[str, Any]:
    analysis, summary = _analysis_summary(record)
    events = list(dict.fromkeys(audit_events or []))
    actions: list[str] = []
    for event in events:
        label = _event_label(event)
        if label not in actions:
            actions.append(label)

    # Reuse the stored decision to explain the outcome without spending a new AI call.
    if analysis.get("bill") and not any("Bill" in value for value in actions):
        actions.append("Bill recorded")
    if analysis.get("calendar_event") and not any("Calendar" in value for value in actions):
        actions.append("Calendar item identified")
    if analysis.get("task") and not any("Task" in value for value in actions):
        actions.append("Task tracked")
    if analysis.get("reply") and not any("Reply" in value for value in actions):
        actions.append("Reply prepared")
    if analysis.get("archive") is True and "Filed" not in actions:
        actions.append("Filed")
    if analysis.get("trash") is True and "Removed as low-value mail" not in actions:
        actions.append("Removed as low-value mail")

    if record.action_required:
        outcome = "Needs attention"
    elif actions:
        outcome = " · ".join(actions[:4])
    else:
        outcome = "Handled automatically"

    return {
        "id": record.id,
        "message_id": record.provider_message_id,
        "received_at": record.received_at,
        "sender": _trim(record.sender, 180),
        "subject": _trim(record.subject or "(No subject)", 220),
        "summary": summary,
        "category": record.category,
        "priority": record.priority,
        "status": record.status,
        "is_read": record.is_read,
        "action_required": record.action_required,
        "outcome": outcome,
        "va_actions": actions[:6],
        "financial_document_type": str(analysis.get("financial_document_type") or "none"),
    }


def _payment_item(payment: Payment, bill: Bill | None, account: BankAccount | None) -> dict[str, Any]:
    purpose_parts: list[str] = []
    if bill is not None:
        if bill.creditor_name:
            purpose_parts.append(bill.creditor_name)
        if bill.invoice_number:
            purpose_parts.append(f"invoice {bill.invoice_number}")
        elif bill.reference:
            purpose_parts.append(f"reference {bill.reference}")
    purpose = " · ".join(purpose_parts) or f"Bill #{payment.bill_id}"
    return {
        "id": payment.id,
        "bill_id": payment.bill_id,
        "purpose": purpose,
        "creditor": bill.creditor_name if bill is not None else "",
        "invoice_number": bill.invoice_number if bill is not None else "",
        "reference": bill.reference if bill is not None else "",
        "amount": payment.amount,
        "currency": payment.currency,
        "amount_text": _money(payment.amount, payment.currency),
        "status": payment.status,
        "requires_user_action": payment.requires_user_action,
        "account": account.name if account is not None else "",
        "account_scope": account.account_scope if account is not None else "",
        "created_at": payment.created_at,
        "updated_at": payment.updated_at,
        "failure_reason": payment.failure_reason,
        "authorization_url": payment.authorization_url,
    }


def _bill_item(bill: Bill) -> dict[str, Any]:
    return {
        "id": bill.id,
        "creditor": bill.creditor_name,
        "amount": bill.amount,
        "currency": bill.currency,
        "amount_text": _money(bill.amount, bill.currency),
        "due_at": bill.due_at,
        "status": bill.status,
        "invoice_number": bill.invoice_number,
        "reference": bill.reference,
        "risk_reason": bill.risk_reason,
        "created_at": bill.created_at,
        "updated_at": bill.updated_at,
    }


def _activity_item(row: AuditLog) -> dict[str, Any]:
    return {
        "id": row.id,
        "event_type": row.event_type,
        "label": _event_label(row.event_type),
        "entity_type": row.entity_type,
        "entity_id": row.entity_id,
        "result": row.result,
        "details": _decode(row.details_json, {}),
        "created_at": row.created_at,
    }


def _task_item(row: Task, *, state: str | None = None) -> dict[str, Any]:
    return {
        "id": row.id,
        "title": row.title,
        "description": _trim(row.description, 420),
        "source_type": row.source_type,
        "source_id": row.source_id,
        "due_at": row.due_at,
        "priority": row.priority,
        "status": state or row.status,
        "requires_approval": row.requires_approval,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _document_item(row: DocumentRecord) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "mime_type": row.mime_type,
        "size_bytes": row.size_bytes,
        "category": row.category,
        "account_scope": row.account_scope,
        "source_type": row.source_type,
        "source_id": row.source_id,
        "drive_web_url": row.drive_web_url,
        "created_at": row.created_at,
    }


def _summary_text(
    stats: dict[str, int],
    payments: list[dict[str, Any]],
    upcoming_bills: list[Bill],
    *,
    window_hours: int = 24,
) -> str:
    email_count = stats["emails_received"]
    handled = stats["emails_handled_automatically"]
    needs = stats["needs_you"]
    parts = [
        f"You received {email_count} email{'s' if email_count != 1 else ''} in the last {window_hours} hours; "
        f"Autopilot handled {handled} without needing you."
    ]

    completed_payments = [item for item in payments if str(item.get("status") or "").lower() == "completed"]
    failed_statuses = {"failed", "rejected", "cancelled"}
    failed_payments = [
        item for item in payments if str(item.get("status") or "").lower() in failed_statuses
    ]
    pending_payments = [
        item
        for item in payments
        if str(item.get("status") or "").lower() not in failed_statuses | {"completed"}
    ]
    if completed_payments:
        names = ", ".join(_trim(item.get("purpose"), 80) for item in completed_payments[:2])
        parts.append(
            f"{len(completed_payments)} payment{'s were' if len(completed_payments) != 1 else ' was'} completed"
            + (f" for {names}." if names else ".")
        )
    if pending_payments:
        parts.append(
            f"{len(pending_payments)} payment action{'s are' if len(pending_payments) != 1 else ' is'} still in progress, awaiting bank authorization, or awaiting bank status."
        )
    if failed_payments:
        names = ", ".join(_trim(item.get("purpose"), 80) for item in failed_payments[:2])
        parts.append(
            f"{len(failed_payments)} payment{'s failed' if len(failed_payments) != 1 else ' failed'}"
            + (f" for {names}." if names else ".")
        )
    if upcoming_bills:
        parts.append(
            f"{len(upcoming_bills)} bill{'s are' if len(upcoming_bills) != 1 else ' is'} due within the next seven days."
        )
    if stats["calendar_changes"]:
        parts.append(
            f"{stats['calendar_changes']} calendar item{'s were' if stats['calendar_changes'] != 1 else ' was'} added or updated from incoming information."
        )
    parts.append(
        "Nothing needs you right now."
        if needs == 0
        else f"{needs} item{'s still need' if needs != 1 else ' still needs'} your attention."
    )
    return " ".join(parts)


async def daily_briefing(db: AsyncSession, *, device: Device | None = None) -> dict[str, Any]:
    """Return an executive briefing while preserving v0.5.x response keys.

    The briefing reuses persisted decisions and audit data; generating it does not make
    a new LLM call. This keeps Groq/Gemini quota available for actual VA decisions and
    makes the briefing available even during an AI-provider outage.
    """

    try:
        tz = ZoneInfo(settings.default_timezone)
    except Exception:
        tz = timezone.utc
    now_utc_aware = datetime.now(timezone.utc)
    local_now = now_utc_aware.astimezone(tz)
    now = now_utc_aware.replace(tzinfo=None)

    try:
        window_hours = max(6, min(int(await get_runtime_value(db, "daily_briefing_window_hours", "24")), 72))
    except ValueError:
        window_hours = 24
    try:
        delivery_hour = max(0, min(int(await get_runtime_value(db, "daily_briefing_hour_local", "19")), 23))
    except ValueError:
        delivery_hour = 19
    enabled = (await get_runtime_value(db, "daily_briefing_enabled", "true")).lower() == "true"

    periods = await briefing_period_schedule(db, local_now)
    since, acknowledged_delivery = await resolve_briefing_window_start(
        db,
        device_id=device.id if device is not None else None,
        now=now,
        fallback_hours=window_hours,
    )
    if device is not None:
        for period in periods:
            if period["enabled"] and period["ready"]:
                period["delivery_token"] = issue_briefing_delivery_token(
                    device,
                    delivery_key=str(period["delivery_key"]),
                    window_start=since,
                    window_end=now,
                )
    upcoming = now + timedelta(days=7)

    mail_rows = list(
        (
            await db.execute(
                select(EmailMessage)
                .where(
                    EmailMessage.received_at.is_not(None),
                    EmailMessage.received_at >= since,
                    EmailMessage.received_at <= now,
                )
                .order_by(EmailMessage.received_at.desc(), EmailMessage.id.desc())
                .limit(100)
            )
        ).scalars()
    )
    open_tasks = list(
        (
            await db.execute(
                select(Task)
                .where(Task.status.in_(["open", "waiting"]))
                .order_by(Task.due_at.asc().nullslast(), Task.id.desc())
                .limit(50)
            )
        ).scalars()
    )
    completed_tasks = list(
        (
            await db.execute(
                select(Task)
                .where(Task.status == "completed", Task.updated_at >= since, Task.updated_at <= now)
                .order_by(Task.updated_at.desc(), Task.id.desc())
                .limit(50)
            )
        ).scalars()
    )
    upcoming_bills = list(
        (
            await db.execute(
                select(Bill)
                .where(
                    Bill.status.not_in(["paid", "cancelled", "reclassified_nonpayable"]),
                    Bill.due_at.is_not(None),
                    Bill.due_at <= upcoming,
                )
                .order_by(Bill.due_at.asc())
                .limit(50)
            )
        ).scalars()
    )
    bill_activity = list(
        (
            await db.execute(
                select(Bill)
                .where(
                    or_(
                        and_(Bill.created_at >= since, Bill.created_at <= now),
                        and_(Bill.updated_at >= since, Bill.updated_at <= now),
                    )
                )
                .order_by(Bill.updated_at.desc(), Bill.id.desc())
                .limit(50)
            )
        ).scalars()
    )

    payment_rows = list(
        (
            await db.execute(
                select(Payment, Bill, BankAccount)
                .outerjoin(Bill, Bill.id == Payment.bill_id)
                .outerjoin(BankAccount, BankAccount.id == Payment.bank_account_id)
                .where(
                    or_(
                        and_(Payment.created_at >= since, Payment.created_at <= now),
                        and_(Payment.updated_at >= since, Payment.updated_at <= now),
                        Payment.requires_user_action.is_(True),
                    )
                )
                .order_by(Payment.updated_at.desc(), Payment.id.desc())
                .limit(50)
            )
        ).all()
    )
    payments = [_payment_item(payment, bill, account) for payment, bill, account in payment_rows]
    payment_actions = [item for item in payments if item["requires_user_action"]]

    support = list(
        (
            await db.execute(
                select(SupportCase)
                .where(SupportCase.status.not_in(["resolved", "closed"]))
                .order_by(SupportCase.updated_at.desc())
                .limit(20)
            )
        ).scalars()
    )
    orders = list(
        (await db.execute(select(OrderRecord).order_by(OrderRecord.updated_at.desc()).limit(15))).scalars()
    )
    subscriptions = list(
        (
            await db.execute(
                select(SubscriptionRecord)
                .where(SubscriptionRecord.status == "active")
                .order_by(SubscriptionRecord.next_charge_at.asc().nullslast())
                .limit(30)
            )
        ).scalars()
    )
    financial_records = list(
        (
            await db.execute(
                select(FinancialRecord)
                .where(FinancialRecord.created_at >= since, FinancialRecord.created_at <= now)
                .order_by(FinancialRecord.created_at.desc())
                .limit(50)
            )
        ).scalars()
    )
    important_documents = list(
        (
            await db.execute(
                select(DocumentRecord)
                .where(DocumentRecord.created_at >= since, DocumentRecord.created_at <= now)
                .order_by(DocumentRecord.created_at.desc(), DocumentRecord.id.desc())
                .limit(50)
            )
        ).scalars()
    )
    connector_errors = list(
        (
            await db.execute(
                select(ServiceConnector)
                .where(ServiceConnector.enabled.is_(True), ServiceConnector.status == "error")
                .order_by(ServiceConnector.updated_at.desc(), ServiceConnector.id.desc())
                .limit(30)
            )
        ).scalars()
    )
    activity = list(
        (
            await db.execute(
                select(AuditLog)
                .where(AuditLog.created_at >= since, AuditLog.created_at <= now)
                .order_by(AuditLog.created_at.desc())
                .limit(200)
            )
        ).scalars()
    )
    dead_letters = list(
        (
            await db.execute(
                select(WorkflowJob)
                .where(WorkflowJob.status == "dead_letter")
                .order_by(WorkflowJob.updated_at.desc())
                .limit(200)
            )
        ).scalars()
    )
    communication_rows = list(
        (
            await db.execute(
                select(CommunicationEvent)
                .where(
                    CommunicationEvent.occurred_at.is_not(None),
                    CommunicationEvent.occurred_at >= since,
                    CommunicationEvent.occurred_at <= now,
                )
                .order_by(CommunicationEvent.occurred_at.desc(), CommunicationEvent.id.desc())
                .limit(100)
            )
        ).scalars()
    )
    communication_reply_count = int(
        (
            await db.execute(
                select(func.count(CommunicationAction.id)).where(
                    CommunicationAction.action_type == "reply",
                    CommunicationAction.status == "completed",
                    CommunicationAction.updated_at >= since,
                    CommunicationAction.updated_at <= now,
                )
            )
        ).scalar_one()
    )
    own_transfer_rows = list(
        (
            await db.execute(
                select(OwnAccountTransfer)
                .where(
                    or_(
                        and_(OwnAccountTransfer.created_at >= since, OwnAccountTransfer.created_at <= now),
                        and_(OwnAccountTransfer.updated_at >= since, OwnAccountTransfer.updated_at <= now),
                        OwnAccountTransfer.requires_user_action.is_(True),
                    )
                )
                .order_by(OwnAccountTransfer.updated_at.desc(), OwnAccountTransfer.id.desc())
                .limit(50)
            )
        ).scalars()
    )

    # Associate recorded email side effects with their originating message so the user
    # sees what the VA actually did, not just what the AI suggested.
    mail_events: dict[str, list[str]] = defaultdict(list)
    for row in activity:
        if row.entity_type == "email" and row.entity_id:
            mail_events[row.entity_id].append(row.event_type)
    mail = [_mail_item(row, mail_events.get(row.provider_message_id)) for row in mail_rows]

    appointments: list[dict[str, Any]] = []
    calendar_error = ""
    google_configured = int(
        (
            await db.execute(
                select(func.count(OAuthConnection.id)).where(
                    OAuthConnection.provider == "google", OAuthConnection.enabled.is_(True)
                )
            )
        ).scalar_one()
    ) > 0
    if google_configured:
        try:
            from app.integrations.google_api import list_upcoming_calendar_events

            appointments = await list_upcoming_calendar_events(db, days=7, max_results=30)
        except Exception as exc:
            calendar_error = str(exc)[:1000]

    mail_by_provider_id = {row.provider_message_id: row for row in mail_rows}

    reply_activity: list[dict[str, Any]] = []
    for row in activity:
        if row.event_type != "email_reply_sent" or row.entity_type != "email":
            continue
        source = mail_by_provider_id.get(row.entity_id)
        reply_activity.append(
            {
                "status": "sent",
                "created_at": row.created_at,
                "source_message_id": row.entity_id,
                "subject": source.subject if source is not None else "Reply sent",
                "recipient": source.sender if source is not None else "",
                "detail": "Sent automatically by the VA",
            }
        )
    for task in open_tasks:
        if task.source_type != "email_reply" or not task.requires_approval:
            continue
        source = mail_by_provider_id.get(str(task.source_id or ""))
        reply_activity.append(
            {
                "status": "awaiting_decision",
                "created_at": task.created_at,
                "source_message_id": task.source_id,
                "subject": source.subject if source is not None else task.title,
                "recipient": source.sender if source is not None else "",
                "detail": _trim(task.description, 420),
                "task_id": task.id,
            }
        )

    task_activity = {
        "completed": [_task_item(row, state="completed") for row in completed_tasks],
        "upcoming": [
            _task_item(row)
            for row in open_tasks
            if row.due_at is not None and row.due_at <= upcoming
        ],
    }

    sensitive_terms = (
        "security", "beveilig", "legal", "jurid", "government", "overheid",
        "finance", "geld", "bank", "medical", "medisch", "health", "gezondheid",
        "court", "rechtbank", "bailiff", "deurwaard", "tax", "belasting",
    )
    unusual_items = [
        item
        for item in mail
        if item["priority"] in {"high", "urgent"}
        or item.get("financial_document_type") not in {None, "", "none"}
        or any(term in str(item.get("category") or "").lower() for term in sensitive_terms)
    ][:40]

    calendar_changes: list[dict[str, Any]] = []
    for row in activity:
        if row.event_type != "calendar_event_created":
            continue
        source = mail_by_provider_id.get(row.entity_id)
        calendar_changes.append(
            {
                "created_at": row.created_at,
                "source_message_id": row.entity_id,
                "subject": source.subject if source is not None else "Calendar event",
                "detail": "Added to Google Calendar from incoming information",
                "details": _decode(row.details_json, {}),
            }
        )

    needs_you: list[dict[str, Any]] = []
    for item in payment_actions:
        uncertain = str(item.get("status") or "").lower() == "creation_uncertain" or not item.get("authorization_url")
        needs_you.append(
            {
                "type": "payment_reconciliation" if uncertain else "payment_authorization",
                "id": item["id"],
                "title": (
                    f"Check bank before retrying: {item['purpose']}"
                    if uncertain
                    else f"Authorize payment: {item['purpose']}"
                ),
                "detail": item.get("failure_reason") or item["amount_text"],
                "authorization_url": item["authorization_url"],
            }
        )
    for transfer in own_transfer_rows:
        if transfer.requires_user_action:
            uncertain = transfer.status == "creation_uncertain" or not transfer.authorization_url
            needs_you.append(
                {
                    "type": "transfer_reconciliation" if uncertain else "transfer_authorization",
                    "id": transfer.id,
                    "title": "Check bank before retrying transfer" if uncertain else "Authorize own-account transfer",
                    "detail": transfer.failure_reason or _money(transfer.amount, transfer.currency),
                    "authorization_url": transfer.authorization_url,
                }
            )
    for task in open_tasks:
        if task.requires_approval:
            needs_you.append(
                {"type": "task_approval", "id": task.id, "title": task.title, "detail": task.description}
            )

    for task in open_tasks:
        if task.source_type == "bill_payment" and not task.requires_approval:
            needs_you.append(
                {
                    "type": "funding_required",
                    "id": task.id,
                    "title": task.title,
                    "detail": task.description,
                }
            )

    for event in communication_rows:
        decision = _decode(event.decision_json, {})
        if event.action_required and decision.get("interrupt") is True:
            sender = _trim(event.sender or "message", 120)
            body = _trim(event.body, 360)
            needs_you.append(
                {
                    "type": "urgent_communication",
                    "id": event.id,
                    "title": f"Important message from {sender}",
                    "detail": body or "A genuinely urgent communication needs your judgment.",
                    "interrupt": True,
                }
            )

    grouped_failures: dict[str, dict[str, Any]] = {}
    for job in dead_letters:
        signature = failure_signature(job.job_type, job.last_error)
        group = grouped_failures.setdefault(
            signature,
            {
                "job": job,
                "occurrences": 0,
                "classification": failure_recovery_class(job.job_type, job.last_error),
                "automatic_recoveries": 0,
            },
        )
        group["occurrences"] += 1

    for group in grouped_failures.values():
        job = group["job"]
        group["automatic_recoveries"] = int(
            (
                await db.execute(
                    select(func.count(AuditLog.id)).where(
                        AuditLog.event_type == "workflow_job_auto_recovered",
                        AuditLog.entity_type == "workflow_job",
                        AuditLog.entity_id == str(job.id),
                    )
                )
            ).scalar_one()
        )
        classification = str(group["classification"])
        recoveries = int(group["automatic_recoveries"])
        # Transient failures stay off Needs you while the self-healing budget is still available.
        if classification == "transient" and recoveries < 2:
            continue
        occurrences = int(group["occurrences"])
        detail = job.last_error
        if occurrences > 1:
            detail = f"{detail}\nRepeated {occurrences} times; Autopilot grouped these into one exception."
        if classification == "user_required":
            needs_you.append(
                {
                    "type": "autopilot_exception",
                    "id": job.id,
                    "title": f"Authorization/configuration required: {job.job_type}",
                    "detail": detail,
                    "occurrences": occurrences,
                    "classification": classification,
                    "automatic_recoveries": recoveries,
                }
            )

    provider_health: dict[str, Any] = {}
    provider_problems: list[dict[str, Any]] = []
    for group in grouped_failures.values():
        job = group["job"]
        classification = str(group["classification"])
        recoveries = int(group["automatic_recoveries"])
        if classification == "transient" and recoveries < 2:
            continue
        provider_problems.append(
            {
                "type": "autopilot_dead_letter",
                "provider": job.job_type,
                "status": "failed",
                "detail": _trim(job.last_error, 600),
                "occurrences": int(group["occurrences"]),
                "classification": classification,
                "automatic_recoveries": recoveries,
                "updated_at": job.updated_at,
            }
        )
    try:
        from app.services.autopilot_service import provider_health_snapshot

        provider_health = await provider_health_snapshot(db)
        core_providers = {"google", "banking"}
        for provider, status in (provider_health.get("providers") or {}).items():
            provider_status = str(status.get("status") or "")
            if provider_status == "not_configured" and provider in core_providers:
                detail = f"{provider} needs provider setup or authorization before Autopilot can use it."
                provider_problems.append(
                    {
                        "type": "provider_setup_required",
                        "provider": provider,
                        "status": provider_status,
                        "detail": detail,
                    }
                )
                needs_you.append(
                    {
                        "type": "provider_authorization",
                        "id": provider,
                        "title": f"Connect {provider.replace('_', ' ')}",
                        "detail": detail,
                    }
                )
                continue
            if provider_status != "degraded":
                continue
            provider_problems.append(
                {
                    "type": "provider_degraded",
                    "provider": provider,
                    "status": provider_status,
                    "detail": _trim(status.get("last_error") or f"{provider} is degraded", 600),
                    "last_job_status": status.get("last_job_status"),
                    "last_job_at": status.get("last_job_at"),
                }
            )
    except Exception as exc:
        provider_problems.append(
            {
                "type": "provider_health_check_failed",
                "provider": "autopilot",
                "status": "degraded",
                "detail": _trim(exc, 600),
            }
        )

    for connector in connector_errors:
        provider_problems.append(
            {
                "type": "connector_error",
                "provider": connector.display_name or connector.slug,
                "status": connector.status,
                "detail": _trim(connector.last_error, 600),
                "updated_at": connector.updated_at,
            }
        )
    if calendar_error:
        provider_problems.append(
            {
                "type": "calendar_error",
                "provider": "google_calendar",
                "status": "degraded",
                "detail": _trim(calendar_error, 600),
            }
        )

    seen_provider_problems: set[tuple[str, str, str]] = set()
    unique_provider_problems: list[dict[str, Any]] = []
    for item in provider_problems:
        signature = (str(item.get("type")), str(item.get("provider")), str(item.get("detail")))
        if signature in seen_provider_problems:
            continue
        seen_provider_problems.add(signature)
        unique_provider_problems.append(item)
    provider_problems = unique_provider_problems
    needs_you = filter_needs_you(needs_you)

    plan: list[dict[str, Any]] = []
    for item in needs_you[:10]:
        plan.append(
            {"kind": item["type"], "title": item["title"], "owner": "user", "reason": "Human action is required"}
        )
    for task in open_tasks:
        if not task.requires_approval and task.due_at is not None and task.due_at <= upcoming:
            plan.append(
                {"kind": "task", "title": task.title, "owner": "autopilot", "due_at": task.due_at, "reason": "Upcoming task"}
            )
    for bill in upcoming_bills:
        if bill.status == "validated":
            plan.append(
                {
                    "kind": "bill",
                    "title": f"Handle {bill.creditor_name} bill",
                    "owner": "autopilot",
                    "due_at": bill.due_at,
                    "reason": "Validated bill within seven days",
                }
            )

    meaningful_activity = [
        row
        for row in activity
        if not row.event_type.startswith("workflow_job_")
        and row.event_type
        not in {
            "workflow_started",
            "workflow_dead_letters_compacted",
            "workflow_exceptions_requeued",
        }
    ]
    activity_counts = Counter(_event_label(row.event_type) for row in meaningful_activity if row.result != "failed")
    activity_summary = [
        {"label": label, "count": count}
        for label, count in activity_counts.most_common(12)
    ]

    stats = {
        "emails_received": len(mail),
        "emails_handled_automatically": sum(1 for item in mail if not item["action_required"]),
        "emails_needing_attention": sum(1 for item in mail if item["action_required"]),
        "bills_changed": len(bill_activity),
        "bills_due_7d": len(upcoming_bills),
        "payments_changed": len(payments),
        "payments_completed": sum(1 for item in payments if str(item["status"]).lower() == "completed"),
        "receipts_and_notices": len(financial_records),
        "calendar_changes": len(calendar_changes),
        "appointments_upcoming": len(appointments),
        "tasks_completed": len(completed_tasks),
        "tasks_upcoming": len(task_activity["upcoming"]),
        "replies_sent": sum(1 for item in reply_activity if item["status"] == "sent"),
        "replies_awaiting_decision": sum(1 for item in reply_activity if item["status"] == "awaiting_decision"),
        "messages_received": sum(1 for item in communication_rows if item.channel != "call" and item.direction == "incoming"),
        "calls_received": sum(1 for item in communication_rows if item.channel == "call" and item.direction == "incoming"),
        "communication_replies_sent": communication_reply_count,
        "communication_needing_attention": sum(1 for item in communication_rows if item.action_required),
        "own_account_transfers_changed": len(own_transfer_rows),
        "important_documents": len(important_documents),
        "unusual_items": len(unusual_items),
        "provider_problems": len(provider_problems),
        "va_actions": len(meaningful_activity),
        "needs_you": len(needs_you),
    }

    from app.services.commitment_graph import executive_commitment_overview

    commitments = await executive_commitment_overview(db)
    ready_periods = [item for item in periods if item["enabled"] and item["ready"]]
    briefing_period = ready_periods[-1]["name"] if ready_periods else "daily"
    summary_text = human_briefing_summary(
        stats, payments, upcoming_bills, needs_you, period=briefing_period, commitments=commitments
    )
    headline = (
        "Everything is under control."
        if not needs_you
        else f"{len(needs_you)} item{'s genuinely need' if len(needs_you) != 1 else ' genuinely needs'} your authority or input."
    )
    notification_body = summary_text

    important_mail = [
        item
        for item in mail
        if item["action_required"] or item["priority"] in {"high", "urgent"}
    ][:30]

    return {
        "generated_at": now.isoformat() + "Z",
        "briefing_date": local_now.date().isoformat(),
        "window_hours": window_hours,
        "window_start": since.isoformat() + "Z",
        "window_end": now.isoformat() + "Z",
        "window_source": "acknowledged_delivery" if acknowledged_delivery is not None else "fallback",
        "last_acknowledged_delivery_key": (
            acknowledged_delivery.delivery_key if acknowledged_delivery is not None else None
        ),
        "timezone": getattr(tz, "key", str(tz)),
        "headline": headline,
        "summary_text": summary_text,
        "commitments": commitments,
        "stats": stats,
        "mail": mail,
        "mail_category_counts": dict(Counter(item["category"] for item in mail)),
        "payment_activity": payments,
        "communications": [
            {
                "id": row.id,
                "channel": row.channel,
                "provider": row.provider,
                "sender": _trim(row.sender, 180),
                "body": _trim(row.body, 360),
                "direction": row.direction,
                "event_type": row.event_type,
                "occurred_at": row.occurred_at,
                "category": row.category,
                "priority": row.priority,
                "action_required": row.action_required,
                "protected": row.protected,
                "interrupt": bool(_decode(row.decision_json, {}).get("interrupt")),
                "status": row.status,
            }
            for row in communication_rows
        ],
        "internal_transfers": [
            {
                "id": row.id,
                "source_account_id": row.source_account_id,
                "destination_account_id": row.destination_account_id,
                "amount": row.amount,
                "currency": row.currency,
                "amount_text": _money(row.amount, row.currency),
                "reason": _trim(row.reason, 300),
                "status": row.status,
                "requires_user_action": row.requires_user_action,
                "authorization_url": row.authorization_url,
                "failure_reason": _trim(row.failure_reason, 500),
                "created_at": row.created_at,
                "updated_at": row.updated_at,
            }
            for row in own_transfer_rows
        ],
        "bill_activity": [_bill_item(row) for row in bill_activity],
        "reply_activity": reply_activity,
        "task_activity": task_activity,
        "calendar_changes": calendar_changes,
        "important_documents": [_document_item(row) for row in important_documents],
        "unusual_items": unusual_items,
        "provider_health": provider_health,
        "provider_problems": provider_problems,
        "activity_summary": activity_summary,
        "notification": {
            "enabled": enabled,
            "delivery_hour_local": delivery_hour,
            "ready": enabled and any(item["ready"] for item in periods),
            "periods": periods,
            "title": "Your Full-Time VA briefing",
            "body": notification_body,
        },
        # v0.5.x compatibility keys follow. Older Android clients can consume this
        # richer endpoint without requiring a simultaneous app upgrade.
        "plan": plan,
        "important_mail": important_mail,
        "tasks": [_task_item(row) for row in open_tasks],
        "upcoming_bills": [_bill_item(row) for row in upcoming_bills],
        "appointments": appointments,
        "calendar_error": calendar_error,
        "support_cases": [
            {"id": row.id, "subject": row.subject, "priority": row.priority, "status": row.status}
            for row in support
        ],
        "orders": [
            {
                "id": row.id,
                "merchant": row.merchant,
                "order_number": row.order_number,
                "status": row.status,
                "expected_delivery_at": row.expected_delivery_at,
            }
            for row in orders
        ],
        "subscriptions": [
            {
                "id": row.id,
                "provider": row.provider_name,
                "description": row.description,
                "amount": row.amount,
                "currency": row.currency,
                "next_charge_at": row.next_charge_at,
            }
            for row in subscriptions
        ],
        "financial_records": [
            {
                "id": row.id,
                "type": row.record_type,
                "provider": row.provider_name,
                "description": row.description,
                "order_number": row.order_number,
                "amount": row.amount,
                "currency": row.currency,
                "status": row.status,
                "occurred_at": row.occurred_at,
            }
            for row in financial_records
        ],
        "activity": [_activity_item(row) for row in meaningful_activity[:80]],
        "needs_you": needs_you,
    }
