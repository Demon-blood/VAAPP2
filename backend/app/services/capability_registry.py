from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import get_settings
from app.models.entities import BankAccount, BankConnection, BrowserPortal, Device, OAuthConnection, ServiceConnector
from app.services.runtime_config import get_runtime_value


def _cap(
    key: str,
    title: str,
    available: bool,
    executor: str,
    *,
    resolution: str = "automatic",
    detail: str = "",
) -> dict[str, Any]:
    return {
        "key": key,
        "title": title,
        "available": bool(available),
        "executor": executor,
        "resolution": resolution,
        "detail": detail,
    }


async def capability_matrix(db: AsyncSession) -> dict[str, Any]:
    """Report only executors that exist in the currently shipped application.

    Missing credentials/consent are represented as user-resolvable connection gaps.
    Features planned for later phases are deliberately absent rather than advertised
    as fake capabilities.
    """

    google = bool(
        (
            await db.execute(
                select(func.count(OAuthConnection.id)).where(
                    OAuthConnection.provider == "google",
                    OAuthConnection.enabled.is_(True),
                )
            )
        ).scalar_one()
    )
    bank_connected = bool(
        (
            await db.execute(
                select(func.count(BankConnection.id)).where(BankConnection.status == "active")
            )
        ).scalar_one()
    )
    payment_account = bool(
        (
            await db.execute(
                select(func.count(BankAccount.id)).where(BankAccount.enabled_for_payments.is_(True))
            )
        ).scalar_one()
    )
    ai = bool(await get_runtime_value(db, "ai_api_key", "") and await get_runtime_value(db, "ai_model", ""))
    gmail_topic = (await get_runtime_value(db, "google_pubsub_topic", "")).strip()
    generic_connector = bool(
        (
            await db.execute(
                select(func.count(ServiceConnector.id)).where(
                    ServiceConnector.enabled.is_(True),
                    ServiceConnector.status.in_(["live", "configured"]),
                )
            )
        ).scalar_one()
    )
    browser_portal = bool(
        (
            await db.execute(
                select(func.count(BrowserPortal.id)).where(BrowserPortal.enabled.is_(True))
            )
        ).scalar_one()
    )
    recent_device_cutoff = datetime.utcnow() - timedelta(hours=24)
    device = bool(
        (
            await db.execute(
                select(func.count(Device.id)).where(
                    Device.enabled.is_(True),
                    Device.last_seen_at.is_not(None),
                    Device.last_seen_at >= recent_device_cutoff,
                )
            )
        ).scalar_one()
    )
    settings = get_settings()
    runtime_bank_key = await get_runtime_value(db, "enable_banking_private_key_pem", "")
    enable_banking_configured = bool(
        await get_runtime_value(db, "enable_banking_application_id", "")
        and (runtime_bank_key or settings.enable_banking_key_exists)
    )

    rows = [
        _cap("workflow_engine", "Durable workflow execution", True, "VAAPP workflow engine"),
        _cap(
            "email",
            "Gmail read/send",
            google,
            "Google Gmail API",
            resolution="user_connect" if not google else "automatic",
            detail="Google OAuth connection required" if not google else "Connected Google account",
        ),
        _cap(
            "gmail_push",
            "Real-time Gmail change notifications",
            google and bool(gmail_topic),
            "Gmail watch + Google Cloud Pub/Sub",
            resolution="user_connect" if not (google and gmail_topic) else "automatic",
            detail="Google OAuth and a configured Pub/Sub topic are required" if not (google and gmail_topic) else "Durable history-sync notifications enabled",
        ),
        _cap(
            "calendar",
            "Calendar read/write",
            google,
            "Google Calendar API",
            resolution="user_connect" if not google else "automatic",
            detail="Google OAuth connection required" if not google else "Connected Google account",
        ),
        _cap(
            "contacts",
            "Contacts sync",
            google,
            "Google People API",
            resolution="user_connect" if not google else "automatic",
            detail="Google OAuth connection required" if not google else "Connected Google account",
        ),
        _cap(
            "documents",
            "Drive document storage",
            google,
            "Google Drive API",
            resolution="user_connect" if not google else "automatic",
            detail="Google OAuth connection required" if not google else "Connected Google account",
        ),
        _cap(
            "banking_read",
            "Bank account synchronization",
            bank_connected and enable_banking_configured,
            "Enable Banking",
            resolution="user_connect" if not (bank_connected and enable_banking_configured) else "automatic",
            detail="Active bank consent and Enable Banking application credentials required",
        ),
        _cap(
            "banking_payments",
            "Policy-bound bank payment initiation",
            bank_connected and payment_account and enable_banking_configured,
            "Enable Banking PIS",
            resolution="user_connect" if not (bank_connected and payment_account and enable_banking_configured) else "automatic",
            detail="A payment-enabled account and live bank consent are required",
        ),
        _cap(
            "ai_decisioning",
            "AI decision engine",
            ai,
            "Configured OpenAI-compatible provider",
            resolution="user_connect" if not ai else "automatic",
            detail="AI credentials/model required" if not ai else "Configured",
        ),
        _cap(
            "service_connectors",
            "Connected service actions",
            generic_connector,
            "VAAPP service connector runtime",
            resolution="user_connect" if not generic_connector else "automatic",
            detail="At least one live/configured service connector required",
        ),
        _cap(
            "browser_portal",
            "Secure browser portal execution",
            browser_portal,
            "Playwright Chromium portal operator",
            resolution="user_connect" if not browser_portal else "automatic",
            detail="Configure at least one allowlisted browser portal" if not browser_portal else "Encrypted portal/session executor configured",
        ),
        _cap(
            "sms_send",
            "Android SMS send",
            device,
            "Android SmsManager",
            resolution="user_connect" if not device else "automatic",
            detail="A recently paired Android device is required; SEND_SMS permission is enforced on-device at execution time",
        ),
        _cap(
            "notification_reply",
            "Messaging-app notification replies",
            device,
            "Android RemoteInput",
            resolution="user_connect" if not device else "automatic",
            detail="Replies require a live notification that exposes a RemoteInput action; this does not claim arbitrary message initiation",
        ),
        _cap(
            "android_device",
            "Android device bridge",
            device,
            "Paired Android device",
            resolution="user_connect" if not device else "automatic",
            detail="A paired device must have checked in during the last 24 hours",
        ),
    ]
    return {
        "available": sum(1 for row in rows if row["available"]),
        "total": len(rows),
        "capabilities": rows,
        "checked_at": datetime.utcnow().isoformat() + "Z",
    }


async def capability_for_key(db: AsyncSession, key: str) -> dict[str, Any] | None:
    matrix = await capability_matrix(db)
    return next((row for row in matrix["capabilities"] if row["key"] == key), None)
