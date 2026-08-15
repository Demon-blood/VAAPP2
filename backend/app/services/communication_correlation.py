from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import CommunicationAction, CommunicationEvent, Task, VAEvent, VAObjective
from app.services.audit import write_audit

_MESSAGES_PACKAGES = {"com.google.android.apps.messaging", "com.samsung.android.messaging"}
_WINDOW = timedelta(seconds=120)


def _database_datetime(value: datetime | None) -> datetime:
    if value is None:
        return datetime.utcnow()
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _body(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip().casefold()


def _sender(value: str) -> str:
    raw = (value or "").strip().casefold()
    digits = "".join(ch for ch in raw if ch.isdigit())
    return digits if len(digits) >= 3 else raw


def _is_native_sms(*, provider: str, channel: str) -> bool:
    return provider == "android_sms" and channel == "sms"


def _is_messages_notification(*, provider: str, channel: str, package_name: str) -> bool:
    return package_name in _MESSAGES_PACKAGES and channel == "notification" and provider == "notification"


def _pair_is_cross_transport(
    *,
    left_provider: str,
    left_channel: str,
    left_package: str,
    right_provider: str,
    right_channel: str,
    right_package: str,
) -> bool:
    return (
        _is_native_sms(provider=left_provider, channel=left_channel)
        and _is_messages_notification(provider=right_provider, channel=right_channel, package_name=right_package)
    ) or (
        _is_native_sms(provider=right_provider, channel=right_channel)
        and _is_messages_notification(provider=left_provider, channel=left_channel, package_name=left_package)
    )


def _senders_compatible(left: str, right: str) -> bool:
    a, b = _sender(left), _sender(right)
    if not a or not b:
        return True
    if a == b:
        return True
    # Contact-name notifications may not expose the phone number. When either side
    # is clearly non-numeric, body + timestamp + known Messages/native-SMS transport
    # is the safer correlation signal than pretending the name is a verified number.
    return not a.isdigit() or not b.isdigit()


async def _has_action(db: AsyncSession, event_id: int) -> bool:
    return bool(
        (
            await db.execute(
                select(CommunicationAction.id).where(CommunicationAction.event_id == event_id).limit(1)
            )
        ).scalar_one_or_none()
    )


async def find_cross_transport_duplicate(
    db: AsyncSession,
    *,
    provider: str,
    channel: str,
    package_name: str,
    sender: str,
    body: str,
    occurred_at: datetime | None,
) -> CommunicationEvent | None:
    """Correlate only native SMS with Google/Samsung Messages notification capture.

    Same-channel repeats are never collapsed. That is intentional: providers can send
    two genuinely identical SMS messages, and a content hash is not proof they are one event.
    """

    if not (
        _is_native_sms(provider=provider, channel=channel)
        or _is_messages_notification(provider=provider, channel=channel, package_name=package_name)
    ):
        return None
    normalized_body = _body(body)
    if not normalized_body:
        return None
    happened = _database_datetime(occurred_at)
    rows = list(
        (
            await db.execute(
                select(CommunicationEvent).where(
                    CommunicationEvent.direction == "incoming",
                    CommunicationEvent.occurred_at >= happened - _WINDOW,
                    CommunicationEvent.occurred_at <= happened + _WINDOW,
                )
            )
        ).scalars()
    )
    for row in rows:
        if not _pair_is_cross_transport(
            left_provider=provider,
            left_channel=channel,
            left_package=package_name,
            right_provider=row.provider,
            right_channel=row.channel,
            right_package=row.package_name,
        ):
            continue
        if _body(row.body) != normalized_body or not _senders_compatible(row.sender, sender):
            continue
        if await _has_action(db, row.id):
            # Do not rewrite transport identity after a concrete RemoteInput/SMS action
            # was attached. Outcome reconciliation owns that event now.
            continue
        if _is_native_sms(provider=provider, channel=channel) and _is_messages_notification(
            provider=row.provider, channel=row.channel, package_name=row.package_name
        ):
            previous = {"channel": row.channel, "provider": row.provider, "package_name": row.package_name}
            row.channel = "sms"
            row.provider = "android_sms"
            row.package_name = package_name
            row.thread_key = _sender(sender) or row.thread_key
            row.sender = sender or row.sender
            row.recipient = "me"
            row.occurred_at = min(row.occurred_at or happened, happened)
            await write_audit(
                db,
                "communication_transport_promoted",
                entity_type="communication_event",
                entity_id=str(row.id),
                details={"from": previous, "to": {"channel": "sms", "provider": "android_sms"}},
            )
        return row
    return None


def _decision(row: CommunicationEvent) -> dict[str, Any]:
    try:
        decoded = json.loads(row.decision_json or "{}")
        return decoded if isinstance(decoded, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


async def _cancel_objective(db: AsyncSession, objective: VAObjective | None, reason: str) -> int:
    if objective is None or objective.status in {"completed", "cancelled", "failed"}:
        return 0
    from app.services.autonomous_core import _transition_objective

    await _transition_objective(db, objective, "cancelled", reason=reason)
    return 1


async def _direct_objective_for_event(db: AsyncSession, event_id: int) -> VAObjective | None:
    key = f"event:communication-event:{event_id}:actionable"
    return (
        await db.execute(select(VAObjective).where(VAObjective.correlation_key == key).limit(1))
    ).scalar_one_or_none()


async def _task_objective(db: AsyncSession, task_id: int) -> VAObjective | None:
    return (
        await db.execute(
            select(VAObjective).where(VAObjective.correlation_key == f"event:task:{task_id}").limit(1)
        )
    ).scalar_one_or_none()


async def repair_communication_correlation(db: AsyncSession, *, limit: int = 1000) -> dict[str, int]:
    """Repair legacy duplicate Needs-You projections without deleting source evidence."""

    result = {"task_projections_superseded": 0, "transport_duplicates_superseded": 0, "objectives_cancelled": 0, "direct_needs_user_promoted": 0}
    from app.services.autonomous_core import _transition_objective, record_event

    # A CommunicationEvent already owns its unified Phase-2 event. Legacy Task rows are
    # projections for older screens and must not create a second real-world objective.
    tasks = list(
        (
            await db.execute(
                select(Task)
                .where(Task.source_type == "communication", Task.status.in_(["open", "waiting"]))
                .order_by(Task.id.asc())
                .limit(max(1, min(limit, 5000)))
            )
        ).scalars()
    )
    for task in tasks:
        if not str(task.source_id or "").isdigit():
            continue
        event = await db.get(CommunicationEvent, int(task.source_id))
        if event is None:
            continue
        decision = _decision(event)
        direct_event = (
            await db.execute(
                select(VAEvent).where(VAEvent.event_key == f"communication-event:{event.id}:actionable").limit(1)
            )
        ).scalar_one_or_none()
        if direct_event is None and event.direction == "incoming" and event.action_required:
            direct_event, _ = await record_event(
                db,
                event_key=f"communication-event:{event.id}:actionable",
                source_type="communication_event",
                source_id=str(event.id),
                event_type="communication_actionable",
                title=f"Follow up: {event.sender or event.channel}",
                payload={
                    "communication_event_id": event.id,
                    "channel": event.channel,
                    "provider": event.provider,
                    "priority": event.priority,
                    "protected": event.protected,
                    "requires_user_review": bool(decision.get("relationship_review_required")),
                    "proposed_reply": str(decision.get("reply_text") or ""),
                },
                occurred_at=event.occurred_at,
            )
        direct_objective = await _direct_objective_for_event(db, event.id)
        direct_context = {}
        if direct_objective is not None:
            try:
                decoded_context = json.loads(direct_objective.context_json or "{}")
                direct_context = decoded_context if isinstance(decoded_context, dict) else {}
            except (TypeError, json.JSONDecodeError):
                direct_context = {}
        decided = str((direct_context.get("specific_authorization") or {}).get("decision") or "")
        # Historical Task.requires_approval often meant only "protected message" and
        # produced the fake Needs-You cards v1.0.5 is repairing. Promote only when
        # there is a concrete reply proposal subject to relationship review (or a
        # future protected proposal), never from the legacy Task flag alone.
        proposed_reply = str(decision.get("reply_text") or "").strip()
        needs_decision = bool(
            decision.get("relationship_review_required")
            or (event.protected and proposed_reply)
        )
        if direct_objective is not None and not decided and needs_decision and direct_objective.status == "blocked_capability":
            await _transition_objective(
                db,
                direct_objective,
                "needs_user",
                reason="This communication contains a material decision or an explicit relationship-level review requirement.",
            )
            result["direct_needs_user_promoted"] += 1
        result["objectives_cancelled"] += await _cancel_objective(
            db,
            await _task_objective(db, task.id),
            "Superseded by the source CommunicationEvent objective; one real-world decision has one owner.",
        )
        task.status = "cancelled"
        result["task_projections_superseded"] += 1
        await write_audit(
            db,
            "communication_task_projection_superseded",
            entity_type="task",
            entity_id=str(task.id),
            details={"communication_event_id": event.id},
        )

    # Historical Google/Samsung Messages notifications can mirror a native SMS. Keep
    # the native SMS as canonical when both independently reached the backend.
    rows = list(
        (
            await db.execute(
                select(CommunicationEvent)
                .where(CommunicationEvent.direction == "incoming")
                .order_by(CommunicationEvent.occurred_at.desc().nullslast(), CommunicationEvent.id.desc())
                .limit(max(1, min(limit, 5000)))
            )
        ).scalars()
    )
    natives = [row for row in rows if _is_native_sms(provider=row.provider, channel=row.channel)]
    notifications = [
        row
        for row in rows
        if _is_messages_notification(provider=row.provider, channel=row.channel, package_name=row.package_name)
    ]
    for duplicate in notifications:
        if duplicate.status == "duplicate_transport" or await _has_action(db, duplicate.id):
            continue
        if not duplicate.occurred_at or not _body(duplicate.body):
            continue
        canonical = next(
            (
                row
                for row in natives
                if row.occurred_at
                and abs((row.occurred_at - duplicate.occurred_at).total_seconds()) <= _WINDOW.total_seconds()
                and _body(row.body) == _body(duplicate.body)
                and _senders_compatible(row.sender, duplicate.sender)
            ),
            None,
        )
        if canonical is None:
            continue
        duplicate.status = "duplicate_transport"
        duplicate.action_required = False
        dup_tasks = list(
            (
                await db.execute(
                    select(Task).where(
                        Task.source_type == "communication", Task.source_id == str(duplicate.id), Task.status.in_(["open", "waiting"])
                    )
                )
            ).scalars()
        )
        for task in dup_tasks:
            task.status = "cancelled"
            result["objectives_cancelled"] += await _cancel_objective(
                db, await _task_objective(db, task.id), "Duplicate Messages notification superseded by native SMS evidence."
            )
        result["objectives_cancelled"] += await _cancel_objective(
            db,
            await _direct_objective_for_event(db, duplicate.id),
            "Duplicate Messages notification superseded by native SMS evidence.",
        )
        result["transport_duplicates_superseded"] += 1
        await write_audit(
            db,
            "communication_cross_transport_duplicate_superseded",
            entity_type="communication_event",
            entity_id=str(duplicate.id),
            details={"canonical_event_id": canonical.id, "duplicate_provider": duplicate.provider},
        )

    if any(result.values()):
        await db.commit()
    return result
