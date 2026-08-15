from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.ai_client import AIConfigurationError, AIQuotaDeferred, analyze_communication
from app.models.entities import CommunicationAction, CommunicationDeliveryEvidence, CommunicationEvent, CommunicationRule, Task
from app.schemas.api import CommunicationIngestRequest
from app.services.audit import write_audit
from app.services.communication_ownership import register_device_communication
from app.services.communication_correlation import find_cross_transport_duplicate
from app.services.relationship_preferences import relationship_reply_review_reason
from app.services.relationship_style_learning import relationship_reply_context_for_party
from app.services.runtime_config import get_runtime_value

PROTECTED_TERMS = (
    "iban", "bank", "betaling", "payment", "factuur", "invoice", "money", "geld", "transfer",
    "advocaat", "lawyer", "rechtbank", "court", "contract", "overeenkomst", "police", "politie",
    "password", "wachtwoord", "verification", "verificatie", "security code", "beveiligingscode",
    "one-time code", "one time code", "otp", "2fa", "pin code", "pincode", "itsme",
    "doctor", "arts", "hospital", "ziekenhuis", "medical", "medisch", "diagnosis", "diagnose",
)
URGENT_TERMS = (
    "urgent", "dringend", "emergency", "noodgeval", "immediately", "onmiddellijk", "today", "vandaag",
    "overdue", "achterstallig", "final notice", "laatste waarschuwing",
)
SPAM_TERMS = (
    "you won", "u heeft gewonnen", "claim your prize", "claim uw prijs", "crypto investment",
    "guaranteed return", "gegarandeerd rendement", "click now", "klik nu", "gift card",
)
GENERIC_NOTIFICATION_TEXT = {
    "new message", "you have a new message", "message", "notification", "new notification",
    "nieuw bericht", "u hebt een nieuw bericht", "je hebt een nieuw bericht",
}
ACK_ONLY = re.compile(r"^\s*(ok(?:ay)?|thanks?|thank you|dank(?:je| u)?|prima|top|👍|👌)[.! ]*\s*$", re.I)


def _database_datetime(value: datetime | None) -> datetime:
    """Normalize provider timestamps for the timezone-naive SQL schema.

    Android emits RFC3339 UTC timestamps with a ``Z`` suffix. Pydantic parses
    those as offset-aware datetimes, while the existing communication tables use
    SQLAlchemy ``DateTime`` without ``timezone=True``. asyncpg/PostgreSQL rejects
    that aware/naive mismatch even though SQLite accepts it in tests.
    """
    if value is None:
        return datetime.utcnow()
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _text_sensitive(body: str) -> bool:
    lower = body.casefold()
    return any(term in lower for term in PROTECTED_TERMS) or bool(
        re.search(r"\b(?:\d[ -]?){5,8}\b", body) and any(term in lower for term in ("code", "otp", "verific", "2fa"))
    )


def _local_decision(payload: CommunicationIngestRequest) -> dict[str, Any]:
    body = payload.body.strip()
    lower = body.casefold()
    sensitive = _text_sensitive(body)
    urgent = any(term in lower for term in URGENT_TERMS)
    spam = any(term in lower for term in SPAM_TERMS)
    generic_hidden = lower.strip(" .!:") in GENERIC_NOTIFICATION_TEXT
    if payload.channel == "call":
        return {
            "category": "Phone call",
            "priority": "normal",
            "action_required": payload.event_type in {"missed_call", "blocked_call"},
            "protected": False,
            "spam": False,
            "auto_reply_safe": False,
            "reply_text": None,
            "call_action": "allow",
            "reasoning_summary": "Conservative local call policy; unknown callers are not blocked without an explicit rule.",
        }
    if generic_hidden:
        return {
            "category": "Private/hidden notification",
            "priority": "normal",
            "action_required": True,
            "protected": True,
            "spam": False,
            "auto_reply_safe": False,
            "reply_text": None,
            "call_action": "allow",
            "reasoning_summary": "The messaging app hid the message content, so the VA cannot safely reply from the notification.",
        }
    if sensitive:
        return {
            "category": "Protected communication",
            "priority": "urgent" if urgent else "high",
            "action_required": True,
            "protected": True,
            "spam": False,
            "auto_reply_safe": False,
            "reply_text": None,
            "call_action": "allow",
            "reasoning_summary": "Sensitive content is kept for the user and never auto-replied to by fallback logic.",
        }
    if spam:
        return {
            "category": "Spam",
            "priority": "low",
            "action_required": False,
            "protected": False,
            "spam": True,
            "auto_reply_safe": False,
            "reply_text": None,
            "call_action": "allow",
            "reasoning_summary": "High-confidence promotional/scam wording matched the local spam rule.",
        }
    if not body or ACK_ONLY.match(body):
        return {
            "category": "Routine conversation",
            "priority": "low",
            "action_required": False,
            "protected": False,
            "spam": False,
            "auto_reply_safe": False,
            "reply_text": None,
            "call_action": "allow",
            "reasoning_summary": "No response is needed for this acknowledgement-only message.",
        }
    return {
        "category": "Conversation",
        "priority": "urgent" if urgent else "normal",
        "action_required": True,
        "protected": False,
        "spam": False,
        "auto_reply_safe": False,
        "reply_text": None,
        "call_action": "allow",
        "reasoning_summary": "AI was unavailable, so the message is retained for safe follow-up.",
    }


def _normalize_decision(payload: CommunicationIngestRequest, decision: dict[str, Any]) -> dict[str, Any]:
    normalized = {
        "category": str(decision.get("category") or "Conversation")[:120],
        "priority": str(decision.get("priority") or "normal").lower(),
        "action_required": bool(decision.get("action_required")),
        "protected": bool(decision.get("protected")),
        "spam": bool(decision.get("spam")),
        "auto_reply_safe": bool(decision.get("auto_reply_safe")),
        "reply_text": None if decision.get("reply_text") is None else str(decision.get("reply_text")).strip()[:800],
        "call_action": str(decision.get("call_action") or "allow").lower(),
        "reasoning_summary": str(decision.get("reasoning_summary") or "")[:500],
    }
    if normalized["priority"] not in {"low", "normal", "high", "urgent"}:
        normalized["priority"] = "normal"
    if normalized["call_action"] not in {"allow", "silence", "block"}:
        normalized["call_action"] = "allow"

    # Deterministic post-AI safety gate. Provider output can never override this.
    if _text_sensitive(payload.body):
        normalized["protected"] = True
        normalized["action_required"] = True
        normalized["auto_reply_safe"] = False
        normalized["reply_text"] = None
        normalized["call_action"] = "allow"
    if payload.body.casefold().strip(" .!:") in GENERIC_NOTIFICATION_TEXT:
        normalized["protected"] = True
        normalized["action_required"] = True
        normalized["auto_reply_safe"] = False
        normalized["reply_text"] = None
    if payload.direction != "incoming" or payload.channel == "call":
        normalized["auto_reply_safe"] = False
        normalized["reply_text"] = None
    if normalized["protected"]:
        normalized["auto_reply_safe"] = False
        normalized["reply_text"] = None
    if normalized["spam"]:
        normalized["auto_reply_safe"] = False
        normalized["reply_text"] = None
    if not normalized["reply_text"]:
        normalized["auto_reply_safe"] = False
    return normalized


async def _decision_for(db: AsyncSession, payload: CommunicationIngestRequest) -> dict[str, Any]:
    fallback = _local_decision(payload)
    # Device-history catch-up can contain hundreds of records. It is evidence import,
    # not a live reply opportunity, so keep it deterministic and avoid burning AI
    # quota or holding one mobile HTTP request open across many provider calls.
    if payload.provider in {"android_sms_history", "android_call_log"}:
        return fallback
    relationship_preferences, relationship_reply_context = await relationship_reply_context_for_party(
        db, payload.sender, channel=payload.channel, provider=payload.provider
    )
    if payload.channel == "call" or payload.direction != "incoming" or not payload.body.strip():
        return fallback
    try:
        decision = await analyze_communication(
            db,
            {
                "channel": payload.channel,
                "provider": payload.provider,
                "sender": payload.sender,
                "body": payload.body[:12000],
                "event_type": payload.event_type,
                "supports_direct_reply": payload.supports_direct_reply,
                "relationship_reply_preferences": relationship_reply_context,
            },
            urgent=any(term in payload.body.casefold() for term in URGENT_TERMS),
            sensitive=_text_sensitive(payload.body),
        )
    except (AIConfigurationError, AIQuotaDeferred, Exception):
        decision = fallback
    normalized = _normalize_decision(payload, decision)
    review_reason = relationship_reply_review_reason(
        relationship_preferences,
        incoming_text=payload.body,
        proposed_reply=str(normalized.get("reply_text") or ""),
    )
    if review_reason:
        normalized["relationship_review_required"] = True
        normalized["relationship_review_reason"] = review_reason
        normalized["action_required"] = True
        normalized["auto_reply_safe"] = False
    return normalized


async def _pending_action_for(db: AsyncSession, event_id: int) -> CommunicationAction | None:
    return (
        await db.execute(
            select(CommunicationAction)
            .where(CommunicationAction.event_id == event_id, CommunicationAction.status == "pending")
            .order_by(CommunicationAction.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def ingest_communication(db: AsyncSession, payload: CommunicationIngestRequest) -> dict[str, Any]:
    existing = (
        await db.execute(select(CommunicationEvent).where(CommunicationEvent.external_id == payload.external_id))
    ).scalar_one_or_none()
    if existing is not None:
        action = await _pending_action_for(db, existing.id)
        return {
            "event_id": existing.id,
            "duplicate": True,
            "decision": json.loads(existing.decision_json or "{}"),
            "device_action": _action_payload(action) if action is not None else None,
        }

    transport_duplicate = await find_cross_transport_duplicate(
        db,
        provider=payload.provider,
        channel=payload.channel,
        package_name=payload.package_name,
        sender=payload.sender,
        body=payload.body,
        occurred_at=_database_datetime(payload.occurred_at),
    )
    if transport_duplicate is not None:
        action = await _pending_action_for(db, transport_duplicate.id)
        await db.commit()
        return {
            "event_id": transport_duplicate.id,
            "duplicate": True,
            "decision": json.loads(transport_duplicate.decision_json or "{}"),
            "device_action": _action_payload(action) if action is not None else None,
        }

    event = CommunicationEvent(
        external_id=payload.external_id,
        channel=payload.channel,
        provider=payload.provider,
        package_name=payload.package_name,
        thread_key=payload.thread_key,
        sender=payload.sender,
        recipient=payload.recipient,
        body=payload.body,
        direction=payload.direction,
        event_type=payload.event_type,
        occurred_at=_database_datetime(payload.occurred_at),
    )
    db.add(event)
    await db.flush()

    decision = await _decision_for(db, payload)
    event.category = decision["category"]
    event.priority = decision["priority"]
    event.action_required = decision["action_required"]
    event.protected = decision["protected"]
    event.status = "processed"
    event.decision_json = json.dumps(decision, ensure_ascii=False, separators=(",", ":"))

    action: CommunicationAction | None = None
    auto_reply_enabled = (await get_runtime_value(db, "communications_auto_reply_enabled", "true")).lower() == "true"
    can_reply_on_device = payload.channel == "sms" or payload.supports_direct_reply
    if (
        payload.allow_action
        and payload.direction == "incoming"
        and payload.channel != "call"
        and auto_reply_enabled
        and can_reply_on_device
        and decision["auto_reply_safe"]
        and decision["reply_text"]
    ):
        action = CommunicationAction(
            event_id=event.id,
            action_type="reply",
            target=payload.sender,
            payload_json=json.dumps({"text": decision["reply_text"], "channel": payload.channel}, ensure_ascii=False),
            idempotency_key=f"communication:{event.id}:reply",
            status="pending",
            requires_user_action=False,
        )
        db.add(action)
        await db.flush()

    # Phase-2 CommunicationEvent ownership is canonical. Do not create a
    # parallel legacy Task projection for new device messages. v1.0.5 startup/core
    # repair supersedes historical projections without deleting source evidence.

    await register_device_communication(db, event=event, action=action)

    await write_audit(
        db,
        "communication_processed",
        entity_type="communication_event",
        entity_id=str(event.id),
        details={
            "channel": payload.channel,
            "category": event.category,
            "priority": event.priority,
            "protected": event.protected,
            "device_action": action.action_type if action else None,
        },
    )
    await db.commit()
    return {
        "event_id": event.id,
        "duplicate": False,
        "decision": decision,
        "device_action": _action_payload(action) if action is not None else None,
    }


def _action_payload(action: CommunicationAction) -> dict[str, Any]:
    payload = json.loads(action.payload_json or "{}")
    return {
        "id": action.id,
        "type": action.action_type,
        "target": action.target,
        **payload,
    }


async def complete_communication_action(
    db: AsyncSession,
    action_id: int,
    *,
    status: str,
    failure_reason: str = "",
    external_ref: str = "",
    details: dict[str, Any] | None = None,
) -> CommunicationAction:
    action = await db.get(CommunicationAction, action_id)
    if action is None:
        raise ValueError("Communication action does not exist")

    accepted = {"pending", "dispatched", "sent", "delivered", "completed", "failed", "cancelled", "delivery_failed"}
    if status not in accepted:
        raise ValueError(f"Unsupported communication action status: {status}")

    current_success = action.status in {"dispatched", "sent", "delivered", "completed"}
    evidence_type = {
        "dispatched": "remote_input_dispatched",
        "sent": "sms_sent",
        "delivered": "sms_delivered",
        "delivery_failed": "sms_delivery_failed",
        "completed": "device_action_completed",
    }.get(status)

    # Never turn a proven send/provider handoff into a retryable failure just because
    # a later delivery receipt reports failure or a duplicate callback arrives.
    if status == "delivery_failed" and current_success:
        effective_status = action.status
    elif status == "failed" and current_success:
        effective_status = action.status
    else:
        rank = {"pending": 0, "dispatched": 1, "sent": 2, "completed": 2, "delivered": 3}
        if status in rank and action.status in rank:
            effective_status = status if rank[status] >= rank[action.status] else action.status
        else:
            effective_status = status

    action.status = effective_status
    action.failure_reason = failure_reason[:2000]
    if evidence_type:
        existing_evidence = (
            await db.execute(
                select(CommunicationDeliveryEvidence).where(
                    CommunicationDeliveryEvidence.communication_action_id == action.id,
                    CommunicationDeliveryEvidence.evidence_type == evidence_type,
                ).limit(1)
            )
        ).scalar_one_or_none()
        if existing_evidence is None:
            db.add(
                CommunicationDeliveryEvidence(
                    communication_action_id=action.id,
                    evidence_type=evidence_type,
                    external_ref=external_ref[:1000],
                    details_json=json.dumps(details or {"reported_status": status, "failure_reason": failure_reason}, ensure_ascii=False, default=str),
                )
            )

    event = await db.get(CommunicationEvent, action.event_id)
    proven = effective_status in {"dispatched", "sent", "delivered", "completed"}
    if event is not None and proven:
        event.status = f"action_{effective_status}"
        event.action_required = False
        task = (
            await db.execute(
                select(Task).where(
                    Task.source_type == "communication",
                    Task.source_id == str(event.id),
                    Task.status.in_(["open", "waiting"]),
                )
            )
        ).scalar_one_or_none()
        if task is not None and not task.requires_approval:
            task.status = "completed"

    await write_audit(
        db,
        "communication_action_result",
        entity_type="communication_action",
        entity_id=str(action.id),
        result="success" if proven else ("deferred" if status == "delivery_failed" else "failed"),
        details={
            "reported_status": status,
            "effective_status": effective_status,
            "failure_reason": action.failure_reason,
            "external_ref": external_ref,
        },
    )
    await db.commit()
    return action


async def pending_communication_actions(db: AsyncSession, *, limit: int = 50) -> list[dict[str, Any]]:
    """Return real persisted device work that still needs dispatch/reconciliation.

    SMS actions may be initiated by the paired device worker. Notification-app rows
    are also returned so locally persisted RemoteInput handoff evidence can be re-posted
    after a network outage, but they are explicitly marked non-dispatchable because a
    stale RemoteInput action cannot be reconstructed safely.
    """
    rows = list(
        (
            await db.execute(
                select(CommunicationAction)
                .where(CommunicationAction.status == "pending")
                .order_by(CommunicationAction.created_at.asc(), CommunicationAction.id.asc())
                .limit(max(1, min(limit, 200)))
            )
        ).scalars()
    )
    result: list[dict[str, Any]] = []
    for action in rows:
        payload = json.loads(action.payload_json or "{}")
        channel = str(payload.get("channel") or "").lower()
        result.append({
            "id": action.id,
            "type": action.action_type,
            "target": action.target,
            "text": str(payload.get("text") or ""),
            "channel": channel,
            "can_background_dispatch": channel == "sms",
            "created_at": action.created_at,
        })
    return result


async def device_call_policy(db: AsyncSession) -> dict[str, Any]:
    rows = list((await db.execute(select(CommunicationRule).where(CommunicationRule.channel == "call"))).scalars())
    blocked = [row.contact_key for row in rows if row.disposition == "block"]
    silenced = [row.contact_key for row in rows if row.disposition == "silence"]
    vip = [row.contact_key for row in rows if row.disposition == "allow" and row.source in {"manual", "vip"}]
    return {
        "blocked_numbers": blocked,
        "silenced_numbers": silenced,
        "vip_numbers": vip,
        "silence_unknown": (await get_runtime_value(db, "communications_silence_unknown_calls", "false")).lower() == "true",
    }
