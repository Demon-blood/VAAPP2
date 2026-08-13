from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta
from typing import Any

from googleapiclient.errors import HttpError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.google_api import (
    _google_http_status,
    get_gmail_message,
    get_gmail_profile,
    get_google_connection,
    list_gmail_history_added_message_ids,
    start_gmail_watch,
)
from app.models.entities import GmailMailboxState
from app.services.action_reconciler import reconcile_action_queue
from app.services.audit import write_audit
from app.services.email_processor import sync_gmail, process_single_message
from app.services.runtime_config import get_runtime_value


def utcnow() -> datetime:
    return datetime.utcnow()


def _expiration_datetime(value: Any) -> datetime | None:
    try:
        millis = int(str(value or "0"))
    except (TypeError, ValueError):
        return None
    if millis <= 0:
        return None
    return datetime.fromtimestamp(millis / 1000)


async def mailbox_state(db: AsyncSession) -> GmailMailboxState:
    connection = await get_google_connection(db)
    key = connection.account_key.lower()
    row = await db.get(GmailMailboxState, key)
    if row is None:
        row = GmailMailboxState(account_key=key)
        db.add(row)
        await db.flush()
    return row


async def refresh_mailbox_cursor_from_profile(
    db: AsyncSession,
    *,
    mark_full_sync: bool = False,
) -> GmailMailboxState:
    row = await mailbox_state(db)
    profile = await get_gmail_profile(db)
    profile_email = str(profile.get("emailAddress") or row.account_key).lower()
    if profile_email and profile_email != row.account_key:
        raise RuntimeError("Gmail profile does not match the connected Google account")
    history_id = str(profile.get("historyId") or "")
    if history_id:
        row.history_id = history_id
    if mark_full_sync:
        row.last_full_sync_at = utcnow()
    row.last_history_sync_at = utcnow()
    row.last_error = ""
    await db.commit()
    return row


async def ensure_gmail_watch(db: AsyncSession, *, force: bool = False) -> dict[str, Any]:
    topic = (await get_runtime_value(db, "google_pubsub_topic", "")).strip()
    if not topic:
        raise RuntimeError("Google Pub/Sub topic is not configured")
    row = await mailbox_state(db)
    now = utcnow()
    # Gmail requires renewal at least every seven days and recommends daily renewal.
    if (
        not force
        and row.watch_expiration_at is not None
        and row.watch_expiration_at > now + timedelta(hours=24)
        and row.watch_topic == topic
    ):
        return {
            "renewed": False,
            "account_key": row.account_key,
            "history_id": row.history_id,
            "expiration": int(row.watch_expiration_at.timestamp() * 1000),
            "topic": row.watch_topic,
        }

    result = await start_gmail_watch(db, topic)
    history_id = str(result.get("historyId") or "")
    expiration = _expiration_datetime(result.get("expiration"))
    if not history_id or expiration is None:
        raise RuntimeError("Gmail watch response did not include a valid history cursor and expiration")
    row.history_id = history_id
    row.watch_topic = topic
    row.watch_expiration_at = expiration
    row.last_watch_renewed_at = now
    row.last_history_sync_at = now
    row.last_error = ""
    await write_audit(
        db,
        "gmail_watch_renewed",
        entity_type="gmail_mailbox",
        entity_id=row.account_key,
        details={"history_id": history_id, "expiration": expiration, "topic": topic},
    )
    await db.commit()
    return {
        "renewed": True,
        "account_key": row.account_key,
        "history_id": row.history_id,
        "expiration": int(expiration.timestamp() * 1000),
        "topic": topic,
    }


async def full_recovery_sync(db: AsyncSession, *, max_messages: int = 500) -> dict[str, Any]:
    processed = await sync_gmail(db, max_messages=max_messages)
    row = await refresh_mailbox_cursor_from_profile(db, mark_full_sync=True)
    await write_audit(
        db,
        "gmail_recovery_sync_completed",
        entity_type="gmail_mailbox",
        entity_id=row.account_key,
        details={"processed": processed, "history_id": row.history_id},
    )
    await db.commit()
    return {"mode": "full_recovery", "processed": processed, "history_id": row.history_id}


async def history_sync(
    db: AsyncSession,
    *,
    notification_history_id: str = "",
    notification_email: str = "",
) -> dict[str, Any]:
    row = await mailbox_state(db)
    now = utcnow()
    notification_email = notification_email.strip().lower()
    if notification_email and notification_email != row.account_key:
        raise RuntimeError("Pub/Sub notification account does not match the connected Gmail account")
    row.last_push_at = now if notification_history_id else row.last_push_at

    if not row.history_id:
        return await full_recovery_sync(db)

    start_history_id = row.history_id
    try:
        message_ids, newest_history_id = await list_gmail_history_added_message_ids(
            db,
            start_history_id=start_history_id,
            label_id="INBOX",
        )
    except HttpError as exc:
        if _google_http_status(exc) == 404:
            # Gmail documents a stale/out-of-date history cursor as requiring a full sync.
            row.last_error = "Gmail history cursor expired; performing full recovery sync"
            await db.commit()
            return await full_recovery_sync(db)
        row.last_error = str(exc)[:8000]
        await db.commit()
        raise

    processed = 0
    for message_id in message_ids:
        message = await get_gmail_message(db, message_id, format="full")
        labels = set(message.get("labelIds") or [])
        if "INBOX" not in labels:
            continue
        await process_single_message(db, message)
        processed += 1

    await reconcile_action_queue(db)
    row.history_id = str(newest_history_id or notification_history_id or row.history_id)
    row.last_history_sync_at = now
    row.last_error = ""
    await write_audit(
        db,
        "gmail_history_sync_completed",
        entity_type="gmail_mailbox",
        entity_id=row.account_key,
        details={
            "start_history_id": start_history_id,
            "notification_history_id": notification_history_id,
            "processed": processed,
            "message_ids_seen": len(message_ids),
        },
    )
    await db.commit()
    return {
        "mode": "history",
        "processed": processed,
        "message_ids_seen": len(message_ids),
        "history_id": row.history_id,
    }


def decode_pubsub_notification(body: dict[str, Any]) -> dict[str, str]:
    message = body.get("message") if isinstance(body, dict) else None
    if not isinstance(message, dict):
        raise ValueError("Pub/Sub payload is missing message")
    data = str(message.get("data") or "")
    if not data:
        raise ValueError("Pub/Sub payload is missing message.data")
    padding = "=" * ((4 - len(data) % 4) % 4)
    try:
        decoded = base64.urlsafe_b64decode(data + padding).decode("utf-8")
        payload = json.loads(decoded)
    except Exception as exc:
        raise ValueError("Pub/Sub Gmail notification data is invalid") from exc
    if not isinstance(payload, dict):
        raise ValueError("Pub/Sub Gmail notification data is not an object")
    email = str(payload.get("emailAddress") or "").strip().lower()
    history_id = str(payload.get("historyId") or "").strip()
    if not email or not history_id:
        raise ValueError("Pub/Sub Gmail notification is missing emailAddress/historyId")
    return {
        "email": email,
        "history_id": history_id,
        "pubsub_message_id": str(message.get("messageId") or ""),
        "publish_time": str(message.get("publishTime") or ""),
    }


async def mailbox_status(db: AsyncSession) -> dict[str, Any]:
    row = await mailbox_state(db)
    return {
        "account_key": row.account_key,
        "history_id": row.history_id,
        "watch_topic_configured": bool(row.watch_topic),
        "watch_expiration_at": row.watch_expiration_at,
        "last_push_at": row.last_push_at,
        "last_history_sync_at": row.last_history_sync_at,
        "last_full_sync_at": row.last_full_sync_at,
        "last_watch_renewed_at": row.last_watch_renewed_at,
        "last_error": row.last_error,
    }
