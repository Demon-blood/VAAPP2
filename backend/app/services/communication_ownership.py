from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import (
    CommunicationAction,
    CommunicationEvent,
    EmailMessage,
    VACommunicationThread,
    VAFollowUp,
)
from app.services.autonomous_core import record_event
from app.services.audit import write_audit


def utcnow() -> datetime:
    return datetime.utcnow()


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _loads(value: str | None) -> dict[str, Any]:
    try:
        decoded = json.loads(value or "{}")
        return decoded if isinstance(decoded, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


async def _thread(
    db: AsyncSession,
    *,
    channel: str,
    provider: str,
    thread_key: str,
    title: str,
    participant: str,
) -> VACommunicationThread:
    normalized_key = thread_key.strip()[:255]
    row = (
        await db.execute(
            select(VACommunicationThread).where(
                VACommunicationThread.channel == channel,
                VACommunicationThread.provider == provider,
                VACommunicationThread.thread_key == normalized_key,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = VACommunicationThread(
            channel=channel,
            provider=provider,
            thread_key=normalized_key,
            title=title,
            participant=participant,
            status="open",
            waiting_on="va",
            last_activity_at=utcnow(),
        )
        db.add(row)
        await db.flush()
    else:
        if title:
            row.title = title
        if participant:
            row.participant = participant
    return row


async def cancel_pending_followups_for_objective(db: AsyncSession, objective_id: int) -> int:
    rows = list(
        (
            await db.execute(
                select(VAFollowUp).where(
                    VAFollowUp.objective_id == objective_id,
                    VAFollowUp.status.in_(["pending", "due"]),
                )
            )
        ).scalars()
    )
    for row in rows:
        row.status = "cancelled"
    return len(rows)


async def register_email_inbound(
    db: AsyncSession,
    *,
    record: EmailMessage,
    message: dict[str, Any],
    message_id_header: str,
    decision: dict[str, Any],
    reply_plan: dict[str, Any] | None,
) -> VACommunicationThread:
    thread = await _thread(
        db,
        channel="email",
        provider="gmail",
        thread_key=record.thread_id or record.provider_message_id,
        title=record.subject,
        participant=record.sender,
    )
    received_at = record.received_at or utcnow()

    # An inbound response closes the previous waiting period before the new message
    # becomes its own piece of work.  The new message may still create a fresh
    # objective, so this does not equate "reply received" with "whole thread done".
    prior_objective_id = thread.objective_id if thread.waiting_on == "counterparty" else None
    if prior_objective_id:
        await cancel_pending_followups_for_objective(db, prior_objective_id)
        await record_event(
            db,
            event_key=f"gmail-thread:{thread.thread_key}:response:{record.provider_message_id}",
            source_type="gmail_thread",
            source_id=thread.thread_key,
            event_type="communication_response_received",
            title=f"Response received: {record.subject or thread.participant}",
            payload={
                "prior_objective_id": prior_objective_id,
                "thread_record_id": thread.id,
                "channel": "email",
                "provider": "gmail",
                "message_id": record.provider_message_id,
                "gmail_thread_id": record.thread_id,
                "participant": record.sender,
            },
            occurred_at=received_at,
        )

    thread.last_inbound_at = received_at
    thread.last_activity_at = received_at
    thread.last_message_ref = record.provider_message_id
    thread.waiting_on = "va"
    thread.status = "open"
    thread.next_follow_up_at = None
    thread.context_json = _dump(
        {
            **_loads(thread.context_json),
            "last_inbound_message_id": record.provider_message_id,
            "last_inbound_rfc_message_id": message_id_header,
            "last_decision_category": decision.get("category"),
            "last_decision_priority": decision.get("priority"),
        }
    )

    if reply_plan is not None:
        event, _ = await record_event(
            db,
            event_key=f"gmail-message:{record.provider_message_id}:reply-plan",
            source_type="email",
            source_id=record.provider_message_id,
            event_type="email_reply_planned",
            title=f"Reply: {record.subject or record.sender}",
            payload={
                "thread_record_id": thread.id,
                "gmail_thread_id": record.thread_id,
                "source_message_id": record.provider_message_id,
                "source_rfc_message_id": message_id_header,
                "sender": record.sender,
                "recipient": reply_plan.get("to") or record.sender,
                "subject": reply_plan.get("subject") or f"Re: {record.subject}",
                "body": reply_plan.get("body") or "",
                "category": decision.get("category") or record.category,
                "priority": decision.get("priority") or record.priority,
                "action_required": bool(decision.get("action_required")),
                "expect_reply": bool(reply_plan.get("expect_reply", decision.get("action_required", False))),
                "follow_up_hours": int(reply_plan.get("follow_up_hours") or 48),
                "reasoning_summary": decision.get("reasoning_summary") or "",
            },
            occurred_at=received_at,
        )
        thread.context_json = _dump({**_loads(thread.context_json), "last_va_event_id": event.id})
    elif bool(decision.get("action_required")):
        # The thread is still durably owned even when this phase does not yet have
        # the domain executor (for example a document or calendar action).
        await record_event(
            db,
            event_key=f"gmail-message:{record.provider_message_id}:actionable",
            source_type="email",
            source_id=record.provider_message_id,
            event_type="email_actionable",
            title=record.subject or f"Email from {record.sender}",
            payload={
                "thread_record_id": thread.id,
                "gmail_thread_id": record.thread_id,
                "sender": record.sender,
                "subject": record.subject,
                "category": decision.get("category") or record.category,
                "priority": decision.get("priority") or record.priority,
                "reasoning_summary": decision.get("reasoning_summary") or "",
            },
            occurred_at=received_at,
        )
    return thread


async def register_device_communication(
    db: AsyncSession,
    *,
    event: CommunicationEvent,
    action: CommunicationAction | None,
) -> VACommunicationThread:
    thread = await _thread(
        db,
        channel=event.channel,
        provider=event.provider,
        thread_key=event.thread_key or event.sender or event.external_id,
        title=event.sender or event.channel,
        participant=event.sender if event.direction == "incoming" else event.recipient,
    )
    occurred_at = event.occurred_at or event.created_at or utcnow()

    if event.direction == "incoming":
        prior_objective_id = thread.objective_id if thread.waiting_on == "counterparty" else None
        if prior_objective_id:
            await cancel_pending_followups_for_objective(db, prior_objective_id)
            await record_event(
                db,
                event_key=f"communication-thread:{thread.id}:response:{event.external_id}",
                source_type="communication_thread",
                source_id=str(thread.id),
                event_type="communication_response_received",
                title=f"Response received: {event.sender or event.channel}",
                payload={
                    "prior_objective_id": prior_objective_id,
                    "thread_record_id": thread.id,
                    "channel": event.channel,
                    "provider": event.provider,
                    "communication_event_id": event.id,
                    "participant": event.sender,
                },
                occurred_at=occurred_at,
            )
        thread.last_inbound_at = occurred_at
        thread.waiting_on = "va"
        thread.next_follow_up_at = None
    else:
        thread.last_outbound_at = occurred_at

    thread.last_activity_at = occurred_at
    thread.last_message_ref = event.external_id
    thread.status = "open"
    thread.context_json = _dump(
        {
            **_loads(thread.context_json),
            "last_communication_event_id": event.id,
            "last_category": event.category,
            "last_priority": event.priority,
        }
    )

    if action is not None:
        await record_event(
            db,
            event_key=f"communication-action:{action.id}:planned",
            source_type="communication_action",
            source_id=str(action.id),
            event_type="device_reply_planned",
            title=f"Reply: {event.sender or event.channel}",
            payload={
                "thread_record_id": thread.id,
                "communication_event_id": event.id,
                "communication_action_id": action.id,
                "channel": event.channel,
                "provider": event.provider,
                "target": action.target,
                "priority": event.priority,
                # Source sensitivity remains on CommunicationEvent.protected. A prior
                # exact user authorization only prevents a second approval loop for
                # this newly persisted executor action; it does not erase classification.
                "protected": bool(event.protected and not _loads(event.decision_json).get("specific_authorized")),
                "source_protected": event.protected,
                "expect_reply": bool(event.action_required),
                "follow_up_hours": 48,
            },
            occurred_at=occurred_at,
        )
    elif event.direction == "incoming" and event.action_required:
        await record_event(
            db,
            event_key=f"communication-event:{event.id}:actionable",
            source_type="communication_event",
            source_id=str(event.id),
            event_type="communication_actionable",
            title=f"Follow up: {event.sender or event.channel}",
            payload={
                "thread_record_id": thread.id,
                "communication_event_id": event.id,
                "channel": event.channel,
                "provider": event.provider,
                "priority": event.priority,
                "protected": event.protected,
                "requires_user_review": bool(_loads(event.decision_json).get("relationship_review_required")),
                "proposed_reply": str(_loads(event.decision_json).get("reply_text") or ""),
            },
            occurred_at=occurred_at,
        )
    return thread


async def link_thread_objective(
    db: AsyncSession,
    *,
    thread_record_id: int,
    objective_id: int,
) -> None:
    thread = await db.get(VACommunicationThread, thread_record_id)
    if thread is None:
        return
    thread.objective_id = objective_id
    thread.status = "open"
    thread.waiting_on = "va"
    await db.flush()


async def mark_thread_waiting_for_counterparty(
    db: AsyncSession,
    *,
    thread_record_id: int,
    objective_id: int,
    channel: str,
    target: str,
    purpose: str,
    payload: dict[str, Any] | None = None,
    follow_up_hours: int = 48,
    max_attempts: int = 4,
    schedule_follow_up: bool = True,
) -> VAFollowUp | None:
    thread = await db.get(VACommunicationThread, thread_record_id)
    if thread is None:
        return None
    due_at = utcnow() + timedelta(hours=max(1, min(int(follow_up_hours or 48), 24 * 30)))
    existing: VAFollowUp | None = None
    if schedule_follow_up:
        existing = (
            await db.execute(
                select(VAFollowUp)
                .where(
                    VAFollowUp.objective_id == objective_id,
                    VAFollowUp.channel == channel,
                    VAFollowUp.target == target,
                    VAFollowUp.status.in_(["pending", "due", "dispatching"]),
                )
                .order_by(VAFollowUp.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if existing is None:
            existing = VAFollowUp(
                objective_id=objective_id,
                channel=channel,
                target=target,
                purpose=purpose,
                payload_json=_dump(payload or {}),
                due_at=due_at,
                recurrence_hours=max(1, min(int(follow_up_hours or 48), 24 * 30)),
                max_attempts=max(1, min(int(max_attempts or 4), 10)),
                status="pending",
            )
            db.add(existing)
            await db.flush()
        else:
            existing.due_at = due_at
            existing.purpose = purpose
            existing.payload_json = _dump(payload or {})
            existing.status = "pending"

    thread.objective_id = objective_id
    thread.waiting_on = "counterparty"
    thread.status = "waiting"
    thread.last_outbound_at = utcnow()
    thread.last_activity_at = thread.last_outbound_at
    thread.next_follow_up_at = due_at if schedule_follow_up else None
    await write_audit(
        db,
        "communication_followup_scheduled",
        entity_type="va_communication_thread",
        entity_id=str(thread.id),
        details={
            "objective_id": objective_id,
            "follow_up_id": existing.id if existing is not None else None,
            "channel": channel,
            "target": target,
            "due_at": due_at,
        },
    )
    await db.flush()
    return existing


async def communication_threads_overview(db: AsyncSession, *, limit: int = 100) -> list[dict[str, Any]]:
    rows = list(
        (
            await db.execute(
                select(VACommunicationThread)
                .order_by(VACommunicationThread.last_activity_at.desc().nullslast(), VACommunicationThread.id.desc())
                .limit(max(1, min(limit, 500)))
            )
        ).scalars()
    )
    return [
        {
            "id": row.id,
            "channel": row.channel,
            "provider": row.provider,
            "thread_key": row.thread_key,
            "objective_id": row.objective_id,
            "title": row.title,
            "participant": row.participant,
            "status": row.status,
            "waiting_on": row.waiting_on,
            "last_message_ref": row.last_message_ref,
            "last_inbound_at": row.last_inbound_at,
            "last_outbound_at": row.last_outbound_at,
            "last_activity_at": row.last_activity_at,
            "next_follow_up_at": row.next_follow_up_at,
        }
        for row in rows
    ]


async def queue_saved_email_reply(
    db: AsyncSession,
    *,
    record: EmailMessage,
    recipient: str,
    subject: str,
    body: str,
    priority: str = "normal",
    expect_reply: bool = False,
    follow_up_hours: int = 48,
    policy: str = "autonomy_policy",
) -> VACommunicationThread:
    """Migrate a previously queued reply into the durable Phase-2 ownership path.

    This never sends Gmail directly. It fetches only the source message metadata needed
    for correct threading, records an idempotent VA event, and lets the objective engine
    persist/send/verify the outbound message.
    """
    from app.integrations.google_api import get_gmail_message, headers_to_dict

    source = await get_gmail_message(db, record.provider_message_id, format="metadata")
    headers = headers_to_dict(source.get("payload") or {})
    gmail_thread_id = str(source.get("threadId") or record.thread_id or "")
    rfc_message_id = str(headers.get("message-id") or "")
    thread = await _thread(
        db,
        channel="email",
        provider="gmail",
        thread_key=gmail_thread_id or record.provider_message_id,
        title=record.subject,
        participant=record.sender,
    )
    event, _ = await record_event(
        db,
        event_key=f"gmail-message:{record.provider_message_id}:reply-plan",
        source_type="email",
        source_id=record.provider_message_id,
        event_type="email_reply_planned",
        title=f"Reply: {record.subject or record.sender}",
        payload={
            "thread_record_id": thread.id,
            "gmail_thread_id": gmail_thread_id,
            "source_message_id": record.provider_message_id,
            "source_rfc_message_id": rfc_message_id,
            "sender": record.sender,
            "recipient": recipient,
            "subject": subject,
            "body": body,
            "category": record.category,
            "priority": priority or record.priority,
            "action_required": expect_reply,
            "expect_reply": expect_reply,
            "follow_up_hours": follow_up_hours,
            "policy": policy,
            "reasoning_summary": "Previously queued safe reply migrated into durable communications ownership.",
        },
        occurred_at=record.received_at or record.created_at,
    )
    thread.context_json = _dump({**_loads(thread.context_json), "last_va_event_id": event.id})
    await db.flush()
    return thread
