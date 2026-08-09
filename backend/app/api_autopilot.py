from __future__ import annotations

import json
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_device
from app.core.database import get_db
from app.models.entities import Device, WorkflowJob, WorkflowRun
from app.services.workflow_engine import requeue_dead_letter

router = APIRouter(prefix="/api/autopilot", tags=["autopilot"])


def _decode(value: str) -> dict:
    try:
        decoded = json.loads(value or "{}")
        return decoded if isinstance(decoded, dict) else {}
    except json.JSONDecodeError:
        return {}


@router.get("/health")
async def autopilot_health(
    db: AsyncSession = Depends(get_db),
    _: Device = Depends(require_device),
) -> dict:
    now = datetime.utcnow()
    stale_before = now - timedelta(minutes=5)
    counts = dict(
        (
            await db.execute(
                select(WorkflowJob.status, func.count(WorkflowJob.id)).group_by(WorkflowJob.status)
            )
        ).all()
    )
    stalled = int(
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
    overdue = int(
        (
            await db.execute(
                select(func.count(WorkflowJob.id)).where(
                    WorkflowJob.status.in_(["pending", "retry"]),
                    WorkflowJob.run_after < stale_before,
                )
            )
        ).scalar_one()
    )
    return {
        "status": "degraded" if stalled or counts.get("dead_letter", 0) else "healthy",
        "jobs": counts,
        "expired_leases": stalled,
        "overdue_jobs": overdue,
        "checked_at": now.isoformat() + "Z",
    }


@router.get("/jobs")
async def list_autopilot_jobs(
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _: Device = Depends(require_device),
) -> list[dict]:
    stmt = select(WorkflowJob).order_by(WorkflowJob.created_at.desc()).limit(limit)
    if status:
        stmt = stmt.where(WorkflowJob.status == status)
    rows = list((await db.execute(stmt)).scalars())
    return [
        {
            "id": row.id,
            "workflow_run_id": row.workflow_run_id,
            "job_type": row.job_type,
            "status": row.status,
            "priority": row.priority,
            "attempts": row.attempts,
            "max_attempts": row.max_attempts,
            "run_after": row.run_after,
            "lease_owner": row.lease_owner,
            "lease_expires_at": row.lease_expires_at,
            "last_error": row.last_error,
            "result": _decode(row.result_json),
            "created_at": row.created_at,
            "finished_at": row.finished_at,
        }
        for row in rows
    ]


@router.get("/workflows")
async def list_autopilot_workflows(
    limit: int = Query(default=30, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _: Device = Depends(require_device),
) -> list[dict]:
    rows = list(
        (
            await db.execute(select(WorkflowRun).order_by(WorkflowRun.created_at.desc()).limit(limit))
        ).scalars()
    )
    return [
        {
            "id": row.id,
            "workflow_type": row.workflow_type,
            "correlation_key": row.correlation_key,
            "status": row.status,
            "intent": _decode(row.intent_json),
            "result": _decode(row.result_json),
            "created_at": row.created_at,
            "finished_at": row.finished_at,
        }
        for row in rows
    ]


@router.post("/jobs/{job_id}/requeue")
async def requeue_autopilot_job(
    job_id: int,
    db: AsyncSession = Depends(get_db),
    _: Device = Depends(require_device),
) -> dict:
    try:
        job = await requeue_dead_letter(db, job_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"id": job.id, "status": job.status, "run_after": job.run_after}
