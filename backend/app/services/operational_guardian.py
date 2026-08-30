from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import get_settings
from app.models.entities import (
    BankConnection,
    OAuthConnection,
    Payment,
    VAObjective,
    VAOutcomeEvidence,
    WorkflowJob,
)
from app.services.audit import write_audit
from app.services.workflow_engine import enqueue_job

_BANK_WARNING = timedelta(days=7)
_BANK_URGENT = timedelta(hours=48)
_OAUTH_WARNING = timedelta(hours=24)
_TERMINAL_OBJECTIVE_STATES = {"completed", "cancelled", "failed"}


def _now() -> datetime:
    return datetime.utcnow()


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _fingerprint(value: datetime | None) -> str:
    if value is None:
        return "unknown"
    return value.strftime("%Y%m%dT%H%M%S")


def _job_plan() -> dict[str, tuple[int, int, dict[str, Any]]]:
    settings = get_settings()
    return {
        "va.core.cycle": (max(5, 3), 12, {}),
        "gmail.sync": (max(15, int(settings.gmail_sync_minutes) * 3), 20, {"max_messages": 250}),
        "calendar.sync": (max(20, int(settings.external_sync_minutes) * 3), 22, {"days_back": 30, "days_forward": 365}),
        "banking.autopilot": (max(20, int(settings.bank_sync_minutes) * 3), 10, {}),
        "autopilot.provider_health": (20, 80, {}),
    }


async def _ensure_user_objective(
    db: AsyncSession,
    *,
    source_id: str,
    correlation_key: str,
    title: str,
    goal: str,
    reason: str,
    due_at: datetime | None,
    priority: str,
    context: dict[str, Any],
) -> tuple[VAObjective, bool]:
    row = (
        await db.execute(select(VAObjective).where(VAObjective.correlation_key == correlation_key))
    ).scalar_one_or_none()
    created = False
    if row is None:
        row = VAObjective(
            correlation_key=correlation_key,
            source_type="operational_guardian",
            source_id=source_id,
            title=title,
            goal=goal,
            category="operational_continuity",
            priority=priority,
            risk_level="high",
            status="needs_user",
            due_at=due_at,
            context_json=_dump(context),
            plan_json="{}",
            needs_user_reason=reason,
        )
        db.add(row)
        await db.flush()
        created = True
    elif row.status not in _TERMINAL_OBJECTIVE_STATES:
        row.status = "needs_user"
        row.title = title
        row.goal = goal
        row.priority = priority
        row.risk_level = "high"
        row.due_at = due_at
        row.context_json = _dump(context)
        row.needs_user_reason = reason
        row.blocked_reason = ""
        row.last_error = ""
    return row, created


async def _complete_verified_objectives(
    db: AsyncSession,
    *,
    source_id: str,
    provider: str,
    evidence: dict[str, Any],
) -> int:
    rows = list(
        (
            await db.execute(
                select(VAObjective).where(
                    VAObjective.source_type == "operational_guardian",
                    VAObjective.source_id == source_id,
                    VAObjective.status.not_in(tuple(_TERMINAL_OBJECTIVE_STATES)),
                )
            )
        ).scalars()
    )
    completed = 0
    now = _now()
    for row in rows:
        row.status = "completed"
        row.finished_at = now
        row.needs_user_reason = ""
        row.blocked_reason = ""
        row.last_error = ""
        exists = int(
            (
                await db.execute(
                    select(func.count(VAOutcomeEvidence.id)).where(
                        VAOutcomeEvidence.objective_id == row.id,
                        VAOutcomeEvidence.evidence_type == "provider_state",
                    )
                )
            ).scalar_one()
        )
        if not exists:
            db.add(
                VAOutcomeEvidence(
                    objective_id=row.id,
                    evidence_type="provider_state",
                    provider=provider,
                    external_ref=source_id,
                    details_json=_dump(evidence),
                    verified_at=now,
                )
            )
        completed += 1
    return completed


async def _bank_consent_state(db: AsyncSession, *, mutate: bool) -> dict[str, Any]:
    now = _now()
    rows = list((await db.execute(select(BankConnection).order_by(BankConnection.id.asc()))).scalars())
    expiring: list[dict[str, Any]] = []
    created = 0
    completed = 0
    for row in rows:
        source_id = f"bank_connection:{row.id}"
        if row.status != "active" or row.valid_until is None:
            continue
        remaining = row.valid_until - now
        if remaining <= _BANK_WARNING:
            urgent = remaining <= _BANK_URGENT
            expired = remaining.total_seconds() <= 0
            severity = "expired" if expired else "urgent" if urgent else "upcoming"
            item = {
                "connection_id": row.id,
                "provider": row.provider,
                "institution": row.institution_name,
                "valid_until": row.valid_until.isoformat() + "Z",
                "hours_remaining": round(remaining.total_seconds() / 3600, 1),
                "severity": severity,
            }
            expiring.append(item)
            if mutate:
                objective, was_created = await _ensure_user_objective(
                    db,
                    source_id=source_id,
                    correlation_key=f"operational:bank_consent:{row.id}:{_fingerprint(row.valid_until)}",
                    title=f"Renew {row.institution_name} bank consent",
                    goal="Keep bank synchronization available without an avoidable service interruption.",
                    reason=(
                        "The bank consent has expired and requires fresh account-holder authorization."
                        if expired
                        else f"Renew the bank consent before {row.valid_until.isoformat()}Z; the provider requires fresh account-holder authorization."
                    ),
                    due_at=row.valid_until,
                    priority="high" if urgent else "normal",
                    context={
                        "kind": "bank_consent_renewal",
                        "connection_id": row.id,
                        "provider": row.provider,
                        "institution": row.institution_name,
                        "valid_until": row.valid_until.isoformat() + "Z",
                        "verified_boundary": "provider_account_holder_authorization",
                    },
                )
                item["objective_id"] = objective.id
                created += int(was_created)
        elif mutate:
            completed += await _complete_verified_objectives(
                db,
                source_id=source_id,
                provider=row.provider or "enable_banking",
                evidence={
                    "kind": "bank_consent_valid",
                    "connection_id": row.id,
                    "valid_until": row.valid_until.isoformat() + "Z",
                    "verified_from": "bank_connection_provider_state",
                },
            )
    return {"expiring": expiring, "created": created, "completed": completed}


async def _oauth_state(db: AsyncSession, *, mutate: bool) -> dict[str, Any]:
    now = _now()
    rows = list(
        (
            await db.execute(
                select(OAuthConnection).where(OAuthConnection.enabled.is_(True)).order_by(OAuthConnection.id.asc())
            )
        ).scalars()
    )
    reconnect_required: list[dict[str, Any]] = []
    refreshable = 0
    created = 0
    completed = 0
    for row in rows:
        source_id = f"oauth_connection:{row.id}"
        if row.expires_at is None:
            continue
        remaining = row.expires_at - now
        has_refresh = bool((row.refresh_token_encrypted or "").strip())
        if has_refresh:
            refreshable += int(remaining <= _OAUTH_WARNING)
            if mutate:
                completed += await _complete_verified_objectives(
                    db,
                    source_id=source_id,
                    provider=row.provider,
                    evidence={
                        "kind": "oauth_refresh_available",
                        "provider": row.provider,
                        "expires_at": row.expires_at.isoformat() + "Z",
                        "verified_from": "oauth_connection_provider_state",
                    },
                )
            continue
        if remaining <= _OAUTH_WARNING:
            expired = remaining.total_seconds() <= 0
            item = {
                "connection_id": row.id,
                "provider": row.provider,
                "expires_at": row.expires_at.isoformat() + "Z",
                "hours_remaining": round(remaining.total_seconds() / 3600, 1),
                "severity": "expired" if expired else "urgent",
            }
            reconnect_required.append(item)
            if mutate:
                objective, was_created = await _ensure_user_objective(
                    db,
                    source_id=source_id,
                    correlation_key=f"operational:oauth_reconnect:{row.id}:{_fingerprint(row.expires_at)}",
                    title=f"Reconnect {row.provider.title()} authorization",
                    goal="Restore the provider connection before autonomous work is interrupted.",
                    reason=(
                        f"The {row.provider} authorization has expired and no refresh credential is available; reconnecting requires provider authorization."
                        if expired
                        else f"The {row.provider} authorization expires soon and has no refresh credential; provider authorization is required to reconnect before {row.expires_at.isoformat()}Z."
                    ),
                    due_at=row.expires_at,
                    priority="high",
                    context={
                        "kind": "oauth_reconnect",
                        "connection_id": row.id,
                        "provider": row.provider,
                        "expires_at": row.expires_at.isoformat() + "Z",
                        "verified_boundary": "provider_authorization",
                    },
                )
                item["objective_id"] = objective.id
                created += int(was_created)
        elif mutate:
            completed += await _complete_verified_objectives(
                db,
                source_id=source_id,
                provider=row.provider,
                evidence={
                    "kind": "oauth_authorization_valid",
                    "provider": row.provider,
                    "expires_at": row.expires_at.isoformat() + "Z",
                    "verified_from": "oauth_connection_provider_state",
                },
            )
    return {
        "reconnect_required": reconnect_required,
        "refreshable_expiries": refreshable,
        "created": created,
        "completed": completed,
    }


async def _payment_state(db: AsyncSession) -> dict[str, Any]:
    cutoff = _now() - timedelta(days=7)
    rejected = list(
        (
            await db.execute(
                select(Payment).where(
                    Payment.updated_at >= cutoff,
                    Payment.status.in_(["failed", "rejected", "declined"]),
                )
            )
        ).scalars()
    )
    uncertain = list(
        (
            await db.execute(
                select(Payment).where(
                    Payment.updated_at >= cutoff,
                    Payment.status == "creation_uncertain",
                    Payment.external_payment_id.is_(None),
                )
            )
        ).scalars()
    )
    return {
        "recent_rejections": len(rejected),
        "provider_user_action_required": sum(1 for row in rejected if row.requires_user_action),
        "creation_uncertain": len(uncertain),
        "system_owned_uncertainty": len(uncertain),
    }


async def _workflow_state(db: AsyncSession, *, mutate: bool) -> dict[str, Any]:
    now = _now()
    stale: list[dict[str, Any]] = []
    healed: list[str] = []
    if not get_settings().automation_enabled:
        return {"stale": stale, "self_healed": healed, "automation_enabled": False}

    for job_type, (max_age_minutes, priority, payload) in _job_plan().items():
        latest = (
            await db.execute(
                select(WorkflowJob)
                .where(WorkflowJob.job_type == job_type)
                .order_by(WorkflowJob.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        cutoff = now - timedelta(minutes=max_age_minutes)
        if latest is not None and latest.created_at >= cutoff:
            continue
        status = latest.status if latest is not None else "missing"
        stale.append(
            {
                "job_type": job_type,
                "last_status": status,
                "last_job_at": latest.created_at.isoformat() + "Z" if latest is not None else "",
                "max_age_minutes": max_age_minutes,
            }
        )
        if not mutate:
            continue
        # Never manufacture a second copy of a stuck or dead-letter job. The existing
        # watchdog/recovery classifier owns those states. Only restore a missing cadence
        # after the previous cadence completed or no historical row exists.
        if latest is not None and latest.status not in {"completed", "cancelled"}:
            continue
        bucket = int(now.timestamp()) // max(60, max_age_minutes * 60)
        await enqueue_job(
            db,
            job_type=job_type,
            payload=payload,
            idempotency_key=f"operational_guardian:{job_type}:{bucket}",
            priority=priority,
            max_attempts=5,
        )
        healed.append(job_type)
    return {"stale": stale, "self_healed": healed, "automation_enabled": True}


async def operational_guardian_status(db: AsyncSession) -> dict[str, Any]:
    bank = await _bank_consent_state(db, mutate=False)
    oauth = await _oauth_state(db, mutate=False)
    payments = await _payment_state(db)
    workflow = await _workflow_state(db, mutate=False)
    needs_user = len(bank["expiring"]) + len(oauth["reconnect_required"])
    system_issues = len(workflow["stale"]) + payments["system_owned_uncertainty"]
    status = "needs_user" if needs_user else "degraded" if system_issues else "healthy"
    return {
        "status": status,
        "needs_user_count": needs_user,
        "system_issue_count": system_issues,
        "bank_consent": bank,
        "oauth": oauth,
        "payments": payments,
        "workflow": workflow,
        "checked_at": _now().isoformat() + "Z",
    }


async def run_operational_guardian(db: AsyncSession) -> dict[str, Any]:
    bank = await _bank_consent_state(db, mutate=True)
    oauth = await _oauth_state(db, mutate=True)
    workflow = await _workflow_state(db, mutate=True)
    payments = await _payment_state(db)
    await db.commit()

    needs_user = len(bank["expiring"]) + len(oauth["reconnect_required"])
    system_issues = (
        len(workflow["stale"])
        - len(workflow["self_healed"])
        + payments["system_owned_uncertainty"]
    )
    changed = bank["created"] + bank["completed"] + oauth["created"] + oauth["completed"] + len(workflow["self_healed"])
    if changed:
        await write_audit(
            db,
            "operational_guardian_reconciled",
            entity_type="system",
            entity_id="operational_guardian",
            details={
                "bank_objectives_created": bank["created"],
                "bank_objectives_completed": bank["completed"],
                "oauth_objectives_created": oauth["created"],
                "oauth_objectives_completed": oauth["completed"],
                "self_healed_jobs": workflow["self_healed"],
            },
        )
        await db.commit()
    status = "needs_user" if needs_user else "degraded" if system_issues > 0 else "healthy"
    return {
        "status": status,
        "needs_user_count": needs_user,
        "system_issue_count": max(0, system_issues),
        "bank_consent": bank,
        "oauth": oauth,
        "payments": payments,
        "workflow": workflow,
        "checked_at": _now().isoformat() + "Z",
    }
