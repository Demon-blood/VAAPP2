from __future__ import annotations

import json
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import CommunicationEvent, EmailMessage, Task
from app.services.audit import write_audit
from app.services.communication_attention import normalize_communication_attention

_OTP_TASK_TERMS = (
    "one-time code", "one time code", "otp", "verification code", "verificatiecode",
    "security code", "beveiligingscode", "2fa",
)
_PROVIDER_ERROR_TERMS = (
    "groq", "gemini", "ai provider", "quota", "rate limit", "timeout", "google drive",
    "drive archive", "provider error", "api error", "connection error", "404", "413",
)
_LOW_VALUE_CATEGORIES = ("newsletter", "promotion", "social", "notification", "low priority", "lage prioriteit")


def _saved_decision(email: EmailMessage | None) -> dict:
    if email is None:
        return {}
    try:
        value = json.loads(email.analysis_json or "{}")
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        return {}


def _email_has_real_structured_action(saved: dict) -> bool:
    return any(saved.get(key) is not None for key in ("task", "reply", "calendar_event", "bill", "support_case", "order"))


async def repair_communication_backlog(db: AsyncSession) -> dict[str, int]:
    """Cancel bad legacy projections while retaining the original evidence."""
    cancelled_tasks = 0
    repaired_events = 0
    repaired_emails = 0
    tasks = list((await db.execute(select(Task).where(Task.status.in_(["open", "waiting"])))).scalars())

    # Remove exact duplicate projections first. Different source types for one email may
    # still represent different real obligations, so dedupe only within source type/id.
    seen_task_keys: set[tuple[str, str]] = set()
    for task in sorted(tasks, key=lambda row: row.id, reverse=True):
        source_type = str(task.source_type or "")
        source_id = str(task.source_id or "")
        if source_type == "manual" or not source_id:
            continue
        key = (source_type, source_id)
        if key in seen_task_keys:
            task.status = "cancelled"
            task.requires_approval = False
            cancelled_tasks += 1
            continue
        seen_task_keys.add(key)

    for task in tasks:
        if task.status not in {"open", "waiting"}:
            continue
        source_type = str(task.source_type or "").casefold()
        text = f"{task.title}\n{task.description}".casefold()
        email = None
        saved: dict = {}
        if task.source_id and source_type.startswith("email"):
            email = (await db.execute(select(EmailMessage).where(EmailMessage.provider_message_id == task.source_id).limit(1))).scalar_one_or_none()
            saved = _saved_decision(email)

        cancel = False
        reason = ""
        if source_type == "communication":
            cancel, reason = True, "legacy device-message Task superseded by canonical CommunicationEvent ownership"
        elif source_type in {"ai_review", "email_ai_review"}:
            cancel, reason = True, "AI/provider availability is system-owned and is not a human task"
        elif source_type == "email_archive" and any(term in text for term in _PROVIDER_ERROR_TERMS):
            cancel, reason = True, "document/provider failure remains system-owned"
        elif source_type != "manual" and any(term in text for term in _OTP_TASK_TERMS):
            cancel, reason = True, "authentication-code message is evidence; the active authentication objective owns any real blocker"
        elif source_type == "email_action" and email is not None:
            category = str(saved.get("category") or email.category or "").casefold()
            fallback_noise = any(term in text for term in _PROVIDER_ERROR_TERMS) or "deterministic fallback" in str(saved.get("reasoning_summary") or "").casefold()
            low_value = any(term in category for term in _LOW_VALUE_CATEGORIES)
            if (fallback_noise or low_value) and not _email_has_real_structured_action(saved):
                cancel, reason = True, "informational/fallback email does not require user completion"
                email.action_required = False
                saved["action_required"] = False
                email.analysis_json = json.dumps(saved, ensure_ascii=False, separators=(",", ":"))
                repaired_emails += 1
        elif source_type == "email_reply" and email is not None:
            if email.status == "replied" or not saved.get("reply"):
                cancel, reason = True, "saved email no longer has an unresolved reply decision"

        if cancel:
            task.status = "cancelled"
            task.requires_approval = False
            cancelled_tasks += 1
            await write_audit(
                db,
                "communication_backlog_task_cancelled",
                entity_type="task",
                entity_id=str(task.id),
                result="superseded",
                details={"source_type": task.source_type, "reason": reason},
            )

    events = list((await db.execute(select(CommunicationEvent).where(CommunicationEvent.action_required.is_(True)))).scalars())
    for event in events:
        try:
            decision = json.loads(event.decision_json or "{}")
        except json.JSONDecodeError:
            continue
        payload = SimpleNamespace(body=event.body, provider=event.provider, direction=event.direction, channel=event.channel, event_type=event.event_type)
        repaired = normalize_communication_attention(payload, decision)
        if bool(repaired.get("action_required")) == bool(event.action_required) and bool(repaired.get("protected")) == bool(event.protected):
            continue
        event.action_required = bool(repaired.get("action_required"))
        event.protected = bool(repaired.get("protected"))
        event.decision_json = json.dumps(repaired, ensure_ascii=False, separators=(",", ":"))
        repaired_events += 1

    if cancelled_tasks or repaired_events or repaired_emails:
        await write_audit(
            db,
            "communication_backlog_repaired",
            entity_type="communications",
            entity_id="canonical",
            details={"cancelled_tasks": cancelled_tasks, "repaired_events": repaired_events, "repaired_emails": repaired_emails},
        )
        await db.commit()
    return {"cancelled_tasks": cancelled_tasks, "repaired_events": repaired_events, "repaired_emails": repaired_emails}
