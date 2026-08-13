import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.api.telephony_routes import router as telephony_router
from app.api_autopilot import router as autopilot_router
from app.core.database import SessionLocal, init_db
from app.core.version import APP_VERSION
from app.core.settings import get_settings
from app.services.action_reconciler import reconcile_action_queue
from app.services.bank_statement_import import reconcile_statement_transactions_with_bank
from app.services.financial_reconciliation import reclassify_existing_nonpayable_bills
from app.services.financial_autopilot import (
    recategorize_bank_transaction_history,
    repair_legacy_account_scopes,
    repair_v080_default_account_roles,
)
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
    # Scope is ownership (Personal/Pro); Reserve is an account role. Repair the
    # legacy Android option that could store `reserve` as an ownership scope.
    try:
        async with SessionLocal() as db:
            repaired_scopes = await repair_legacy_account_scopes(db)
            if repaired_scopes:
                logger.warning("Legacy bank account scope repair: %s account(s)", repaired_scopes)
    except Exception:
        logger.exception("Initial bank-account scope repair failed")
    # Repair only untouched auto-seeded Revolut policies between Personal spending
    # and Pro operating roles. Explicit user edits remain authoritative.
    try:
        async with SessionLocal() as db:
            migrated_roles = await repair_v080_default_account_roles(db)
            if migrated_roles:
                logger.warning("v0.8 account-role migration: %s policy/policies", migrated_roles)
    except Exception:
        logger.exception("Initial v0.8 account-role migration failed")
    # Re-evaluate stored Enable Banking rows under the current deterministic category
    # rules. This repairs historical rows (for example irregular Google Play purchases)
    # without touching amounts, dates, or provider identities.
    try:
        async with SessionLocal() as db:
            recategorized = await recategorize_bank_transaction_history(db)
            if recategorized["changed"]:
                logger.warning("Bank transaction category repair: %s", recategorized)
    except Exception:
        logger.exception("Initial bank-transaction recategorization failed")
    # Re-attach imported statement history to any accounts connected after import,
    # re-apply current historical categories, and reconcile duplicates against the
    # live Enable Banking ledger without making any provider call.
    try:
        async with SessionLocal() as db:
            statement_reconciliation = await reconcile_statement_transactions_with_bank(db)
            if statement_reconciliation["matched"] or statement_reconciliation["attached_accounts"] or statement_reconciliation["recategorized"]:
                logger.warning("Historical statement reconciliation: %s", statement_reconciliation)
    except Exception:
        logger.exception("Initial historical-statement reconciliation failed")
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
app.include_router(telephony_router)
