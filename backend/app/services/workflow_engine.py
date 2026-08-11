from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import socket
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import exists, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import SessionLocal
from app.models.entities import AuditLog, WorkflowJob, WorkflowJobDependency, WorkflowRun
from app.services.audit import write_audit

logger = logging.getLogger(__name__)

JobHandler = Callable[[AsyncSession, dict[str, Any]], Awaitable[dict[str, Any] | None]]
_JOB_HANDLERS: dict[str, JobHandler] = {}


def utcnow() -> datetime:
    return datetime.utcnow()


def default_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def job_handler(job_type: str) -> Callable[[JobHandler], JobHandler]:
    def decorator(handler: JobHandler) -> JobHandler:
        _JOB_HANDLERS[job_type] = handler
        return handler

    return decorator


def _json_loads(value: str) -> dict[str, Any]:
    try:
        decoded = json.loads(value or "{}")
        return decoded if isinstance(decoded, dict) else {}
    except json.JSONDecodeError:
        return {}


def _backoff_seconds(attempt: int, *, base: int = 15, ceiling: int = 3600) -> int:
    # attempt is 1-based. 15s, 30s, 60s ... capped at one hour.
    return min(ceiling, base * (2 ** max(0, attempt - 1)))


def failure_signature(job_type: str, error: str) -> str:
    """Collapse repeated provider failures without hiding materially different errors."""

    value = (error or "").strip()
    http_match = re.search(r"HttpError\s+(\d{3})", value, flags=re.IGNORECASE)
    if http_match:
        return f"{job_type}:http:{http_match.group(1)}"
    value = re.sub(r"https?://\S+", "<url>", value)
    value = re.sub(r"\b[0-9a-f]{12,}\b", "<id>", value, flags=re.IGNORECASE)
    value = re.sub(r"\b\d{6,}\b", "<number>", value)
    value = re.sub(r"\s+", " ", value).lower()
    return f"{job_type}:{value[:240]}"


def failure_recovery_class(job_type: str, error: str) -> str:
    """Classify a terminal job failure for safe automatic recovery.

    ``transient`` may be requeued automatically. ``user_required`` covers OAuth/SCA/security or
    configuration states where retries cannot succeed without a person. Unknown failures stay
    exception-only instead of being retried forever.
    """

    value = (error or "").lower()
    http_match = re.search(r"(?:httperror|status(?: code)?|http)\s*[:= ]?\s*(\d{3})", value)
    status = int(http_match.group(1)) if http_match else None
    if status in {408, 409, 425, 429} or (status is not None and 500 <= status <= 599):
        return "transient"
    if status == 403 and any(term in value for term in ("rate limit", "ratelimit", "quota", "resource exhausted")):
        return "transient"
    if status in {401, 403}:
        return "user_required"

    user_terms = (
        "authorization required", "authorisation required", "oauth consent", "reauthor",
        "invalid credential", "invalid api key", "missing api key", "not configured",
        "permission denied", "access denied", "forbidden", "unauthorized", "unauthorised",
        "sca", "strong customer authentication", "itsme", "bank authorization",
        "bank authorisation", "user action required", "security challenge", "invalid_grant",
        "oauth connection", "google connection", "connection is missing", "credentials are missing",
    )
    if any(term in value for term in user_terms):
        return "user_required"

    transient_terms = (
        "timeout", "timed out", "temporarily unavailable", "temporary failure", "try again",
        "rate limit", "too many requests", "connection reset", "connection aborted",
        "connection refused", "service unavailable", "bad gateway", "gateway timeout",
        "dns", "name resolution", "worker lease expired", "conflict", "concurrent",
    )
    if any(term in value for term in transient_terms):
        return "transient"
    return "unknown"


async def auto_recover_transient_failures(
    db: AsyncSession,
    *,
    limit: int = 25,
    cooldown_minutes: int = 15,
    max_automatic_recoveries: int = 2,
) -> dict[str, int]:
    """Requeue only dead letters that are demonstrably transient."""

    compacted = await compact_duplicate_dead_letters(db)
    now = utcnow()
    cutoff = now - timedelta(minutes=max(1, cooldown_minutes))
    rows = list(
        (
            await db.execute(
                select(WorkflowJob)
                .where(WorkflowJob.status == "dead_letter", WorkflowJob.updated_at <= cutoff)
                .order_by(WorkflowJob.updated_at.asc(), WorkflowJob.id.asc())
                .limit(max(1, min(limit, 100)))
            )
        ).scalars()
    )
    recovered = 0
    skipped_user = 0
    skipped_unknown = 0
    exhausted = 0
    for job in rows:
        classification = failure_recovery_class(job.job_type, job.last_error)
        if classification == "user_required":
            skipped_user += 1
            continue
        if classification != "transient":
            skipped_unknown += 1
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
        if recovery_count >= max_automatic_recoveries:
            exhausted += 1
            continue
        job.status = "retry"
        job.attempts = 0
        job.run_after = now
        job.finished_at = None
        job.lease_owner = ""
        job.lease_expires_at = None
        if job.workflow_run_id is not None:
            run = await db.get(WorkflowRun, job.workflow_run_id)
            if run is not None:
                run.status = "running"
                run.finished_at = None
        await write_audit(
            db,
            "workflow_job_auto_recovered",
            entity_type="workflow_job",
            entity_id=str(job.id),
            details={
                "job_type": job.job_type,
                "failure_signature": failure_signature(job.job_type, job.last_error),
                "automatic_recovery_number": recovery_count + 1,
            },
        )
        recovered += 1
    if recovered:
        await db.commit()
    return {
        "recovered": recovered,
        "skipped_user_required": skipped_user,
        "skipped_unknown": skipped_unknown,
        "exhausted": exhausted,
        "superseded_duplicates": compacted["superseded"],
    }


async def compact_duplicate_dead_letters(db: AsyncSession) -> dict[str, int]:
    """Keep one actionable dead-letter per job/error signature.

    Historical attempts remain in PostgreSQL as ``superseded`` rows for auditability,
    but they no longer flood health counts or the user-facing Needs you queue.
    """

    rows = list(
        (
            await db.execute(
                select(WorkflowJob)
                .where(WorkflowJob.status == "dead_letter")
                .order_by(WorkflowJob.updated_at.desc(), WorkflowJob.id.desc())
            )
        ).scalars()
    )
    keepers: dict[str, WorkflowJob] = {}
    superseded = 0
    affected_runs: set[int] = set()
    for job in rows:
        signature = failure_signature(job.job_type, job.last_error)
        keeper = keepers.get(signature)
        if keeper is None:
            keepers[signature] = job
            continue
        job.status = "superseded"
        job.result_json = json.dumps(
            {
                "superseded_by_job_id": keeper.id,
                "failure_signature": signature,
                "reason": "duplicate_dead_letter",
            },
            ensure_ascii=False,
        )
        job.lease_owner = ""
        job.lease_expires_at = None
        superseded += 1
        if job.workflow_run_id is not None:
            affected_runs.add(job.workflow_run_id)

    for run_id in affected_runs:
        await refresh_workflow_status(db, run_id)

    if superseded:
        await write_audit(
            db,
            "workflow_dead_letters_compacted",
            entity_type="workflow",
            entity_id="autopilot",
            details={
                "superseded": superseded,
                "active_signatures": len(keepers),
            },
        )
        await db.commit()
    return {"superseded": superseded, "active_signatures": len(keepers)}


async def repair_v052_gmail_conflict_backlog(db: AsyncSession) -> dict[str, int]:
    """One-time migration for the pre-v0.5.2 Gmail HTTP-409 retry storm."""

    marker = (
        await db.execute(
            select(AuditLog.id)
            .where(AuditLog.event_type == "v052_gmail_conflict_backlog_repaired")
            .limit(1)
        )
    ).scalar_one_or_none()
    if marker is not None:
        return {"superseded": 0, "already_repaired": 1}

    rows = list(
        (
            await db.execute(
                select(WorkflowJob).where(
                    WorkflowJob.job_type == "gmail.sync",
                    WorkflowJob.status.in_(["retry", "dead_letter"]),
                    WorkflowJob.last_error.contains("HttpError 409"),
                )
            )
        ).scalars()
    )
    now = utcnow()
    run_ids: set[int] = set()
    for job in rows:
        job.status = "superseded"
        job.result_json = json.dumps(
            {
                "reason": "v0.5.2_gmail_409_backlog_repair",
                "previous_error": job.last_error,
            },
            ensure_ascii=False,
            default=str,
        )
        job.lease_owner = ""
        job.lease_expires_at = None
        job.finished_at = job.finished_at or now
        if job.workflow_run_id is not None:
            run_ids.add(job.workflow_run_id)

    for run_id in run_ids:
        await refresh_workflow_status(db, run_id)

    await write_audit(
        db,
        "v052_gmail_conflict_backlog_repaired",
        entity_type="workflow",
        entity_id="gmail.sync",
        details={"superseded": len(rows)},
    )
    await db.commit()
    return {"superseded": len(rows), "already_repaired": 0}


async def repair_v062_gmail_label_conflict_backlog(db: AsyncSession) -> dict[str, int]:
    """Retire pre-v0.6.2 Gmail label-conflict retries before scheduling a fresh sync.

    v0.6.2 changes the label resolver itself, so replaying a pile of historical
    full-mailbox jobs is unnecessary. Supersede only the exact live failure shape
    observed from Gmail label creation; the scheduler enqueues a fresh gmail.sync
    immediately after startup.
    """

    marker = (
        await db.execute(
            select(AuditLog.id)
            .where(AuditLog.event_type == "v062_gmail_label_conflict_backlog_repaired")
            .limit(1)
        )
    ).scalar_one_or_none()
    if marker is not None:
        return {"superseded": 0, "already_repaired": 1}

    rows = list(
        (
            await db.execute(
                select(WorkflowJob).where(
                    WorkflowJob.job_type == "gmail.sync",
                    WorkflowJob.status.in_(["retry", "dead_letter"]),
                    WorkflowJob.last_error.contains("Label name exists or conflicts"),
                )
            )
        ).scalars()
    )
    now = utcnow()
    run_ids: set[int] = set()
    for job in rows:
        job.status = "superseded"
        job.result_json = json.dumps(
            {
                "reason": "v0.6.2_gmail_label_conflict_backlog_repair",
                "previous_error": job.last_error,
            },
            ensure_ascii=False,
            default=str,
        )
        job.lease_owner = ""
        job.lease_expires_at = None
        job.finished_at = job.finished_at or now
        if job.workflow_run_id is not None:
            run_ids.add(job.workflow_run_id)

    for run_id in run_ids:
        await refresh_workflow_status(db, run_id)

    await write_audit(
        db,
        "v062_gmail_label_conflict_backlog_repaired",
        entity_type="workflow",
        entity_id="gmail.sync",
        details={"superseded": len(rows)},
    )
    await db.commit()
    return {"superseded": len(rows), "already_repaired": 0}


async def recover_autopilot_exceptions(db: AsyncSession, *, limit: int = 50) -> dict[str, int]:
    """Compact duplicate failures and retry one representative of each active exception."""

    compacted = await compact_duplicate_dead_letters(db)
    rows = list(
        (
            await db.execute(
                select(WorkflowJob)
                .where(WorkflowJob.status == "dead_letter")
                .order_by(WorkflowJob.updated_at.desc(), WorkflowJob.id.desc())
                .limit(max(1, min(limit, 100)))
            )
        ).scalars()
    )
    now = utcnow()
    run_ids: set[int] = set()
    for job in rows:
        job.status = "retry"
        job.attempts = 0
        job.run_after = now
        job.finished_at = None
        job.lease_owner = ""
        job.lease_expires_at = None
        if job.workflow_run_id is not None:
            run_ids.add(job.workflow_run_id)

    for run_id in run_ids:
        run = await db.get(WorkflowRun, run_id)
        if run is not None:
            run.status = "running"
            run.finished_at = None

    if rows:
        await write_audit(
            db,
            "workflow_exceptions_requeued",
            entity_type="workflow",
            entity_id="autopilot",
            details={"requeued": len(rows)},
        )
        await db.commit()
    return {
        "requeued": len(rows),
        "superseded_duplicates": compacted["superseded"],
    }


async def create_workflow(
    db: AsyncSession,
    *,
    workflow_type: str,
    correlation_key: str,
    intent: dict[str, Any] | None = None,
) -> tuple[WorkflowRun, bool]:
    existing = (
        await db.execute(select(WorkflowRun).where(WorkflowRun.correlation_key == correlation_key))
    ).scalar_one_or_none()
    if existing is not None:
        return existing, False
    run = WorkflowRun(
        workflow_type=workflow_type,
        correlation_key=correlation_key,
        status="running",
        intent_json=json.dumps(intent or {}, ensure_ascii=False, default=str),
    )
    db.add(run)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        existing = (
            await db.execute(select(WorkflowRun).where(WorkflowRun.correlation_key == correlation_key))
        ).scalar_one()
        return existing, False
    await write_audit(
        db,
        "workflow_started",
        entity_type="workflow_run",
        entity_id=str(run.id),
        details={"workflow_type": workflow_type, "correlation_key": correlation_key},
    )
    await db.commit()
    return run, True


async def refresh_workflow_status(db: AsyncSession, workflow_run_id: int) -> str:
    run = await db.get(WorkflowRun, workflow_run_id)
    if run is None:
        return "missing"
    statuses = list(
        (await db.execute(select(WorkflowJob.status).where(WorkflowJob.workflow_run_id == workflow_run_id))).scalars()
    )
    if not statuses:
        return run.status
    now = utcnow()
    if any(status == "dead_letter" for status in statuses):
        run.status = "failed"
        run.finished_at = now
    elif all(status in {"completed", "superseded"} for status in statuses):
        run.status = "completed" if all(status == "completed" for status in statuses) else "superseded"
        run.finished_at = now
    else:
        run.status = "running"
        run.finished_at = None
    await db.flush()
    return run.status


async def enqueue_job(
    db: AsyncSession,
    *,
    job_type: str,
    payload: dict[str, Any] | None = None,
    idempotency_key: str,
    run_after: datetime | None = None,
    priority: int = 100,
    max_attempts: int = 8,
    dependency_ids: list[int] | None = None,
    workflow_run_id: int | None = None,
) -> tuple[WorkflowJob, bool]:
    existing = (
        await db.execute(select(WorkflowJob).where(WorkflowJob.idempotency_key == idempotency_key))
    ).scalar_one_or_none()
    if existing is not None:
        return existing, False

    job = WorkflowJob(
        workflow_run_id=workflow_run_id,
        job_type=job_type,
        payload_json=json.dumps(payload or {}, ensure_ascii=False, default=str),
        idempotency_key=idempotency_key,
        status="pending",
        priority=priority,
        max_attempts=max(1, max_attempts),
        run_after=run_after or utcnow(),
    )
    db.add(job)
    try:
        await db.flush()
    except IntegrityError:
        # A second worker may have enqueued the same logical job after our initial lookup.
        await db.rollback()
        existing = (
            await db.execute(select(WorkflowJob).where(WorkflowJob.idempotency_key == idempotency_key))
        ).scalar_one()
        return existing, False

    for dependency_id in sorted(set(dependency_ids or [])):
        if dependency_id == job.id:
            raise ValueError("a job cannot depend on itself")
        db.add(WorkflowJobDependency(job_id=job.id, depends_on_job_id=dependency_id))

    await write_audit(
        db,
        "workflow_job_enqueued",
        entity_type="workflow_job",
        entity_id=str(job.id),
        details={"job_type": job_type, "idempotency_key": idempotency_key},
    )
    await db.commit()
    return job, True


async def lease_due_jobs(
    db: AsyncSession,
    *,
    worker_id: str,
    limit: int = 4,
    lease_seconds: int = 180,
) -> list[WorkflowJob]:
    now = utcnow()
    # Build the dependency predicate using aliases to avoid correlating the outer WorkflowJob
    # with the dependency target row accidentally.
    from sqlalchemy.orm import aliased

    dep_link = aliased(WorkflowJobDependency)
    dep_job = aliased(WorkflowJob)
    has_blocker = exists(
        select(1)
        .select_from(dep_link)
        .join(dep_job, dep_job.id == dep_link.depends_on_job_id)
        .where(dep_link.job_id == WorkflowJob.id, dep_job.status != "completed")
    )

    stmt = (
        select(WorkflowJob)
        .where(
            WorkflowJob.status.in_(["pending", "retry"]),
            WorkflowJob.run_after <= now,
            ~has_blocker,
        )
        .order_by(WorkflowJob.priority.asc(), WorkflowJob.run_after.asc(), WorkflowJob.id.asc())
        .limit(max(1, limit))
        .with_for_update(skip_locked=True)
    )
    jobs = list((await db.execute(stmt)).scalars())
    lease_until = now + timedelta(seconds=max(30, lease_seconds))
    for job in jobs:
        job.status = "running"
        job.attempts += 1
        job.lease_owner = worker_id
        job.lease_expires_at = lease_until
        job.started_at = job.started_at or now
        job.last_heartbeat_at = now
    if jobs:
        await db.commit()
    return jobs


async def heartbeat_job(db: AsyncSession, job_id: int, *, worker_id: str, lease_seconds: int = 180) -> bool:
    job = await db.get(WorkflowJob, job_id)
    if job is None or job.status != "running" or job.lease_owner != worker_id:
        return False
    now = utcnow()
    job.last_heartbeat_at = now
    job.lease_expires_at = now + timedelta(seconds=max(30, lease_seconds))
    await db.commit()
    return True


async def complete_job(
    db: AsyncSession,
    job: WorkflowJob,
    *,
    worker_id: str,
    result: dict[str, Any] | None = None,
) -> None:
    if job.lease_owner != worker_id:
        raise RuntimeError("workflow lease ownership changed before completion")
    job.status = "completed"
    job.result_json = json.dumps(result or {}, ensure_ascii=False, default=str)
    job.last_error = ""
    job.finished_at = utcnow()
    job.lease_owner = ""
    job.lease_expires_at = None
    await write_audit(
        db,
        "workflow_job_completed",
        entity_type="workflow_job",
        entity_id=str(job.id),
        details={"job_type": job.job_type, "attempts": job.attempts},
    )
    if job.workflow_run_id is not None:
        await refresh_workflow_status(db, job.workflow_run_id)
    await db.commit()


async def fail_job(
    db: AsyncSession,
    job: WorkflowJob,
    *,
    worker_id: str,
    error: BaseException,
) -> str:
    if job.lease_owner != worker_id:
        raise RuntimeError("workflow lease ownership changed before failure handling")
    job.last_error = str(error)[:8000]
    job.lease_owner = ""
    job.lease_expires_at = None
    if job.attempts >= job.max_attempts:
        job.status = "dead_letter"
        job.finished_at = utcnow()
        event = "workflow_job_dead_lettered"
    else:
        job.status = "retry"
        job.run_after = utcnow() + timedelta(seconds=_backoff_seconds(job.attempts))
        event = "workflow_job_retry_scheduled"
    await write_audit(
        db,
        event,
        entity_type="workflow_job",
        entity_id=str(job.id),
        result="failed",
        details={
            "job_type": job.job_type,
            "attempts": job.attempts,
            "max_attempts": job.max_attempts,
            "error": job.last_error,
            "next_run_at": job.run_after if job.status == "retry" else None,
        },
    )
    if job.workflow_run_id is not None:
        await refresh_workflow_status(db, job.workflow_run_id)
    await db.commit()
    return job.status


async def requeue_dead_letter(db: AsyncSession, job_id: int) -> WorkflowJob:
    job = await db.get(WorkflowJob, job_id)
    if job is None:
        raise LookupError("workflow job not found")
    if job.status != "dead_letter":
        raise ValueError("only dead-letter jobs can be requeued")
    job.status = "retry"
    job.attempts = 0
    job.run_after = utcnow()
    job.finished_at = None
    job.lease_owner = ""
    job.lease_expires_at = None
    await write_audit(
        db,
        "workflow_job_requeued",
        entity_type="workflow_job",
        entity_id=str(job.id),
        details={"job_type": job.job_type},
    )
    if job.workflow_run_id is not None:
        run = await db.get(WorkflowRun, job.workflow_run_id)
        if run is not None:
            run.status = "running"
            run.finished_at = None
    await db.commit()
    return job


async def recover_expired_leases(db: AsyncSession) -> dict[str, int]:
    now = utcnow()
    expired = list(
        (
            await db.execute(
                select(WorkflowJob).where(
                    WorkflowJob.status == "running",
                    WorkflowJob.lease_expires_at.is_not(None),
                    WorkflowJob.lease_expires_at < now,
                )
            )
        ).scalars()
    )
    recovered = 0
    dead_lettered = 0
    for job in expired:
        previous_owner = job.lease_owner
        job.lease_owner = ""
        job.lease_expires_at = None
        job.last_error = "worker lease expired; recovered by watchdog"
        if job.attempts >= job.max_attempts:
            job.status = "dead_letter"
            job.finished_at = now
            dead_lettered += 1
        else:
            job.status = "retry"
            job.run_after = now
            recovered += 1
        if job.workflow_run_id is not None:
            await refresh_workflow_status(db, job.workflow_run_id)
        await write_audit(
            db,
            "workflow_job_lease_recovered",
            entity_type="workflow_job",
            entity_id=str(job.id),
            result="recovered",
            details={"previous_owner": previous_owner, "new_status": job.status},
        )
    if expired:
        await db.commit()
    return {"recovered": recovered, "dead_lettered": dead_lettered}


async def _run_with_heartbeat(job_id: int, worker_id: str, handler_coro: Awaitable[dict[str, Any] | None]) -> dict[str, Any] | None:
    async def heartbeat_loop() -> None:
        while True:
            await asyncio.sleep(45)
            async with SessionLocal() as heartbeat_db:
                alive = await heartbeat_job(heartbeat_db, job_id, worker_id=worker_id)
                if not alive:
                    return

    heartbeat_task = asyncio.create_task(heartbeat_loop())
    try:
        return await handler_coro
    finally:
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass


async def execute_one_job(job_id: int, *, worker_id: str) -> str:
    async with SessionLocal() as db:
        job = await db.get(WorkflowJob, job_id)
        if job is None:
            return "missing"
        if job.status != "running" or job.lease_owner != worker_id:
            return "lease_lost"
        handler = _JOB_HANDLERS.get(job.job_type)
        if handler is None:
            await fail_job(db, job, worker_id=worker_id, error=RuntimeError(f"unknown job type: {job.job_type}"))
            return job.status
        payload = _json_loads(job.payload_json)
        try:
            result = await _run_with_heartbeat(job.id, worker_id, handler(db, payload))
            await complete_job(db, job, worker_id=worker_id, result=result)
            return "completed"
        except Exception as exc:
            logger.exception("Autopilot workflow job failed: id=%s type=%s", job.id, job.job_type)
            await db.rollback()
            job = await db.get(WorkflowJob, job_id)
            if job is None:
                return "missing"
            return await fail_job(db, job, worker_id=worker_id, error=exc)


async def worker_tick(*, worker_id: str | None = None, limit: int = 4) -> dict[str, int]:
    worker_id = worker_id or default_worker_id()
    async with SessionLocal() as db:
        jobs = await lease_due_jobs(db, worker_id=worker_id, limit=limit)
    outcome = {"leased": len(jobs), "completed": 0, "retry": 0, "dead_letter": 0, "lease_lost": 0}
    for job in jobs:
        status = await execute_one_job(job.id, worker_id=worker_id)
        if status in outcome:
            outcome[status] += 1
    return outcome


@job_handler("gmail.sync")
async def _gmail_sync(db: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
    from app.services.email_processor import sync_gmail

    max_messages = max(1, min(int(payload.get("max_messages") or 250), 1000))
    result = await sync_gmail(db, max_messages=max_messages)
    return {"result": result}


@job_handler("banking.autopilot")
async def _banking_autopilot(db: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
    from app.core.settings import get_settings
    from app.services.action_reconciler import reconcile_action_queue
    from app.services.banking_service import refresh_all_payments, sync_all_banks
    from app.services.financial_autopilot import (
        refresh_all_own_account_transfers,
        run_budget_autopilot,
        sync_bank_transactions,
    )
    from app.services.financial_reconciliation import reconcile_receipts_with_bank_transactions

    bank_sync = await sync_all_banks(db)
    transaction_sync = await sync_bank_transactions(db)
    receipt_reconciliation = await reconcile_receipts_with_bank_transactions(db)
    refreshed = await refresh_all_payments(db)
    transfer_refresh = await refresh_all_own_account_transfers(db)
    settings = get_settings()
    transfer_callback = str(settings.public_base_url).rstrip("/") + "/api/banking/transfer-callback"
    budget = await run_budget_autopilot(db, redirect_url=transfer_callback)
    reconciled = await reconcile_action_queue(db)
    return {
        "bank_sync": bank_sync,
        "transaction_sync": transaction_sync,
        "receipt_reconciliation": receipt_reconciliation,
        "payment_initiation": "delegated_to_durable_bill_lifecycle",
        "payment_refresh": refreshed,
        "internal_transfer_refresh": transfer_refresh,
        "budget_autopilot": budget,
        "action_reconciliation": reconciled,
    }


@job_handler("google.contacts.sync")
async def _contacts_sync(db: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
    from app.services.operations_service import sync_google_contacts

    return {"updated": await sync_google_contacts(db)}


@job_handler("connectors.rules.run")
async def _connector_rules(db: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
    from app.services.automation_engine import run_connector_automation_rules

    return await run_connector_automation_rules(db)


@job_handler("housekeeping.documents")
async def _document_housekeeping(db: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
    from app.services.action_reconciler import reconcile_action_queue
    from app.services.financial_reconciliation import reclassify_existing_nonpayable_bills
    from app.services.operations_service import cleanup_low_value_documents

    result = await cleanup_low_value_documents(db)
    financial = await reclassify_existing_nonpayable_bills(db)
    reconciled = await reconcile_action_queue(db)
    return {"documents": result, "financial_reclassification": financial, "actions": reconciled}


@job_handler("bill.lifecycle")
async def _bill_lifecycle(db: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
    from sqlalchemy import select

    from app.core.settings import get_settings
    from app.models.entities import BankAccount, Bill, Creditor, Payment, Task
    from app.services.banking_service import create_payment_for_bill, refresh_payment

    bill_id = int(payload.get("bill_id") or 0)
    bill = await db.get(Bill, bill_id)
    if bill is None:
        raise ValueError(f"bill not found: {bill_id}")
    if bill.status == "reclassified_nonpayable":
        return {"bill_id": bill.id, "state": "nonpayable"}
    if bill.status == "paid":
        return {"bill_id": bill.id, "state": "settled"}

    payment = (
        await db.execute(
            select(Payment)
            .where(Payment.bill_id == bill.id, Payment.status.not_in(["failed", "cancelled", "rejected"]))
            .order_by(Payment.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if payment is not None:
        if payment.external_payment_id:
            await refresh_payment(db, payment)
        return {
            "bill_id": bill.id,
            "state": "settled" if payment.status == "completed" else ("authorization_required" if payment.requires_user_action else "payment_pending"),
            "payment_id": payment.id,
            "payment_status": payment.status,
            "requires_user_action": payment.requires_user_action,
            "authorization_url": payment.authorization_url,
        }

    if bill.status != "validated" or not bill.iban:
        existing_task = (
            await db.execute(
                select(Task).where(
                    Task.source_type == "bill_review",
                    Task.source_id == str(bill.id),
                    Task.status.in_(["open", "waiting"]),
                )
            )
        ).scalar_one_or_none()
        if existing_task is None:
            db.add(
                Task(
                    title=f"Review bill from {bill.creditor_name}",
                    description=bill.risk_reason or "The creditor or payment details require review before payment can be initiated.",
                    source_type="bill_review",
                    source_id=str(bill.id),
                    priority="high",
                    requires_approval=True,
                )
            )
            await db.commit()
        return {"bill_id": bill.id, "state": "needs_review"}

    creditor = await db.get(Creditor, bill.creditor_id) if bill.creditor_id else None
    if creditor is None or not creditor.auto_pay_enabled or creditor.iban != bill.iban:
        existing_task = (
            await db.execute(
                select(Task).where(
                    Task.source_type == "creditor_review",
                    Task.source_id == str(bill.id),
                    Task.status.in_(["open", "waiting"]),
                )
            )
        ).scalar_one_or_none()
        if existing_task is None:
            db.add(
                Task(
                    title=f"Approve creditor: {bill.creditor_name}",
                    description="The bill cannot be paid automatically until the exact creditor IBAN and payment limit are approved.",
                    source_type="creditor_review",
                    source_id=str(bill.id),
                    priority="high",
                    requires_approval=True,
                )
            )
            await db.commit()
        return {"bill_id": bill.id, "state": "needs_creditor_approval"}

    accounts = list(
        (
            await db.execute(
                select(BankAccount).where(
                    BankAccount.enabled_for_payments.is_(True),
                    BankAccount.account_scope == bill.account_scope,
                    BankAccount.currency == bill.currency,
                ).order_by(BankAccount.available_balance.desc().nullslast(), BankAccount.current_balance.desc().nullslast())
            )
        ).scalars()
    )
    selected = None
    for account in accounts:
        available = account.available_balance if account.available_balance is not None else account.current_balance
        if available is not None and available - bill.amount >= account.safety_reserve:
            selected = account
            break
    if selected is None:
        existing_task = (
            await db.execute(
                select(Task).where(
                    Task.source_type == "bill_payment",
                    Task.source_id == str(bill.id),
                    Task.status.in_(["open", "waiting"]),
                )
            )
        ).scalar_one_or_none()
        if existing_task is None:
            db.add(
                Task(
                    title=f"Funding required for {bill.creditor_name}",
                    description=f"No approved {bill.account_scope} account can pay {bill.amount} {bill.currency} while preserving its safety reserve.",
                    source_type="bill_payment",
                    source_id=str(bill.id),
                    priority="high",
                    requires_approval=False,
                )
            )
            await db.commit()
        return {"bill_id": bill.id, "state": "funding_required"}

    settings = get_settings()
    redirect_url = str(settings.public_base_url).rstrip("/") + "/api/banking/payment-callback"
    payment = await create_payment_for_bill(
        db,
        bill_id=bill.id,
        bank_account_id=selected.id,
        redirect_url=redirect_url,
    )
    return {
        "bill_id": bill.id,
        "state": "authorization_required" if payment.requires_user_action else "payment_initiated",
        "payment_id": payment.id,
        "payment_status": payment.status,
        "requires_user_action": payment.requires_user_action,
        "authorization_url": payment.authorization_url,
    }


@job_handler("autopilot.provider_health")
async def _provider_health(db: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
    from app.services.autopilot_service import provider_health_snapshot
    from app.services.runtime_config import get_runtime_value

    enabled = (await get_runtime_value(db, "auto_recover_transient_failures", "true")).lower() == "true"
    recovery = (
        await auto_recover_transient_failures(db)
        if enabled
        else {
            "recovered": 0,
            "skipped_user_required": 0,
            "skipped_unknown": 0,
            "exhausted": 0,
            "superseded_duplicates": 0,
            "disabled": True,
        }
    )
    health = await provider_health_snapshot(db)
    health["automatic_recovery"] = recovery
    return health


@job_handler("autopilot.plan")
async def _autopilot_plan(db: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
    from app.services.autopilot_planner import proactive_plan

    return await proactive_plan(db)


@job_handler("autopilot.daily_briefing")
async def _daily_briefing(db: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
    from app.services.briefing_service import daily_briefing

    return await daily_briefing(db)
