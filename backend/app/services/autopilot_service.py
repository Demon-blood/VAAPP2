from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import (
    AIUsageDaily,
    AuditLog,
    BankAccount,
    Bill,
    Creditor,
    EmailMessage,
    FinancialRecord,
    OAuthConnection,
    OperationPreference,
    OrderRecord,
    Payment,
    SenderRule,
    ServiceConnector,
    SubscriptionRecord,
    SupportCase,
    Task,
    WorkflowJob,
)
from app.services.workflow_engine import create_workflow, enqueue_job, failure_signature


def _decode(value: str, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except (json.JSONDecodeError, TypeError):
        return fallback


async def provider_health_snapshot(db: AsyncSession) -> dict[str, Any]:
    now = datetime.utcnow()
    recent = now - timedelta(hours=6)
    providers: dict[str, dict[str, Any]] = {}

    google_connections = int(
        (await db.execute(select(func.count(OAuthConnection.id)).where(OAuthConnection.provider == "google", OAuthConnection.enabled.is_(True)))).scalar_one()
    )
    providers["google"] = {"configured": google_connections > 0, "status": "healthy" if google_connections else "not_configured"}

    from app.services.runtime_config import get_runtime_value

    primary_key = await get_runtime_value(db, "ai_api_key")
    primary_model = await get_runtime_value(db, "ai_model")
    primary_base = await get_runtime_value(db, "ai_base_url", "https://api.groq.com/openai/v1")
    fallback_key = await get_runtime_value(db, "ai_fallback_api_key")
    fallback_model = await get_runtime_value(db, "ai_fallback_model")
    fallback_base = await get_runtime_value(db, "ai_fallback_base_url")
    usage = await db.get(AIUsageDaily, now.date().isoformat())
    rate_limits = usage.rate_limit_count if usage is not None else 0
    providers["ai_primary"] = {
        "configured": bool(primary_key and primary_model),
        "status": "degraded" if rate_limits >= 5 else ("healthy" if primary_key and primary_model else "not_configured"),
        "base_url": primary_base,
        "model": primary_model,
        "rate_limits_today": rate_limits,
    }
    providers["ai_fallback"] = {
        "configured": bool(fallback_key and fallback_model and fallback_base),
        "status": "healthy" if fallback_key and fallback_model and fallback_base else "not_configured",
        "base_url": fallback_base,
        "model": fallback_model,
        "sensitive_mail_allowed": False,
    }

    bank_accounts = int((await db.execute(select(func.count(BankAccount.id)))).scalar_one())
    providers["banking"] = {"configured": bank_accounts > 0, "status": "healthy" if bank_accounts else "not_configured"}

    enabled_connectors = int(
        (await db.execute(select(func.count(ServiceConnector.id)).where(ServiceConnector.enabled.is_(True)))).scalar_one()
    )
    connector_errors = int(
        (
            await db.execute(
                select(func.count(ServiceConnector.id)).where(
                    ServiceConnector.enabled.is_(True), ServiceConnector.status == "error"
                )
            )
        ).scalar_one()
    )
    providers["connectors"] = {
        "configured": enabled_connectors > 0,
        "status": "degraded" if connector_errors else ("healthy" if enabled_connectors else "not_configured"),
        "error_count": connector_errors,
    }

    mapping = {
        "gmail": "gmail.sync",
        "banking": "banking.autopilot",
        "contacts": "google.contacts.sync",
        "connectors": "connectors.rules.run",
        "housekeeping": "housekeeping.documents",
    }
    for provider, job_type in mapping.items():
        latest = (
            await db.execute(
                select(WorkflowJob)
                .where(WorkflowJob.job_type == job_type)
                .order_by(WorkflowJob.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        entry = providers.setdefault(provider, {"configured": True, "status": "unknown"})
        if latest is not None:
            entry.update(
                {
                    "last_job_status": latest.status,
                    "last_job_at": latest.created_at,
                    "last_error": latest.last_error,
                }
            )
            if latest.status == "dead_letter":
                entry["status"] = "degraded"
            elif latest.status in {"completed", "running", "pending", "retry"} and latest.created_at >= recent:
                if entry.get("status") not in {"not_configured", "degraded"}:
                    entry["status"] = "healthy"

    dead_letters = int(
        (await db.execute(select(func.count(WorkflowJob.id)).where(WorkflowJob.status == "dead_letter"))).scalar_one()
    )
    expired_leases = int(
        (
            await db.execute(
                select(func.count(WorkflowJob.id)).where(
                    WorkflowJob.status == "running",
                    WorkflowJob.lease_expires_at.is_not(None),
                    WorkflowJob.lease_expires_at < now,
                )
            )
        ).scalar_one()
    )
    return {
        "status": "degraded" if dead_letters or expired_leases or any(v.get("status") == "degraded" for v in providers.values()) else "healthy",
        "providers": providers,
        "dead_letter_jobs": dead_letters,
        "expired_leases": expired_leases,
        "checked_at": now.isoformat() + "Z",
    }


async def operations_profile(db: AsyncSession) -> dict[str, Any]:
    explicit = list(
        (
            await db.execute(
                select(OperationPreference)
                .where(OperationPreference.enabled.is_(True))
                .order_by(OperationPreference.domain, OperationPreference.preference_key)
            )
        ).scalars()
    )
    sender_rules = list((await db.execute(select(SenderRule).where(SenderRule.safe_shortcut.is_(True)))).scalars())
    creditors = list((await db.execute(select(Creditor).where(Creditor.auto_pay_enabled.is_(True)))).scalars())
    accounts = list((await db.execute(select(BankAccount).where(BankAccount.enabled_for_payments.is_(True)))).scalars())
    return {
        "preferences": [
            {
                "domain": row.domain,
                "key": row.preference_key,
                "value": _decode(row.value_json, {}),
                "confidence": float(row.confidence),
                "sample_count": row.sample_count,
                "source": row.source,
            }
            for row in explicit
        ],
        "learned_sender_rules": [
            {
                "sender": row.sender_key,
                "category": row.category,
                "priority": row.priority,
                "archive": row.archive,
                "preserve": row.preserve,
                "sample_count": row.sample_count,
            }
            for row in sender_rules
        ],
        "approved_creditors": [
            {"name": row.name, "iban": row.iban, "scope": row.account_scope, "max_auto_amount": row.max_auto_amount}
            for row in creditors
        ],
        "approved_payment_accounts": [
            {"id": row.id, "name": row.name, "scope": row.account_scope, "currency": row.currency, "safety_reserve": row.safety_reserve}
            for row in accounts
        ],
    }


async def daily_briefing(db: AsyncSession) -> dict[str, Any]:
    now = datetime.utcnow()
    since = now - timedelta(hours=24)
    upcoming = now + timedelta(days=7)

    action_emails = list(
        (
            await db.execute(
                select(EmailMessage)
                .where(EmailMessage.action_required.is_(True))
                .order_by(EmailMessage.received_at.desc().nullslast())
                .limit(20)
            )
        ).scalars()
    )
    open_tasks = list(
        (
            await db.execute(
                select(Task).where(Task.status.in_(["open", "waiting"])).order_by(Task.due_at.asc().nullslast()).limit(30)
            )
        ).scalars()
    )
    upcoming_bills = list(
        (
            await db.execute(
                select(Bill)
                .where(Bill.status.not_in(["paid", "cancelled", "reclassified_nonpayable"]), Bill.due_at.is_not(None), Bill.due_at <= upcoming)
                .order_by(Bill.due_at.asc())
                .limit(30)
            )
        ).scalars()
    )
    payment_actions = list(
        (
            await db.execute(
                select(Payment).where(Payment.requires_user_action.is_(True)).order_by(Payment.created_at.desc()).limit(20)
            )
        ).scalars()
    )
    support = list((await db.execute(select(SupportCase).where(SupportCase.status.not_in(["resolved", "closed"])).limit(20))).scalars())
    orders = list((await db.execute(select(OrderRecord).order_by(OrderRecord.updated_at.desc()).limit(10))).scalars())
    subscriptions = list((await db.execute(select(SubscriptionRecord).where(SubscriptionRecord.status == "active").limit(20))).scalars())
    financial_records = list(
        (
            await db.execute(
                select(FinancialRecord)
                .where(FinancialRecord.created_at >= since)
                .order_by(FinancialRecord.created_at.desc())
                .limit(20)
            )
        ).scalars()
    )
    activity = list(
        (
            await db.execute(select(AuditLog).where(AuditLog.created_at >= since).order_by(AuditLog.created_at.desc()).limit(50))
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

    appointments: list[dict[str, Any]] = []
    calendar_error = ""
    google_configured = int(
        (await db.execute(select(func.count(OAuthConnection.id)).where(OAuthConnection.provider == "google", OAuthConnection.enabled.is_(True)))).scalar_one()
    ) > 0
    if google_configured:
        try:
            from app.integrations.google_api import list_upcoming_calendar_events

            appointments = await list_upcoming_calendar_events(db, days=7, max_results=20)
        except Exception as exc:
            calendar_error = str(exc)[:1000]

    needs_you: list[dict[str, Any]] = []
    for payment in payment_actions:
        needs_you.append(
            {
                "type": "payment_authorization",
                "id": payment.id,
                "title": "Bank authorization required",
                "detail": f"{payment.amount} {payment.currency}",
                "authorization_url": payment.authorization_url,
            }
        )
    for task in open_tasks:
        if task.requires_approval:
            needs_you.append({"type": "task_approval", "id": task.id, "title": task.title, "detail": task.description})
    grouped_failures: dict[str, dict[str, Any]] = {}
    for job in dead_letters:
        signature = failure_signature(job.job_type, job.last_error)
        group = grouped_failures.setdefault(
            signature,
            {"job": job, "occurrences": 0},
        )
        group["occurrences"] += 1

    for group in grouped_failures.values():
        job = group["job"]
        occurrences = int(group["occurrences"])
        detail = job.last_error
        if occurrences > 1:
            detail = f"{detail}\nRepeated {occurrences} times; Autopilot grouped these into one exception."
        needs_you.append(
            {
                "type": "autopilot_exception",
                "id": job.id,
                "title": f"Autopilot failed: {job.job_type}",
                "detail": detail,
                "occurrences": occurrences,
            }
        )

    plan: list[dict[str, Any]] = []
    for item in needs_you[:10]:
        plan.append({"kind": item["type"], "title": item["title"], "owner": "user", "reason": "Human action is required"})
    for task in open_tasks:
        if not task.requires_approval and task.due_at is not None and task.due_at <= upcoming:
            plan.append({"kind": "task", "title": task.title, "owner": "autopilot", "due_at": task.due_at, "reason": "Upcoming task"})
    for bill in upcoming_bills:
        if bill.status == "validated":
            plan.append({"kind": "bill", "title": f"Handle {bill.creditor_name} bill", "owner": "autopilot", "due_at": bill.due_at, "reason": "Validated bill within seven days"})

    return {
        "generated_at": now.isoformat() + "Z",
        "plan": plan,
        "important_mail": [
            {"id": row.id, "sender": row.sender, "subject": row.subject, "priority": row.priority, "category": row.category}
            for row in action_emails
        ],
        "tasks": [
            {"id": row.id, "title": row.title, "due_at": row.due_at, "priority": row.priority, "requires_approval": row.requires_approval}
            for row in open_tasks
        ],
        "upcoming_bills": [
            {"id": row.id, "creditor": row.creditor_name, "amount": row.amount, "currency": row.currency, "due_at": row.due_at, "status": row.status}
            for row in upcoming_bills
        ],
        "appointments": appointments,
        "calendar_error": calendar_error,
        "support_cases": [{"id": row.id, "subject": row.subject, "priority": row.priority, "status": row.status} for row in support],
        "orders": [{"id": row.id, "merchant": row.merchant, "order_number": row.order_number, "status": row.status, "expected_delivery_at": row.expected_delivery_at} for row in orders],
        "subscriptions": [{"id": row.id, "provider": row.provider_name, "description": row.description, "amount": row.amount, "currency": row.currency, "next_charge_at": row.next_charge_at} for row in subscriptions],
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
        "activity": [
            {"id": row.id, "event_type": row.event_type, "entity_type": row.entity_type, "entity_id": row.entity_id, "result": row.result, "details": _decode(row.details_json, {}), "created_at": row.created_at}
            for row in activity
        ],
        "needs_you": needs_you,
    }


async def dispatch_intent(db: AsyncSession, intent: dict[str, Any]) -> dict[str, Any]:
    kind = str(intent.get("type") or "").strip().lower()
    if not kind:
        raise ValueError("intent.type is required")

    simple_mapping = {
        "sync_gmail": "gmail.sync",
        "sync_banking": "banking.autopilot",
        "sync_contacts": "google.contacts.sync",
        "run_connectors": "connectors.rules.run",
        "housekeeping": "housekeeping.documents",
    }
    supported = set(simple_mapping) | {"run_va", "bill_lifecycle"}
    if kind not in supported:
        raise ValueError(f"unsupported intent type: {kind}")

    bill = None
    if kind == "bill_lifecycle":
        bill_id = int(intent.get("bill_id") or 0)
        bill = await db.get(Bill, bill_id)
        if bill_id <= 0 or bill is None:
            raise ValueError("bill_lifecycle requires an existing bill_id")
        default_correlation = f"bill:{bill_id}:lifecycle:{datetime.utcnow().strftime('%Y%m%d')}"
    else:
        default_correlation = f"intent:{kind}:{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}"

    correlation = str(intent.get("correlation_key") or default_correlation)
    run, created = await create_workflow(
        db,
        workflow_type=kind,
        correlation_key=correlation,
        intent=intent,
    )
    if not created:
        return {"workflow_id": run.id, "created": False, "status": run.status}

    if kind in simple_mapping:
        job, _ = await enqueue_job(
            db,
            workflow_run_id=run.id,
            job_type=simple_mapping[kind],
            payload=intent.get("payload") if isinstance(intent.get("payload"), dict) else {},
            idempotency_key=f"{correlation}:execute",
            priority=int(intent.get("priority") or 50),
        )
        return {
            "workflow_id": run.id,
            "job_id": job.id,
            "created": True,
            "status": run.status,
        }

    if kind == "run_va":
        jobs = []
        for index, job_type in enumerate(
            (
                "gmail.sync",
                "banking.autopilot",
                "google.contacts.sync",
                "connectors.rules.run",
                "housekeeping.documents",
            )
        ):
            job, _ = await enqueue_job(
                db,
                workflow_run_id=run.id,
                job_type=job_type,
                idempotency_key=f"{correlation}:{index}:{job_type}",
                priority=20 + index * 10,
            )
            jobs.append(job.id)
        return {
            "workflow_id": run.id,
            "job_ids": jobs,
            "created": True,
            "status": run.status,
        }

    assert kind == "bill_lifecycle" and bill is not None
    run_after = datetime.utcnow()
    if bill.due_at is not None:
        from app.services.runtime_config import get_runtime_value

        try:
            days_before_due = max(
                0,
                min(
                    int(await get_runtime_value(db, "auto_pay_days_before_due", "3")),
                    60,
                ),
            )
        except ValueError:
            days_before_due = 3
        scheduled = bill.due_at - timedelta(days=days_before_due)
        if scheduled > run_after:
            run_after = scheduled

    job, _ = await enqueue_job(
        db,
        workflow_run_id=run.id,
        job_type="bill.lifecycle",
        payload={"bill_id": bill.id},
        idempotency_key=f"{correlation}:execute",
        priority=10,
        max_attempts=10,
        run_after=run_after,
    )
    return {
        "workflow_id": run.id,
        "job_id": job.id,
        "created": True,
        "status": run.status,
        "run_after": run_after,
    }

