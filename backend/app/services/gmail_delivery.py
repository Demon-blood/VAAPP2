from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Any

from googleapiclient.errors import HttpError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.google_api import (
    _google_http_status,
    find_gmail_message_by_rfc_message_id,
    get_gmail_message,
    headers_to_dict,
    send_gmail_message,
)
from app.models.entities import GmailOutboundMessage
from app.services.audit import write_audit
from app.services.workflow_engine import failure_recovery_class


def utcnow() -> datetime:
    return datetime.utcnow()


def deterministic_rfc_message_id(idempotency_key: str) -> str:
    digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:40]
    return f"<vaapp-{digest}@full-time-va.local>"


async def prepare_gmail_outbound(
    db: AsyncSession,
    *,
    idempotency_key: str,
    recipient: str,
    subject: str,
    body: str,
    objective_id: int | None = None,
    step_id: int | None = None,
    source_message_id: str = "",
    gmail_thread_id: str = "",
    in_reply_to: str = "",
    references: str = "",
) -> GmailOutboundMessage:
    existing = (
        await db.execute(
            select(GmailOutboundMessage)
            .where(GmailOutboundMessage.idempotency_key == idempotency_key)
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    row = GmailOutboundMessage(
        idempotency_key=idempotency_key[:255],
        objective_id=objective_id,
        step_id=step_id,
        source_message_id=source_message_id[:255],
        gmail_thread_id=gmail_thread_id[:255],
        recipient=recipient.strip(),
        subject=subject.strip(),
        body=body,
        rfc_message_id=deterministic_rfc_message_id(idempotency_key),
        in_reply_to=in_reply_to.strip(),
        references=references.strip(),
        status="prepared",
        verify_after=utcnow(),
    )
    db.add(row)
    await db.flush()
    await write_audit(
        db,
        "gmail_outbound_prepared",
        entity_type="gmail_outbound_message",
        entity_id=str(row.id),
        details={
            "idempotency_key": row.idempotency_key,
            "objective_id": objective_id,
            "step_id": step_id,
            "source_message_id": source_message_id,
            "gmail_thread_id": gmail_thread_id,
            "recipient": recipient,
            "rfc_message_id": row.rfc_message_id,
        },
    )
    # The intent must be durable before Gmail is contacted.  A process crash after
    # this commit can safely resume by searching the deterministic RFC Message-ID.
    await db.commit()
    return row


def _message_matches(row: GmailOutboundMessage, message: dict[str, Any]) -> bool:
    labels = set(message.get("labelIds") or [])
    if "SENT" not in labels:
        return False
    payload = message.get("payload") or {}
    headers = headers_to_dict(payload)
    header_id = str(headers.get("message-id") or "").strip()
    return header_id.casefold() == row.rfc_message_id.casefold()


async def reconcile_gmail_outbound(db: AsyncSession, row: GmailOutboundMessage) -> bool:
    """Prove that the prepared outbound exists in Gmail Sent.

    Direct message-id lookup is preferred when Gmail returned an API id.  An RFC822
    Message-ID search is the ambiguity-safe fallback when a send request timed out
    after Gmail may already have accepted it.
    """
    message: dict[str, Any] | None = None
    if row.external_message_id:
        try:
            message = await get_gmail_message(db, row.external_message_id, format="metadata")
        except HttpError as exc:
            if _google_http_status(exc) != 404:
                raise
    if message is None or not _message_matches(row, message):
        message = await find_gmail_message_by_rfc_message_id(db, row.rfc_message_id, sent_only=True)
    if message is None or not _message_matches(row, message):
        return False

    row.external_message_id = str(message.get("id") or row.external_message_id)
    row.external_thread_id = str(message.get("threadId") or row.external_thread_id or row.gmail_thread_id)
    row.status = "verified"
    row.verified_at = row.verified_at or utcnow()
    row.sent_at = row.sent_at or row.verified_at
    row.last_error = ""
    await write_audit(
        db,
        "gmail_outbound_verified",
        entity_type="gmail_outbound_message",
        entity_id=str(row.id),
        details={
            "external_message_id": row.external_message_id,
            "external_thread_id": row.external_thread_id,
            "rfc_message_id": row.rfc_message_id,
        },
    )
    await db.commit()
    return True


async def send_or_reconcile_gmail_outbound(
    db: AsyncSession,
    row: GmailOutboundMessage,
) -> GmailOutboundMessage:
    if row.status == "verified":
        return row

    # Every attempt starts with a real Gmail postcondition check.  This is what
    # prevents a timeout/worker restart from sending the same reply twice.
    try:
        if await reconcile_gmail_outbound(db, row):
            return row
    except Exception as exc:
        # A verification provider outage must not cause a new send attempt.
        row.last_error = f"verification failed before send: {exc}"[:8000]
        row.verify_after = utcnow() + timedelta(minutes=2)
        await db.commit()
        return row

    now = utcnow()
    # Once a Gmail POST may have reached the provider, absence from a search result is
    # not proof that the provider rejected it.  Never submit this external intent a
    # second time.  Reconciliation-only is the safe state for both an explicit Gmail
    # message id (sent_unverified) and an ambiguous network/provider outcome.
    if row.status in {"creation_uncertain", "sent_unverified"}:
        row.verify_after = max(row.verify_after, now + timedelta(minutes=2))
        await db.commit()
        return row
    if row.status == "failed_uncertain":
        # Historical v1.0.12 rows are reconciliation-only. Never turn this legacy
        # state into permission to submit the same provider intent again.
        row.status = "creation_uncertain"
        row.verify_after = max(row.verify_after, now + timedelta(minutes=2))
        row.last_error = (
            "Historical Gmail delivery ambiguity returned to provider reconciliation; "
            "automatic resend remains disabled"
        )
        await db.commit()
        return row
    if row.attempts >= row.max_attempts:
        row.status = "failed"
        row.last_error = row.last_error or "Gmail outbound intent has already used its one safe provider submission"
        await db.commit()
        return row
    if not row.recipient.strip() or not row.body.strip():
        row.status = "failed"
        row.last_error = "Gmail recipient and body are required"
        await db.commit()
        return row

    row.attempts += 1
    row.status = "sending"
    row.verify_after = now + timedelta(minutes=2)
    await db.commit()

    try:
        external_id = await send_gmail_message(
            db,
            to=row.recipient,
            subject=row.subject,
            body=row.body,
            reply_to_id=row.in_reply_to or None,
            thread_id=row.gmail_thread_id or None,
            message_id_header=row.rfc_message_id,
            references=row.references or None,
        )
        row.external_message_id = external_id
        row.status = "sent_unverified"
        row.sent_at = utcnow()
        row.last_error = ""
        await db.commit()
        # Verify immediately when possible; if Gmail metadata is not visible yet the
        # core verifier will retry without resending during verify_after.
        try:
            await reconcile_gmail_outbound(db, row)
        except Exception as exc:
            row.last_error = f"sent but verification pending: {exc}"[:8000]
            row.verify_after = utcnow() + timedelta(minutes=2)
            await db.commit()
        return row
    except Exception as exc:
        status = _google_http_status(exc) if isinstance(exc, HttpError) else 0
        recovery = failure_recovery_class("gmail.send", str(exc))
        row.last_error = str(exc)[:8000]

        # Authentication/permission/definitive request errors are not ambiguous
        # delivery outcomes.  Surface them to the objective engine rather than
        # silently attempting a different message.
        if recovery == "user_required" or (status and 400 <= status < 500 and status not in {408, 409, 425, 429}):
            row.status = "failed_user" if recovery == "user_required" else "failed"
            await db.commit()
            raise

        # Timeouts, connection resets, 429 and 5xx can happen after Gmail accepted
        # the request. Never blind-retry this provider intent. Later cycles only
        # reconcile Sent by the stable RFC Message-ID; unresolved ambiguity remains
        # VA-owned instead of creating a duplicate message.
        row.status = "creation_uncertain"
        row.verify_after = utcnow() + timedelta(minutes=2)
        await write_audit(
            db,
            "gmail_outbound_creation_uncertain",
            entity_type="gmail_outbound_message",
            entity_id=str(row.id),
            result="deferred",
            details={"error": str(exc), "attempt": row.attempts, "recovery_class": recovery, "http_status": status},
        )
        await db.commit()
        return row


def _gmail_uncertain_verify_delay(row: GmailOutboundMessage, now: datetime) -> timedelta:
    age = now - row.created_at
    if age < timedelta(minutes=30):
        return timedelta(minutes=2)
    if age < timedelta(hours=24):
        return timedelta(minutes=15)
    if age < timedelta(days=7):
        return timedelta(hours=1)
    return timedelta(hours=6)


async def ensure_gmail_outbound_verified(db: AsyncSession, row: GmailOutboundMessage) -> bool:
    if row.status == "verified":
        return True
    now = utcnow()

    # v1.0.12 used failed_uncertain as a terminal timeout after thirty minutes.
    # It is historical input only now: provider ambiguity stays VA-owned and the
    # stable RFC Message-ID remains the sole recovery key. No provider re-POST.
    if row.status == "failed_uncertain":
        row.status = "creation_uncertain"
        row.last_error = (
            "Historical Gmail delivery ambiguity returned to provider reconciliation; "
            "automatic resend remains disabled"
        )

    try:
        if await reconcile_gmail_outbound(db, row):
            return True
    except Exception as exc:
        row.last_error = f"verification failed: {exc}"[:8000]
        delay = (
            _gmail_uncertain_verify_delay(row, now)
            if row.status in {"creation_uncertain", "sent_unverified"}
            else timedelta(minutes=2)
        )
        row.verify_after = now + delay
        await db.commit()
        return False

    if row.status in {"creation_uncertain", "sent_unverified"}:
        # Provider execution may already have happened. Never re-POST. The fast
        # verification window becomes a bounded long-term reconciliation cadence
        # instead of an arbitrary terminal failure. Late Sent evidence can still
        # complete the original durable objective days later.
        row.verify_after = now + _gmail_uncertain_verify_delay(row, now)
        if now - row.created_at >= timedelta(minutes=30):
            row.last_error = (
                "Gmail send outcome remains ambiguous; VAAPP will continue provider reconciliation "
                "using the stable RFC Message-ID without resending this provider intent"
            )
        await db.commit()
        return False

    if row.status == "prepared" and row.verify_after <= now:
        await send_or_reconcile_gmail_outbound(db, row)
        return row.status == "verified"
    return False
