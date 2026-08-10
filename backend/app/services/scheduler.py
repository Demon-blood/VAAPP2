from __future__ import annotations

import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.core.database import SessionLocal
from app.core.settings import get_settings
from app.services.workflow_engine import (
    compact_duplicate_dead_letters,
    enqueue_job,
    recover_expired_leases,
    worker_tick,
)

logger = logging.getLogger(__name__)
settings = get_settings()
scheduler = AsyncIOScheduler(timezone=settings.default_timezone)


def _bucket_key(prefix: str, minutes: int) -> str:
    window = max(1, minutes) * 60
    bucket = int(datetime.utcnow().timestamp()) // window
    return f"{prefix}:{bucket}"


async def gmail_enqueue_job() -> None:
    if not settings.automation_enabled:
        return
    async with SessionLocal() as db:
        try:
            await enqueue_job(
                db,
                job_type="gmail.sync",
                payload={"max_messages": 250},
                idempotency_key=_bucket_key("gmail.sync", settings.gmail_sync_minutes),
                priority=20,
            )
        except Exception:
            logger.exception("Failed to enqueue Gmail Autopilot job")


async def banking_enqueue_job() -> None:
    if not settings.automation_enabled:
        return
    async with SessionLocal() as db:
        try:
            await enqueue_job(
                db,
                job_type="banking.autopilot",
                payload={},
                idempotency_key=_bucket_key("banking.autopilot", settings.bank_sync_minutes),
                priority=10,
                max_attempts=10,
            )
        except Exception:
            logger.exception("Failed to enqueue banking Autopilot job")


async def external_services_enqueue_job() -> None:
    if not settings.automation_enabled:
        return
    async with SessionLocal() as db:
        try:
            await enqueue_job(
                db,
                job_type="google.contacts.sync",
                payload={},
                idempotency_key=_bucket_key("google.contacts.sync", settings.external_sync_minutes),
                priority=60,
            )
        except Exception:
            logger.exception("Failed to enqueue external-services Autopilot job")


async def connector_rules_enqueue_job() -> None:
    if not settings.automation_enabled:
        return
    async with SessionLocal() as db:
        try:
            await enqueue_job(
                db,
                job_type="connectors.rules.run",
                payload={},
                idempotency_key=_bucket_key("connectors.rules.run", 1),
                priority=50,
            )
        except Exception:
            logger.exception("Failed to enqueue connector-rules Autopilot job")


async def housekeeping_enqueue_job() -> None:
    if not settings.automation_enabled:
        return
    async with SessionLocal() as db:
        try:
            await enqueue_job(
                db,
                job_type="housekeeping.documents",
                payload={},
                idempotency_key=_bucket_key("housekeeping.documents", 360),
                priority=90,
                max_attempts=5,
            )
        except Exception:
            logger.exception("Failed to enqueue housekeeping Autopilot job")


async def provider_health_enqueue_job() -> None:
    if not settings.automation_enabled:
        return
    async with SessionLocal() as db:
        try:
            await enqueue_job(
                db,
                job_type="autopilot.provider_health",
                payload={},
                idempotency_key=_bucket_key("autopilot.provider_health", 5),
                priority=80,
                max_attempts=3,
            )
        except Exception:
            logger.exception("Failed to enqueue provider-health Autopilot job")


async def daily_briefing_enqueue_job() -> None:
    if not settings.automation_enabled:
        return
    async with SessionLocal() as db:
        try:
            await enqueue_job(
                db,
                job_type="autopilot.daily_briefing",
                payload={},
                idempotency_key=_bucket_key("autopilot.daily_briefing", 1440),
                priority=70,
                max_attempts=3,
            )
        except Exception:
            logger.exception("Failed to enqueue daily-briefing Autopilot job")


async def workflow_worker_job() -> None:
    if not settings.automation_enabled:
        return
    try:
        await worker_tick(limit=4)
    except Exception:
        logger.exception("Autopilot workflow worker tick failed")


async def workflow_watchdog_job() -> None:
    async with SessionLocal() as db:
        try:
            outcome = await recover_expired_leases(db)
            compacted = await compact_duplicate_dead_letters(db)
            if outcome["recovered"] or outcome["dead_lettered"] or compacted["superseded"]:
                logger.warning(
                    "Autopilot watchdog recovery: leases=%s duplicate_failures=%s",
                    outcome,
                    compacted,
                )
        except Exception:
            logger.exception("Autopilot workflow watchdog failed")


def start_scheduler() -> None:
    now = datetime.now()
    scheduler.add_job(
        gmail_enqueue_job,
        "interval",
        minutes=settings.gmail_sync_minutes,
        id="gmail_sync_enqueue",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        next_run_time=now,
    )
    scheduler.add_job(
        banking_enqueue_job,
        "interval",
        minutes=settings.bank_sync_minutes,
        id="bank_sync_enqueue",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        next_run_time=now,
    )
    scheduler.add_job(
        connector_rules_enqueue_job,
        "interval",
        minutes=1,
        id="connector_rules_enqueue",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        next_run_time=now,
    )
    scheduler.add_job(
        external_services_enqueue_job,
        "interval",
        minutes=settings.external_sync_minutes,
        id="external_services_enqueue",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        next_run_time=now,
    )
    scheduler.add_job(
        housekeeping_enqueue_job,
        "interval",
        hours=6,
        id="housekeeping_enqueue",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        next_run_time=now,
    )
    scheduler.add_job(
        provider_health_enqueue_job,
        "interval",
        minutes=5,
        id="autopilot_provider_health_enqueue",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        next_run_time=now,
    )
    scheduler.add_job(
        daily_briefing_enqueue_job,
        "interval",
        hours=24,
        id="autopilot_daily_briefing_enqueue",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        next_run_time=now,
    )
    scheduler.add_job(
        workflow_worker_job,
        "interval",
        seconds=5,
        id="autopilot_worker",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        next_run_time=now,
    )
    scheduler.add_job(
        workflow_watchdog_job,
        "interval",
        seconds=60,
        id="autopilot_watchdog",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        next_run_time=now,
    )
    scheduler.start()


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
