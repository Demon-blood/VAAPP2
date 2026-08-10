import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.api_autopilot import router as autopilot_router
from app.core.database import SessionLocal, init_db
from app.core.version import APP_VERSION
from app.core.settings import get_settings
from app.services.action_reconciler import reconcile_action_queue
from app.services.financial_reconciliation import reclassify_existing_nonpayable_bills
from app.services.scheduler import start_scheduler, stop_scheduler
from app.services.operations_service import cleanup_low_value_documents
from app.services.workflow_engine import (
    compact_duplicate_dead_letters,
    recover_expired_leases,
    repair_v052_gmail_conflict_backlog,
    repair_v062_gmail_label_conflict_backlog,
)

settings = get_settings()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_db()
    # Repair durable jobs before new work is scheduled. This makes Render restarts resumable.
    try:
        async with SessionLocal() as db:
            await recover_expired_leases(db)
            legacy_backlog = await repair_v052_gmail_conflict_backlog(db)
            label_backlog = await repair_v062_gmail_label_conflict_backlog(db)
            compacted = await compact_duplicate_dead_letters(db)
            if legacy_backlog["superseded"] or label_backlog["superseded"] or compacted["superseded"]:
                logger.warning(
                    "Initial Autopilot exception repair: legacy_gmail_409=%s label_conflicts=%s duplicates=%s",
                    legacy_backlog,
                    label_backlog,
                    compacted,
                )
    except Exception:
        logger.exception("Initial Autopilot workflow recovery failed")
    # Correct historical receipts/paid confirmations that older classifiers may have
    # inserted as payable bills before reconciling the exception queue.
    try:
        async with SessionLocal() as db:
            outcome = await reclassify_existing_nonpayable_bills(db)
            if outcome["reclassified"]:
                logger.warning("Financial document reclassification: %s", outcome)
    except Exception:
        logger.exception("Initial financial-document reconciliation failed")
    # Repair any older action flags immediately after an upgrade so the Today cards
    # never show an orphaned counter without a concrete task behind it.
    try:
        async with SessionLocal() as db:
            await reconcile_action_queue(db)
    except Exception:
        logger.exception("Initial action-queue reconciliation failed")
    # Remove legacy low-value attachments such as generic Terms of Service files that
    # older versions may have archived before the retention policy was tightened.
    try:
        async with SessionLocal() as db:
            await cleanup_low_value_documents(db)
    except Exception:
        logger.exception("Initial document-retention cleanup failed")
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title=settings.app_name, version=APP_VERSION, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)
app.include_router(router)
app.include_router(autopilot_router)
