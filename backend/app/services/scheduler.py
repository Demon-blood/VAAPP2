from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.core.database import SessionLocal
from app.core.settings import get_settings
from app.services.cash_safety import quarantine_stale_creation_intents
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


async def gmail_watch_enqueue_job() -> None:
    if not settings.automation_enabled:
        return
    async with SessionLocal() as db:
        try:
            from app.services.runtime_config import get_runtime_value

            topic = (await get_runtime_value(db, "google_pubsub_topic", "")).strip()
            if not topic:
                return
            await enqueue_job(
                db,
                job_type="gmail.watch.ensure",
                payload={},
                idempotency_key=_bucket_key("gmail.watch.ensure", 720),
                priority=18,
                max_attempts=5,
            )
        except Exception:
            logger.exception("Failed to enqueue Gmail watch-renewal job")


async def calendar_enqueue_job() -> None:
    if not settings.automation_enabled:
        return
    async with SessionLocal() as db:
        try:
            await enqueue_job(
                db,
                job_type="calendar.sync",
                payload={"days_back": 30, "days_forward": 365},
                idempotency_key=_bucket_key("calendar.sync", max(5, settings.external_sync_minutes)),
                priority=22,
                max_attempts=6,
            )
        except Exception:
            logger.exception("Failed to enqueue Calendar ownership sync job")


async def relationship_memory_enqueue_job() -> None:
    if not settings.automation_enabled:
        return
    async with SessionLocal() as db:
        try:
            await enqueue_job(
                db,
                job_type="relationship.reconcile",
                payload={},
                idempotency_key=_bucket_key("relationship.reconcile", max(5, settings.external_sync_minutes)),
                priority=65,
                max_attempts=5,
            )
        except Exception:
            logger.exception("Failed to enqueue relationship-memory reconciliation job")


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


async def va_core_enqueue_job() -> None:
    if not settings.automation_enabled:
        return
    async with SessionLocal() as db:
        try:
            from app.services.runtime_config import get_runtime_value

            enabled = (await get_runtime_value(db, "va_autonomous_core_enabled", "true")).lower() == "true"
            if not enabled:
                return
            await enqueue_job(
                db,
                job_type="va.core.cycle",
                payload={},
                idempotency_key=_bucket_key("va.core.cycle", 1),
                priority=12,
                max_attempts=5,
            )
        except Exception:
            logger.exception("Failed to enqueue autonomous-core job")


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


async def proactive_planner_enqueue_job() -> None:
    if not settings.automation_enabled:
        return
    async with SessionLocal() as db:
        try:
            await enqueue_job(
                db,
                job_type="autopilot.plan",
                payload={},
                idempotency_key=_bucket_key("autopilot.plan", 15),
                priority=15,
                max_attempts=5,
            )
        except Exception:
            logger.exception("Failed to enqueue proactive Autopilot planner job")


async def daily_briefing_enqueue_job() -> None:
    if not settings.automation_enabled:
        return
    async with SessionLocal() as db:
        try:
            from app.services.runtime_config import get_runtime_value

            enabled = (await get_runtime_value(db, "daily_briefing_enabled", "true")).lower() == "true"
            if not enabled:
                return
            try:
                delivery_hour = max(0, min(int(await get_runtime_value(db, "daily_briefing_hour_local", "19")), 23))
            except ValueError:
                delivery_hour = 19
            local_now = datetime.now(ZoneInfo(settings.default_timezone))
            if local_now.hour != delivery_hour:
                return
            await enqueue_job(
                db,
                job_type="autopilot.daily_briefing",
                payload={"local_date": local_now.date().isoformat(), "delivery_hour": delivery_hour},
                idempotency_key=f"autopilot.daily_briefing:{local_now.date().isoformat()}",
                priority=70,
                max_attempts=3,
            )
        except Exception:
            logger.exception("Failed to enqueue daily-briefing Autopilot job")


async def telephony_reconcile_job() -> None:
    if not settings.automation_enabled:
        return
    async with SessionLocal() as db:
        try:
            from app.services.telephony_service import reconcile_telephony

            await reconcile_telephony(db)
        except Exception:
            logger.exception("Failed to reconcile autonomous telephony calls")


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
            money = await quarantine_stale_creation_intents(db)
            if (
                outcome["recovered"]
                or outcome["dead_lettered"]
                or compacted["superseded"]
                or money["payments"]
                or money["transfers"]
            ):
                logger.warning(
                    "Autopilot watchdog recovery: leases=%s duplicate_failures=%s money_creation=%s",
                    outcome,
                    compacted,
                    money,
                )
        except Exception:
            logger.exception("Autopilot workflow watchdog failed")


def start_scheduler() -> None:
    # APScheduler is configured for the user's local timezone. Passing a naive
    # system-UTC datetime on Render makes an immediate job look two hours late
    # during CEST, so always hand APScheduler an aware local timestamp.
    now = datetime.now(ZoneInfo(settings.default_timezone))
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
        gmail_watch_enqueue_job,
        "interval",
        hours=12,
        id="gmail_watch_renew_enqueue",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        next_run_time=now,
    )
    scheduler.add_job(
        calendar_enqueue_job,
        "interval",
        minutes=max(5, settings.external_sync_minutes),
        id="calendar_sync_enqueue",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        next_run_time=now,
    )
    scheduler.add_job(
        relationship_memory_enqueue_job,
        "interval",
        minutes=max(5, settings.external_sync_minutes),
        id="relationship_memory_enqueue",
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
        va_core_enqueue_job,
        "interval",
        minutes=1,
        id="va_core_enqueue",
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
        proactive_planner_enqueue_job,
        "interval",
        minutes=15,
        id="autopilot_planner_enqueue",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        next_run_time=now,
    )
    # Poll hourly so the runtime-configured local delivery hour can change without a process restart.
    # The daily idempotency key guarantees exactly one durable briefing job per local calendar day.
    scheduler.add_job(
        daily_briefing_enqueue_job,
        "interval",
        hours=1,
        id="autopilot_daily_briefing_enqueue",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        next_run_time=now,
    )
    scheduler.add_job(
        telephony_reconcile_job,
        "interval",
        minutes=1,
        id="telephony_reconcile",
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
