from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import get_settings
from app.models.entities import (
    BankAccount,
    BankConnection,
    BrowserPortal,
    Device,
    GmailMailboxState,
    OAuthConnection,
    PortalDocumentSource,
    ServiceConnector,
)
from app.models.fulfillment_entities import FulfillmentProvider
from app.services.runtime_config import get_runtime_value


def _cap(
    key: str,
    title: str,
    available: bool,
    executor: str,
    *,
    resolution: str = "automatic",
    detail: str = "",
    readiness: str | None = None,
    verified: bool | None = None,
    setup_action: str = "",
    setup_destination: str = "",
    setup_steps: tuple[str, ...] = (),
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "key": key,
        "title": title,
        "available": bool(available),
        "executor": executor,
        "resolution": resolution,
        "detail": detail,
        "readiness": readiness or ("live" if available else "offline"),
    }
    if verified is not None:
        row["verified"] = bool(verified)
    if setup_action or setup_destination or setup_steps:
        row["setup"] = {
            "action": setup_action,
            "destination": setup_destination,
            "steps": list(setup_steps),
        }
    return row


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
    gmail_verification_token = (await get_runtime_value(db, "google_pubsub_verification_token", "")).strip()
    gmail_state = (
        await db.execute(
            select(GmailMailboxState)
            .order_by(GmailMailboxState.updated_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    now = datetime.utcnow()
    gmail_watch_active = bool(
        gmail_state
        and gmail_state.watch_expiration_at is not None
        and gmail_state.watch_expiration_at > now + timedelta(minutes=5)
        and gmail_state.watch_topic == gmail_topic
    )
    gmail_push_observed = bool(gmail_state and gmail_state.last_push_at is not None)
    gmail_push_available = bool(
        google
        and gmail_topic
        and gmail_verification_token
        and gmail_watch_active
    )
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
    enabled_portal_ids = set(
        (
            await db.execute(
                select(BrowserPortal.id).where(BrowserPortal.enabled.is_(True))
            )
        ).scalars()
    )
    browser_portal = bool(enabled_portal_ids)
    portal_document_sources = list(
        (
            await db.execute(
                select(PortalDocumentSource).where(PortalDocumentSource.enabled.is_(True))
            )
        ).scalars()
    )
    portal_document_ready = bool(
        google and portal_document_sources and any(row.portal_id in enabled_portal_ids for row in portal_document_sources)
    )
    portal_document_live = bool(
        portal_document_ready
        and any(
            row.last_success_at is not None
            and row.last_success_at >= now - timedelta(minutes=max(1440, row.sync_interval_minutes * 2))
            for row in portal_document_sources
        )
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
    fulfillment_providers = list(
        (
            await db.execute(
                select(FulfillmentProvider).where(FulfillmentProvider.enabled.is_(True))
            )
        ).scalars()
    )
    fulfillment_provider = bool(fulfillment_providers)
    telephony_enabled = (await get_runtime_value(db, "telephony_enabled", "true")).lower() == "true"
    twilio_configured = bool(
        await get_runtime_value(db, "twilio_account_sid", "")
        and await get_runtime_value(db, "twilio_auth_token", "")
        and await get_runtime_value(db, "twilio_from_number", "")
    )
    telephony_live = bool(telephony_enabled and twilio_configured and ai)
    fulfillment_browser_executor = any(
        provider.browser_portal_id in enabled_portal_ids
        for provider in fulfillment_providers
        if provider.browser_portal_id is not None
    )
    fulfillment_phone_executor = bool(
        telephony_live
        and any(bool(provider.support_phone_encrypted) for provider in fulfillment_providers)
    )
    fulfillment_executor_ready = bool(
        fulfillment_provider and (fulfillment_browser_executor or fulfillment_phone_executor)
    )

    if not google:
        gmail_push_detail = "Connect Google OAuth before Gmail push notifications can run"
    elif not gmail_topic:
        gmail_push_detail = "Configure the full Google Pub/Sub topic name in Services"
    elif not gmail_verification_token:
        gmail_push_detail = "Configure a Pub/Sub verification token in Services"
    elif not gmail_watch_active:
        gmail_push_detail = "The Gmail watch is not active for the configured topic; activate/renew it"
    elif not gmail_push_observed:
        gmail_push_detail = "Gmail accepted the watch; no Pub/Sub delivery has been observed yet"
    else:
        gmail_push_detail = "Gmail watch is active and Pub/Sub delivery has been observed"

    if not fulfillment_provider:
        fulfillment_detail = "Configure at least one enabled fulfillment provider"
    elif not fulfillment_executor_ready:
        fulfillment_detail = (
            "Enabled providers exist, but none is linked to an enabled browser portal "
            "or to a support phone with live telephony"
        )
    else:
        fulfillment_detail = (
            "At least one enabled provider has a real linked browser or telephone executor"
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
            setup_action="services",
            setup_destination="Services → Google Gmail, Calendar, Drive and Contacts",
            setup_steps=(
                "Configure the Google OAuth Web client in Services.",
                "Authorize the Google account used by the VA.",
                "Use Test in Services to confirm the Google APIs are reachable.",
            ),
        ),
        _cap(
            "gmail_push",
            "Real-time Gmail change notifications",
            gmail_push_available,
            "Gmail watch + Google Cloud Pub/Sub",
            resolution="user_connect" if not gmail_push_available else "automatic",
            detail=gmail_push_detail,
            readiness=(
                "live"
                if gmail_push_available and gmail_push_observed
                else "ready"
                if gmail_push_available
                else "offline"
            ),
            verified=gmail_push_observed,
            setup_action="gmail_push",
            setup_destination="Services → Google + Google Cloud Pub/Sub",
            setup_steps=(
                "Connect Google OAuth in Services.",
                "Create a Pub/Sub topic and grant gmail-api-push@system.gserviceaccount.com the Pub/Sub Publisher role on that topic.",
                "Enter the full projects/.../topics/... topic name and a private verification token in Services.",
                "Create a Google Cloud push subscription to {{backend}}/api/google/pubsub?token=<the same verification token>.",
                "Tap Activate Gmail watch below; VAAPP only reports LIVE after the watch is active, and marks delivery verified after a real Pub/Sub notification is observed.",
            ),
        ),
        _cap(
            "calendar",
            "Calendar read/write",
            google,
            "Google Calendar API",
            resolution="user_connect" if not google else "automatic",
            detail="Google OAuth connection required" if not google else "Connected Google account",
            setup_action="services",
            setup_destination="Services → Google Gmail, Calendar, Drive and Contacts",
            setup_steps=(
                "Connect the Google OAuth account in Services.",
                "Use the Google service Test action to verify Calendar access.",
            ),
        ),
        _cap(
            "contacts",
            "Contacts sync",
            google,
            "Google People API",
            resolution="user_connect" if not google else "automatic",
            detail="Google OAuth connection required" if not google else "Connected Google account",
            setup_action="services",
            setup_destination="Services → Google Gmail, Calendar, Drive and Contacts",
            setup_steps=("Connect Google OAuth in Services; People API access is checked through the same connection.",),
        ),
        _cap(
            "documents",
            "Drive document storage",
            google,
            "Google Drive API",
            resolution="user_connect" if not google else "automatic",
            detail="Google OAuth connection required" if not google else "Connected Google account",
            setup_action="services",
            setup_destination="Services → Google Gmail, Calendar, Drive and Contacts",
            setup_steps=("Connect Google OAuth in Services; Drive access is checked through the same connection.",),
        ),
        _cap(
            "portal_document_sync",
            "Portal document synchronization",
            portal_document_ready,
            "Secure Playwright discovery + shared Drive document ingestion",
            resolution="automatic" if portal_document_ready else "user_connect",
            detail=(
                "At least one configured source has completed a real authenticated sync"
                if portal_document_live
                else "A source and Google Drive are configured; run Test/Sync with the real account to verify it"
                if portal_document_ready
                else "Connect Google Drive and configure an enabled document source on an allowlisted browser portal"
            ),
            readiness="live" if portal_document_live else "ready" if portal_document_ready else "offline",
            verified=portal_document_live,
            setup_action="portal_documents",
            setup_destination="Work → Documents → Portal sources",
            setup_steps=(
                "Configure an allowlisted Secure Browser portal and its encrypted credentials.",
                "Add and validate a declarative document source recipe.",
                "Run Test, then Sync now; MFA/CAPTCHA remains a truthful Needs You boundary.",
            ),
        ),
        _cap(
            "banking_read",
            "Bank account synchronization",
            bank_connected and enable_banking_configured,
            "Enable Banking",
            resolution="user_connect" if not (bank_connected and enable_banking_configured) else "automatic",
            detail=(
                "Active bank consent and Enable Banking application credentials required"
                if not (bank_connected and enable_banking_configured)
                else "Enable Banking consent and account synchronization are available"
            ),
            setup_action="services",
            setup_destination="Services → Enable Banking, then Money → Accounts",
            setup_steps=(
                "Configure the Enable Banking application ID and private key in Services.",
                "Connect the required bank consent.",
                "Verify the synchronized account in Money → Accounts.",
            ),
        ),
        _cap(
            "banking_payments",
            "Policy-bound bank payment initiation",
            bank_connected and payment_account and enable_banking_configured,
            "Enable Banking PIS",
            resolution="user_connect" if not (bank_connected and payment_account and enable_banking_configured) else "automatic",
            detail=(
                "A payment-enabled account and live bank consent are required"
                if not (bank_connected and payment_account and enable_banking_configured)
                else "At least one synchronized account is explicitly payment-enabled"
            ),
            setup_action="services",
            setup_destination="Services → Enable Banking, then Money → Accounts",
            setup_steps=(
                "Connect Enable Banking and a live bank consent.",
                "Open Money → Accounts and explicitly enable approved automatic payments on the source account.",
                "Keep the account safety reserve configured; payment policy remains enforced.",
            ),
        ),
        _cap(
            "financial_forecasting",
            "Financial allocation and conservative cash forecasting",
            bank_connected and enable_banking_configured,
            "VAAPP forecast ledger + Enable Banking cash evidence",
            resolution="user_connect" if not (bank_connected and enable_banking_configured) else "automatic",
            detail=(
                "Active bank consent and Enable Banking credentials are required"
                if not (bank_connected and enable_banking_configured)
                else "90-day source-backed forecast and same-scope surplus allocation are active"
            ),
        ),
        _cap(
            "ai_decisioning",
            "AI decision engine",
            ai,
            "Configured OpenAI-compatible provider",
            resolution="user_connect" if not ai else "automatic",
            detail="AI credentials/model required" if not ai else "Configured",
            setup_action="services",
            setup_destination="Services → AI decision engine",
            setup_steps=(
                "Configure the provider base URL, model and API credential in Services.",
                "Use Test to verify the selected model before unattended execution.",
            ),
        ),
        _cap(
            "service_connectors",
            "Connected service actions",
            generic_connector,
            "VAAPP service connector runtime",
            resolution="user_connect" if not generic_connector else "automatic",
            detail=(
                "At least one live/configured service connector required"
                if not generic_connector
                else "At least one service connector is configured"
            ),
            setup_action="services",
            setup_destination="Services → Service catalog / Universal connectors",
            setup_steps=(
                "Choose a built-in service integration or add a universal connector.",
                "Configure the provider credential and run its Test action.",
            ),
        ),
        _cap(
            "browser_portal",
            "Secure browser portal execution",
            browser_portal,
            "Playwright Chromium portal operator",
            resolution="user_connect" if not browser_portal else "automatic",
            detail=(
                "Configure at least one allowlisted browser portal"
                if not browser_portal
                else "At least one enabled HTTPS portal is allowlisted for the real Chromium executor"
            ),
            setup_action="browser_portals",
            setup_destination="Work → Portals",
            setup_steps=(
                "Add the real HTTPS portal the VA is allowed to operate.",
                "Keep the allowlist limited to the provider and legitimate authentication hosts.",
                "Add encrypted credentials only when that provider actually requires login; OTP/MFA remains one-time.",
            ),
        ),
        _cap(
            "document_form_automation",
            "Document forms and deadline ownership",
            google,
            "Drive document intelligence + VAAPP workflow engine + secure browser",
            resolution="user_connect" if not google else "automatic",
            detail="Google OAuth is required to read VA-managed Drive documents" if not google else (
                "Document/deadline extraction is active; matching configured portals are used for form execution"
                if browser_portal
                else "Document/deadline extraction is active; forms wait for a matching allowlisted portal"
            ),
        ),
        _cap(
            "fulfillment_automation",
            "Purchasing, travel, logistics and customer-service ownership",
            fulfillment_executor_ready,
            "VAAPP fulfillment ledger + secure browser/telephony executors",
            resolution="automatic" if fulfillment_executor_ready else "user_connect",
            detail=fulfillment_detail,
            setup_action="fulfillment",
            setup_destination="Fulfillment → Configured providers",
            setup_steps=(
                "Create or edit an enabled fulfillment provider.",
                "Link that provider to an enabled Secure Browser portal, or configure its support phone while autonomous telephony is LIVE.",
                "For logistics, use a tracking recipe/template that observes carrier state; a page visit alone is never completion evidence.",
            ),
        ),
        _cap(
            "telephony_call",
            "Autonomous PSTN telephone calls",
            telephony_live,
            "Twilio Programmable Voice + VAAPP voice decision engine",
            resolution="user_connect" if not telephony_live else "automatic",
            detail=(
                "Twilio Account SID/Auth Token/caller number and the AI decision engine are required"
                if not telephony_live
                else "Signed voice/status webhooks, encrypted transcripts, bounded retries, and counterparty verification are active"
            ),
            setup_action="telephony",
            setup_destination="Calls / Services → Telephony",
            setup_steps=(
                "Configure Twilio Account SID, Auth Token and caller number.",
                "Keep the AI decision engine configured.",
                "Use the Calls workspace for real outbound objectives; material commitments remain policy-bound.",
            ),
        ),
        _cap(
            "sms_send",
            "Android SMS send",
            device,
            "Android SmsManager",
            resolution="user_connect" if not device else "automatic",
            detail="A recently paired Android device is required; SEND_SMS permission is enforced on-device at execution time",
            setup_action="communications",
            setup_destination="Services → Communications Autopilot",
            setup_steps=(
                "Keep this Android device paired with the backend.",
                "Grant SMS read/send/receive permissions and the default SMS role where required.",
                "Run phone history/policy sync and resolve any backend error shown by the app.",
            ),
        ),
        _cap(
            "notification_reply",
            "Messaging-app notification replies",
            device,
            "Android RemoteInput",
            resolution="user_connect" if not device else "automatic",
            detail="Replies require a live notification that exposes a RemoteInput action; this does not claim arbitrary message initiation",
            setup_action="communications",
            setup_destination="Services → Communications Autopilot",
            setup_steps=(
                "Grant notification access to VAAPP.",
                "Messaging-app history is not imported; only new notifications with a RemoteInput reply action can be answered automatically.",
            ),
        ),
        _cap(
            "android_device",
            "Android device bridge",
            device,
            "Paired Android device",
            resolution="user_connect" if not device else "automatic",
            detail="A paired device must have checked in during the last 24 hours",
            setup_action="communications",
            setup_destination="Services → Communications Autopilot",
            setup_steps=(
                "Keep the Android app paired with the current backend.",
                "Refresh or run a phone sync so the device check-in remains current.",
            ),
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
