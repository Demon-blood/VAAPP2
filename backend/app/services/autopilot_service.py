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
    OAuthConnection,
    OperationPreference,
    SenderRule,
    ServiceConnector,
    WorkflowJob,
)
from app.services.workflow_engine import create_workflow, enqueue_job, failure_recovery_class


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
        "plan": "autopilot.plan",
        "provider_health": "autopilot.provider_health",
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
                classification = failure_recovery_class(latest.job_type, latest.last_error)
                recovery_count = int(
                    (
                        await db.execute(
                            select(func.count(AuditLog.id)).where(
                                AuditLog.event_type == "workflow_job_auto_recovered",
                                AuditLog.entity_type == "workflow_job",
                                AuditLog.entity_id == str(latest.id),
                            )
                        )
                    ).scalar_one()
                )
                entry["recovery_class"] = classification
                entry["automatic_recoveries"] = recovery_count
                entry["status"] = (
                    "recovering" if classification == "transient" and recovery_count < 2 else "degraded"
                )
            elif latest.status in {"completed", "running", "pending", "retry"} and latest.created_at >= recent:
                if entry.get("status") not in {"not_configured", "degraded"}:
                    entry["status"] = "healthy"

    dead_letter_rows = list(
        (await db.execute(select(WorkflowJob).where(WorkflowJob.status == "dead_letter"))).scalars()
    )
    actionable_dead_letters = 0
    recovering_dead_letters = 0
    for job in dead_letter_rows:
        classification = failure_recovery_class(job.job_type, job.last_error)
        if classification != "transient":
            actionable_dead_letters += 1
            continue
        recovery_count = int(
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
        if recovery_count >= 2:
            actionable_dead_letters += 1
        else:
            recovering_dead_letters += 1
    dead_letters = actionable_dead_letters
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
    core_setup_required = [
        provider
        for provider in ("google", "ai_primary", "banking")
        if providers.get(provider, {}).get("status") == "not_configured"
    ]
    if dead_letters or expired_leases or any(v.get("status") == "degraded" for v in providers.values()):
        overall_status = "degraded"
    elif core_setup_required:
        overall_status = "needs_setup"
    else:
        overall_status = "healthy"
    return {
        "status": overall_status,
        "providers": providers,
        "dead_letter_jobs": dead_letters,
        "recovering_jobs": recovering_dead_letters,
        "expired_leases": expired_leases,
        "setup_required": core_setup_required,
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
    """Backward-compatible entry point for the v0.6+ Daily Intelligence service."""

    from app.services.briefing_service import daily_briefing as build_daily_briefing

    return await build_daily_briefing(db)


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
        "plan": "autopilot.plan",
        "provider_health": "autopilot.provider_health",
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
        dependency_ids = []
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
            dependency_ids.append(job.id)
        planner, _ = await enqueue_job(
            db,
            workflow_run_id=run.id,
            job_type="autopilot.plan",
            idempotency_key=f"{correlation}:planner",
            priority=75,
            dependency_ids=dependency_ids,
        )
        jobs.append(planner.id)
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

