from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import CommunicationAction, CommunicationEvent, EmailMessage, Task, VAObjective
from app.schemas.api import AutomationDecision
from app.services.audit import write_audit


def _now() -> datetime:
    return datetime.utcnow()


def _loads(value: str | None) -> dict[str, Any]:
    try:
        decoded = json.loads(value or "{}")
        return decoded if isinstance(decoded, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _mentioned_eur(text: str) -> str:
    patterns = (
        r"(?:€|EUR)\s*([0-9]+(?:[.,][0-9]{1,2})?)",
        r"([0-9]+(?:[.,][0-9]{1,2})?)\s*(?:EUR|€)\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text or "", re.I)
        if not match:
            continue
        try:
            return str(Decimal(match.group(1).replace(",", ".")).quantize(Decimal("0.01")))
        except (InvalidOperation, ValueError):
            return ""
    return ""


def _fingerprint(objective: VAObjective, proposal: dict[str, Any]) -> str:
    canonical = json.dumps(
        {
            "objective_id": objective.id,
            "source_type": objective.source_type,
            "source_id": objective.source_id,
            "proposal": proposal,
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _external_authorization(objective: VAObjective) -> bool:
    return objective.category in {"bank_authorization", "provider_authentication"} or objective.source_type in {
        "payment",
        "own_account_transfer",
        "workflow_job",
    }


async def _task_proposal(db: AsyncSession, task: Task) -> dict[str, Any]:
    proposal: dict[str, Any] = {
        "action_type": task.source_type or "material_task_decision",
        "summary": task.title,
        "source_excerpt": (task.description or "")[:1200],
    }
    if task.source_type == "email_reply" and task.source_id:
        email = (
            await db.execute(
                select(EmailMessage).where(EmailMessage.provider_message_id == task.source_id).limit(1)
            )
        ).scalar_one_or_none()
        if email is not None:
            try:
                decision = AutomationDecision.model_validate_json(email.analysis_json or "{}")
                reply = decision.reply or {}
            except Exception:
                reply = {}
            proposal.update(
                {
                    "action_type": "send_email_reply",
                    "provider": "gmail",
                    "channel": "email",
                    "counterparty": email.sender,
                    "category": email.category,
                    "subject": str(reply.get("subject") or f"Re: {email.subject}"),
                    "proposed_reply": str(reply.get("body") or "")[:4000],
                }
            )
    amount = _mentioned_eur(f"{task.title}\n{task.description}")
    if amount:
        proposal["amount_mentioned"] = amount
        proposal["currency"] = "EUR"
    return proposal


async def _communication_proposal(event: CommunicationEvent) -> dict[str, Any]:
    decision = _loads(event.decision_json)
    proposal: dict[str, Any] = {
        "action_type": "send_message_reply" if decision.get("reply_text") else "material_communication_follow_up",
        "summary": f"Follow up with {event.sender or event.channel}",
        "provider": event.provider,
        "channel": event.channel,
        "counterparty": event.sender,
        "category": event.category,
        "protected": event.protected,
        "source_excerpt": (event.body or "")[:1200],
    }
    proposed_reply = str(decision.get("reply_text") or "").strip()
    if proposed_reply:
        proposal["proposed_reply"] = proposed_reply[:4000]
    amount = _mentioned_eur(event.body or "")
    if amount:
        proposal["amount_mentioned"] = amount
        proposal["currency"] = "EUR"
    return proposal


async def user_action_for_objective(db: AsyncSession, objective: VAObjective) -> dict[str, Any] | None:
    if objective.status != "needs_user":
        return None
    if _external_authorization(objective):
        context = _loads(objective.context_json)
        return {
            "kind": "external_authorization",
            "title": "Provider authorization required",
            "detail": objective.needs_user_reason,
            "authorization_url": str(context.get("authorization_url") or ""),
            "can_recheck": True,
        }

    proposal: dict[str, Any] | None = None
    if objective.source_type == "task" and str(objective.source_id).isdigit():
        task = await db.get(Task, int(objective.source_id))
        if task is not None and task.requires_approval and task.status in {"open", "waiting"}:
            proposal = await _task_proposal(db, task)
    elif objective.source_type == "communication_event" and str(objective.source_id).isdigit():
        event = await db.get(CommunicationEvent, int(objective.source_id))
        if event is not None and event.direction == "incoming" and event.action_required:
            decision = _loads(event.decision_json)
            # A sensitive message by itself is not an authorizable action. Needs You
            # may only grant VA execution authority when a concrete proposed reply
            # exists and is bound by the action fingerprint.
            if str(decision.get("reply_text") or "").strip():
                proposal = await _communication_proposal(event)

    if proposal is None:
        return {
            "kind": "recheck",
            "title": "User action required",
            "detail": objective.needs_user_reason,
            "can_recheck": True,
        }
    return {
        "kind": "specific_authorization",
        "title": "Specific authorization required",
        "detail": "Authorization applies only to this objective and this exact proposed action. It does not grant standing authority.",
        "proposal": proposal,
        "action_fingerprint": _fingerprint(objective, proposal),
        "can_authorize": True,
        "can_decline": True,
    }


async def _current_specific_view(db: AsyncSession, objective: VAObjective, action_fingerprint: str) -> dict[str, Any]:
    view = await user_action_for_objective(db, objective)
    if not view or view.get("kind") != "specific_authorization":
        raise ValueError("This objective does not currently expose a specific authorization decision")
    if not action_fingerprint or action_fingerprint != view.get("action_fingerprint"):
        raise ValueError("The proposed action changed; refresh before authorizing or declining")
    return view


async def _queue_authorized_email_reply(
    db: AsyncSession,
    objective: VAObjective,
    task: Task,
    *,
    authorization_policy: str = "specific_objective_authorization",
) -> str:
    email = (
        await db.execute(
            select(EmailMessage).where(EmailMessage.provider_message_id == task.source_id).limit(1)
        )
    ).scalar_one_or_none()
    if email is None:
        return "source email is unavailable"
    try:
        decision = AutomationDecision.model_validate_json(email.analysis_json or "{}")
    except Exception:
        return "saved email decision is invalid"
    reply = decision.reply or {}
    body = str(reply.get("body") or "").strip()
    if not body:
        return "saved email reply is empty"

    from app.services.communication_ownership import queue_saved_email_reply

    await queue_saved_email_reply(
        db,
        record=email,
        recipient=str(reply.get("to") or email.sender),
        subject=str(reply.get("subject") or f"Re: {email.subject}"),
        body=body,
        priority=email.priority or "normal",
        expect_reply=bool(decision.task or decision.support_case or decision.action_required),
        follow_up_hours=48,
        policy=authorization_policy,
    )
    task.status = "waiting"
    task.requires_approval = False
    return "queued"


async def _queue_authorized_device_reply(
    db: AsyncSession,
    objective: VAObjective,
    event: CommunicationEvent,
    *,
    authorization_policy: str = "specific_objective_authorization",
) -> str:
    decision = _loads(event.decision_json)
    text = str(decision.get("reply_text") or "").strip()
    if not text:
        return "no executable reply is attached to this communication"
    if event.channel != "sms":
        return f"{event.channel or 'notification'} has no durable initiator after the original notification action expires"
    key = f"authorized-communication:{event.id}:reply"
    action = (
        await db.execute(select(CommunicationAction).where(CommunicationAction.idempotency_key == key).limit(1))
    ).scalar_one_or_none()
    if action is None:
        action = CommunicationAction(
            event_id=event.id,
            action_type="reply",
            target=event.sender,
            payload_json=_dump({
                "text": text,
                "channel": "sms",
                "authorized_objective_id": objective.id,
                "authorization_policy": authorization_policy,
            }),
            idempotency_key=key,
            status="pending",
            requires_user_action=False,
        )
        db.add(action)
        await db.flush()
    # Preserve the source sensitivity classification while marking only this exact
    # executor proposal as specifically authorized. Communication ownership uses
    # this marker to avoid creating a second approval loop for the same action.
    decision["specific_authorized"] = True
    if authorization_policy.startswith("standing_authority:"):
        decision["standing_authorized"] = True
    event.decision_json = _dump(decision)
    from app.services.communication_ownership import register_device_communication

    await register_device_communication(db, event=event, action=action)
    event.action_required = False
    return "queued"


async def apply_standing_authority_objectives(
    db: AsyncSession,
    *,
    limit: int = 100,
) -> dict[str, int]:
    """Resume exact executable Needs You proposals covered by explicit authority.

    This function deliberately skips provider/bank authorization and proposals that
    do not have a concrete executor. Authority removes repeat decision work; it does
    not manufacture execution or completion evidence.
    """
    from app.services.autonomous_core import _transition_objective
    from app.services.standing_authority import (
        evaluate_standing_authority,
        record_standing_authority_use,
    )

    rows = list(
        (
            await db.execute(
                select(VAObjective)
                .where(VAObjective.status == "needs_user")
                .order_by(VAObjective.priority.desc(), VAObjective.due_at.asc().nullslast(), VAObjective.id.asc())
                .limit(max(1, min(int(limit or 100), 500)))
            )
        ).scalars()
    )
    result = {"checked": 0, "authorized": 0, "blocked": 0}
    for objective in rows:
        if _external_authorization(objective):
            continue

        proposal: dict[str, Any] | None = None
        task: Task | None = None
        event: CommunicationEvent | None = None
        if objective.source_type == "task" and str(objective.source_id).isdigit():
            task = await db.get(Task, int(objective.source_id))
            if task is not None and task.requires_approval and task.status in {"open", "waiting"}:
                if task.source_type == "email_reply":
                    proposal = await _task_proposal(db, task)
        elif objective.source_type == "communication_event" and str(objective.source_id).isdigit():
            event = await db.get(CommunicationEvent, int(objective.source_id))
            if event is not None and event.direction == "incoming" and event.action_required:
                decision = _loads(event.decision_json)
                if str(decision.get("reply_text") or "").strip():
                    proposal = await _communication_proposal(event)

        if proposal is None:
            continue
        result["checked"] += 1
        authority = await evaluate_standing_authority(
            db,
            action_type=str(proposal.get("action_type") or ""),
            risk_level=objective.risk_level,
            proposal=proposal,
        )
        if not authority.get("allowed"):
            continue

        policy = f"standing_authority:{authority['policy_key']}"
        try:
            if task is not None:
                task.requires_approval = False
                execution = await _queue_authorized_email_reply(
                    db,
                    objective,
                    task,
                    authorization_policy=policy,
                )
            elif event is not None:
                event.action_required = False
                execution = await _queue_authorized_device_reply(
                    db,
                    objective,
                    event,
                    authorization_policy=policy,
                )
                legacy_tasks = list(
                    (
                        await db.execute(
                            select(Task).where(
                                Task.source_type == "communication",
                                Task.source_id == str(event.id),
                            )
                        )
                    ).scalars()
                )
                for legacy in legacy_tasks:
                    legacy.requires_approval = False
                    if legacy.status in {"open", "waiting"}:
                        legacy.status = "cancelled"
            else:
                continue
        except Exception as exc:
            await _transition_objective(
                db,
                objective,
                "blocked_system",
                reason="Standing authority covers this action, but executor preparation failed internally.",
                error=str(exc),
            )
            result["blocked"] += 1
            continue

        if execution == "queued":
            context = _loads(objective.context_json)
            context["standing_authority"] = {
                "policy_key": str(authority.get("policy_key") or ""),
                "decision": "authorized",
                "action_type": str(proposal.get("action_type") or ""),
                "applied_at": datetime.utcnow().isoformat() + "Z",
            }
            objective.context_json = _dump(context)
            await record_standing_authority_use(
                db,
                decision=authority,
                action_type=str(proposal.get("action_type") or ""),
                proposal=proposal,
                objective=objective,
            )
            await _transition_objective(
                db,
                objective,
                "cancelled",
                reason=(
                    "Explicit standing authority covered this exact proposal; the decision objective "
                    "was superseded by the durable executor objective."
                ),
            )
            result["authorized"] += 1
        else:
            await _transition_objective(
                db,
                objective,
                "blocked_capability",
                reason=f"Standing authority covers this action, but {execution}.",
            )
            result["blocked"] += 1

    if result["authorized"] or result["blocked"]:
        await db.commit()
    return result


async def authorize_specific_objective(
    db: AsyncSession,
    objective_id: int,
    *,
    action_fingerprint: str,
) -> dict[str, Any]:
    objective = await db.get(VAObjective, objective_id)
    if objective is None:
        raise LookupError("objective not found")
    view = await _current_specific_view(db, objective, action_fingerprint)
    proposal = dict(view.get("proposal") or {})
    context = _loads(objective.context_json)
    context["specific_authorization"] = {
        "decision": "authorized",
        "action_fingerprint": action_fingerprint,
        "decided_at": _now().isoformat() + "Z",
    }
    objective.context_json = _dump(context)
    await write_audit(
        db,
        "va_specific_authorization_granted",
        entity_type="va_objective",
        entity_id=str(objective.id),
        details={"action_fingerprint": action_fingerprint, "action_type": proposal.get("action_type")},
    )

    from app.services.autonomous_core import _transition_objective

    execution = "authorized"
    if objective.source_type == "task" and str(objective.source_id).isdigit():
        task = await db.get(Task, int(objective.source_id))
        if task is None:
            execution = "source task is unavailable"
        elif task.source_type == "email_reply":
            execution = await _queue_authorized_email_reply(db, objective, task)
        else:
            task.requires_approval = False
    elif objective.source_type == "communication_event" and str(objective.source_id).isdigit():
        event = await db.get(CommunicationEvent, int(objective.source_id))
        execution = "source communication is unavailable" if event is None else await _queue_authorized_device_reply(db, objective, event)
        if event is not None:
            # Authorization resolves the human-decision requirement even when the
            # product still lacks an executor. The VA objective then owns that as a
            # capability gap instead of manufacturing another user task.
            event.action_required = False
            legacy_tasks = list(
                (
                    await db.execute(
                        select(Task).where(Task.source_type == "communication", Task.source_id == str(event.id))
                    )
                ).scalars()
            )
            for task in legacy_tasks:
                task.requires_approval = False
                if task.status in {"open", "waiting"}:
                    task.status = "cancelled"

    if execution == "queued":
        await _transition_objective(
            db,
            objective,
            "cancelled",
            reason="Specific authorization recorded; this decision objective was superseded by the durable executor objective.",
        )
    else:
        await _transition_objective(
            db,
            objective,
            "blocked_capability",
            reason=f"Specific authorization recorded, but {execution}.",
        )
    await db.commit()
    return {"authorized": True, "execution": execution, "objective_id": objective.id}


async def decline_specific_objective(
    db: AsyncSession,
    objective_id: int,
    *,
    action_fingerprint: str,
    reason: str = "",
) -> dict[str, Any]:
    objective = await db.get(VAObjective, objective_id)
    if objective is None:
        raise LookupError("objective not found")
    view = await _current_specific_view(db, objective, action_fingerprint)
    proposal = dict(view.get("proposal") or {})
    context = _loads(objective.context_json)
    context["specific_authorization"] = {
        "decision": "declined",
        "action_fingerprint": action_fingerprint,
        "decided_at": _now().isoformat() + "Z",
        "reason": (reason or "")[:500],
    }
    objective.context_json = _dump(context)

    if objective.source_type == "task" and str(objective.source_id).isdigit():
        task = await db.get(Task, int(objective.source_id))
        if task is not None:
            task.status = "cancelled"
            task.requires_approval = False
    elif objective.source_type == "communication_event" and str(objective.source_id).isdigit():
        event = await db.get(CommunicationEvent, int(objective.source_id))
        if event is not None:
            event.action_required = False
            legacy_tasks = list(
                (
                    await db.execute(
                        select(Task).where(Task.source_type == "communication", Task.source_id == str(event.id))
                    )
                ).scalars()
            )
            for task in legacy_tasks:
                task.status = "cancelled"
                task.requires_approval = False

    await write_audit(
        db,
        "va_specific_authorization_declined",
        entity_type="va_objective",
        entity_id=str(objective.id),
        details={
            "action_fingerprint": action_fingerprint,
            "action_type": proposal.get("action_type"),
            "reason": (reason or "")[:500],
        },
    )
    from app.services.autonomous_core import _transition_objective

    await _transition_objective(db, objective, "cancelled", reason="The specific proposed action was declined by the user.")
    await db.commit()
    return {"declined": True, "objective_id": objective.id}
