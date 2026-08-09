import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.core.database import SessionLocal, init_db
from app.core.version import APP_VERSION
from app.core.settings import get_settings
from app.services.action_reconciler import reconcile_action_queue
from app.services.scheduler import start_scheduler, stop_scheduler

settings = get_settings()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_db()
    # Repair any older action flags immediately after an upgrade so the Today cards
    # never show an orphaned counter without a concrete task behind it.
    try:
        async with SessionLocal() as db:
            await reconcile_action_queue(db)
    except Exception:
        logger.exception("Initial action-queue reconciliation failed")
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
