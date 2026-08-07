from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.core.database import SessionLocal
from app.core.settings import get_settings
from app.services.automation_engine import run_connector_automation_rules
from app.services.banking_service import auto_pay_eligible_bills, refresh_all_payments, sync_all_banks
from app.services.email_processor import sync_gmail
from app.services.operations_service import sync_google_contacts

logger = logging.getLogger(__name__)
settings = get_settings()
scheduler = AsyncIOScheduler(timezone=settings.default_timezone)


async def gmail_job() -> None:
    if not settings.automation_enabled:
        return
    async with SessionLocal() as db:
        try:
            await sync_gmail(db, max_messages=250)
        except Exception:
            logger.exception("Gmail automation job failed")


async def banking_job() -> None:
    if not settings.automation_enabled:
        return
    async with SessionLocal() as db:
        try:
            await sync_all_banks(db)
            callback_url = str(settings.public_base_url).rstrip("/") + "/api/banking/payment-callback"
            await auto_pay_eligible_bills(db, redirect_url=callback_url)
            await refresh_all_payments(db)
        except Exception:
            logger.exception("Banking automation job failed")


async def external_services_job() -> None:
    if not settings.automation_enabled:
        return
    async with SessionLocal() as db:
        try:
            await sync_google_contacts(db)
        except Exception:
            logger.exception("External service automation job failed")


async def connector_rules_job() -> None:
    if not settings.automation_enabled:
        return
    async with SessionLocal() as db:
        try:
            await run_connector_automation_rules(db)
        except Exception:
            logger.exception("Scheduled connector automation job failed")


def start_scheduler() -> None:
    scheduler.add_job(
        gmail_job,
        "interval",
        minutes=settings.gmail_sync_minutes,
        id="gmail_sync",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        banking_job,
        "interval",
        minutes=settings.bank_sync_minutes,
        id="bank_sync",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        connector_rules_job,
        "interval",
        minutes=1,
        id="connector_rules",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        external_services_job,
        "interval",
        minutes=settings.external_sync_minutes,
        id="external_services_sync",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
