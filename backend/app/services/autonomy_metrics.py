from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import AutonomyMetricDaily, VAObjective


_ALLOWED_FIELDS = {
    "events_ingested",
    "objectives_created",
    "objectives_completed",
    "user_interventions",
    "provider_failures",
    "automatic_recoveries",
    "followups_due",
}


def _day_key(now: datetime | None = None) -> str:
    return (now or datetime.utcnow()).strftime("%Y-%m-%d")


async def metric_row(db: AsyncSession) -> AutonomyMetricDaily:
    key = _day_key()
    row = await db.get(AutonomyMetricDaily, key)
    if row is None:
        row = AutonomyMetricDaily(day_key=key)
        db.add(row)
        await db.flush()
    return row


async def increment_metric(db: AsyncSession, field: str, amount: int = 1) -> None:
    if field not in _ALLOWED_FIELDS:
        raise ValueError(f"Unknown autonomy metric: {field}")
    row = await metric_row(db)
    setattr(row, field, int(getattr(row, field) or 0) + int(amount))


async def autonomy_summary(db: AsyncSession, *, days: int = 30) -> dict[str, Any]:
    days = max(1, min(int(days), 365))
    cutoff_key = (datetime.utcnow() - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    rows = list(
        (
            await db.execute(
                select(AutonomyMetricDaily)
                .where(AutonomyMetricDaily.day_key >= cutoff_key)
                .order_by(AutonomyMetricDaily.day_key.asc())
            )
        ).scalars()
    )
    totals = {
        field: sum(int(getattr(row, field) or 0) for row in rows)
        for field in sorted(_ALLOWED_FIELDS)
    }
    cutoff_at = datetime.utcnow() - timedelta(days=days)
    completed_total = int(
        (
            await db.execute(
                select(func.count(VAObjective.id)).where(
                    VAObjective.status == "completed",
                    VAObjective.finished_at.is_not(None),
                    VAObjective.finished_at >= cutoff_at,
                )
            )
        ).scalar_one()
    )
    completed_autonomously = int(
        (
            await db.execute(
                select(func.count(VAObjective.id)).where(
                    VAObjective.status == "completed",
                    VAObjective.finished_at.is_not(None),
                    VAObjective.finished_at >= cutoff_at,
                    VAObjective.user_intervention_count == 0,
                )
            )
        ).scalar_one()
    )
    autonomous_rate = (completed_autonomously / completed_total * 100.0) if completed_total else None
    active = int(
        (
            await db.execute(
                select(func.count(VAObjective.id)).where(
                    VAObjective.status.not_in(["completed", "cancelled", "failed"])
                )
            )
        ).scalar_one()
    )
    needs_user = int(
        (
            await db.execute(
                select(func.count(VAObjective.id)).where(VAObjective.status == "needs_user")
            )
        ).scalar_one()
    )
    waiting = int(
        (
            await db.execute(
                select(func.count(VAObjective.id)).where(
                    VAObjective.status.in_(["waiting", "waiting_external", "blocked_capability"])
                )
            )
        ).scalar_one()
    )
    return {
        "days": days,
        "totals": totals,
        "autonomous_completion_rate": None if autonomous_rate is None else round(autonomous_rate, 2),
        "completed_autonomously": completed_autonomously,
        "completed_with_user_help": completed_total - completed_autonomously,
        "resolved_completed": completed_total,
        "active_objectives": active,
        "needs_user": needs_user,
        "waiting_external": waiting,
        "daily": [
            {
                "day": row.day_key,
                "events": row.events_ingested,
                "created": row.objectives_created,
                "completed": row.objectives_completed,
                "needs_user": row.user_interventions,
                "provider_failures": row.provider_failures,
                "recoveries": row.automatic_recoveries,
                "followups_due": row.followups_due,
            }
            for row in rows
        ],
    }
