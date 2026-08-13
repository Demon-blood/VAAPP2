from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import (
    AuditLog,
    CommunicationAction,
    CommunicationDeliveryEvidence,
    BrowserOperation,
    CalendarMutation,
    GmailOutboundMessage,
    OwnAccountTransfer,
    Payment,
    Task,
    VACommunicationThread,
    VAEvent,
    VAFollowUp,
    VAObjective,
    VAObjectiveStep,
    VAOutcomeEvidence,
    WorkflowJob,
    WorkflowRun,
)
from app.services.audit import write_audit
from app.services.autonomy_metrics import autonomy_summary, increment_metric
from app.services.capability_registry import capability_for_key, capability_matrix
from app.services.va_policy import authorize_step
from app.services.workflow_engine import failure_recovery_class, requeue_dead_letter


TERMINAL_OBJECTIVE_STATES = {"completed", "cancelled", "failed"}
_JOB_CAPABILITY = {
    "gmail.sync": "email",
    "calendar.sync": "calendar",
    "browser.operation.run": "browser_portal",
    "banking.autopilot": "banking_read",
    "google.contacts.sync": "contacts",
    "connectors.rules.run": "service_connectors",
    "housekeeping.documents": "documents",
    "autopilot.plan": "ai_decisioning",
}


def utcnow() -> datetime:
    return datetime.utcnow()


def _loads(value: str | None, default: Any) -> Any:
    try:
        decoded = json.loads(value or "")
        return decoded
    except (json.JSONDecodeError, TypeError):
        return default


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


async def _transition_objective(
    db: AsyncSession,
    objective: VAObjective,
    status: str,
    *,
    reason: str = "",
    error: str = "",
) -> None:
    previous = objective.status
    if previous == status and (not reason or objective.blocked_reason == reason or objective.needs_user_reason == reason):
        return
    objective.status = status
    if status == "needs_user":
        objective.needs_user_reason = reason
        objective.blocked_reason = ""
    elif status.startswith("blocked"):
        objective.blocked_reason = reason
        objective.needs_user_reason = ""
    elif status not in {"needs_user"}:
        objective.needs_user_reason = ""
        if status not in {"blocked_capability", "blocked_system"}:
            objective.blocked_reason = ""
    if error:
        objective.last_error = error[:8000]
    if status in TERMINAL_OBJECTIVE_STATES:
        objective.finished_at = objective.finished_at or utcnow()
    elif previous in TERMINAL_OBJECTIVE_STATES:
        objective.finished_at = None
    await write_audit(
        db,
        "va_objective_state_changed",
        entity_type="va_objective",
        entity_id=str(objective.id),
        result="blocked" if status.startswith("blocked") or status == "needs_user" else "success",
        details={"from": previous, "to": status, "reason": reason, "error": error[:2000]},
    )
    if previous != status:
        if status == "completed":
            await increment_metric(db, "objectives_completed")
        elif status == "needs_user":
            objective.user_intervention_count = int(objective.user_intervention_count or 0) + 1
            await increment_metric(db, "user_interventions")


async def record_event(
    db: AsyncSession,
    *,
    event_key: str,
    source_type: str,
    source_id: str,
    event_type: str,
    title: str,
    payload: dict[str, Any] | None = None,
    occurred_at: datetime | None = None,
) -> tuple[VAEvent, bool]:
    event_key = event_key.strip()[:255]
    existing = (
        await db.execute(select(VAEvent).where(VAEvent.event_key == event_key).limit(1))
    ).scalar_one_or_none()
    if existing is not None:
        return existing, False

    event = VAEvent(
        event_key=event_key,
        source_type=source_type[:80],
        source_id=source_id[:255],
        event_type=event_type[:120],
        title=title,
        payload_json=_dump(payload or {}),
        status="new",
        occurred_at=occurred_at or utcnow(),
    )
    try:
        async with db.begin_nested():
            db.add(event)
            await db.flush()
    except IntegrityError:
        existing = (
            await db.execute(select(VAEvent).where(VAEvent.event_key == event_key).limit(1))
        ).scalar_one()
        return existing, False

    await increment_metric(db, "events_ingested")
    await write_audit(
        db,
        "va_event_ingested",
        entity_type="va_event",
        entity_id=str(event.id),
        details={
            "event_key": event.event_key,
            "source_type": event.source_type,
            "source_id": event.source_id,
            "event_type": event.event_type,
        },
    )
    return event, True


async def create_manual_run_event(db: AsyncSession) -> VAEvent:
    token = uuid4().hex
    event, _ = await record_event(
        db,
        event_key=f"manual-va-run:{token}",
        source_type="manual",
        source_id=token,
        event_type="manual_run_requested",
        title="Run the full VA automation cycle",
        payload={"requested_at": utcnow().isoformat() + "Z"},
    )
    await db.commit()
    return event


async def seed_system_events(db: AsyncSession) -> dict[str, int]:
    """Ingest currently actionable durable state into one event stream.

    This does not execute or simulate future-phase capabilities. It only creates
    durable facts that the objective engine can own and reconcile.
    """

    created = {"tasks": 0, "payments": 0, "transfers": 0, "workflow_blockers": 0, "system_failures": 0}

    tasks = list(
        (
            await db.execute(
                select(Task)
                .where(Task.status.in_(["open", "waiting"]))
                .order_by(Task.id.asc())
                .limit(500)
            )
        ).scalars()
    )
    for task in tasks:
        event_type = "task_needs_decision" if task.requires_approval else "task_pending"
        _, was_created = await record_event(
            db,
            event_key=f"task:{task.id}",
            source_type="task",
            source_id=str(task.id),
            event_type=event_type,
            title=task.title,
            payload={
                "description": task.description,
                "task_source_type": task.source_type,
                "task_source_id": task.source_id,
                "priority": task.priority,
                "due_at": task.due_at,
                "requires_approval": task.requires_approval,
            },
            occurred_at=task.created_at,
        )
        created["tasks"] += int(was_created)

    payments = list(
        (
            await db.execute(
                select(Payment)
                .where(Payment.requires_user_action.is_(True), Payment.status.not_in(["completed", "failed", "cancelled", "rejected"]))
                .order_by(Payment.id.asc())
                .limit(250)
            )
        ).scalars()
    )
    for payment in payments:
        _, was_created = await record_event(
            db,
            event_key=f"payment:{payment.id}:authorization",
            source_type="payment",
            source_id=str(payment.id),
            event_type="payment_authorization_required",
            title=f"Bank authorization required for payment {payment.id}",
            payload={
                "bill_id": payment.bill_id,
                "amount": str(payment.amount),
                "currency": payment.currency,
                "authorization_url": payment.authorization_url,
                "status": payment.status,
            },
            occurred_at=payment.created_at,
        )
        created["payments"] += int(was_created)

    transfers = list(
        (
            await db.execute(
                select(OwnAccountTransfer)
                .where(
                    OwnAccountTransfer.requires_user_action.is_(True),
                    OwnAccountTransfer.status.not_in(["completed", "failed", "cancelled", "rejected"]),
                )
                .order_by(OwnAccountTransfer.id.asc())
                .limit(250)
            )
        ).scalars()
    )
    for transfer in transfers:
        _, was_created = await record_event(
            db,
            event_key=f"own-transfer:{transfer.id}:authorization",
            source_type="own_account_transfer",
            source_id=str(transfer.id),
            event_type="payment_authorization_required",
            title=f"Bank authorization required for own-account transfer {transfer.id}",
            payload={
                "amount": str(transfer.amount),
                "currency": transfer.currency,
                "authorization_url": transfer.authorization_url,
                "status": transfer.status,
                "source_account_id": transfer.source_account_id,
                "destination_account_id": transfer.destination_account_id,
            },
            occurred_at=transfer.created_at,
        )
        created["transfers"] += int(was_created)

    dead_letters = list(
        (
            await db.execute(
                select(WorkflowJob)
                .where(WorkflowJob.status == "dead_letter")
                .order_by(WorkflowJob.id.asc())
                .limit(250)
            )
        ).scalars()
    )
    for job in dead_letters:
        recovery_class = failure_recovery_class(job.job_type, job.last_error)
        event_type = "workflow_user_blocker" if recovery_class == "user_required" else "workflow_system_failure"
        _, was_created = await record_event(
            db,
            event_key=f"workflow-job:{job.id}:dead-letter",
            source_type="workflow_job",
            source_id=str(job.id),
            event_type=event_type,
            title=f"Workflow exception: {job.job_type}",
            payload={
                "job_type": job.job_type,
                "last_error": job.last_error,
                "recovery_class": recovery_class,
                "attempts": job.attempts,
                "max_attempts": job.max_attempts,
            },
            occurred_at=job.finished_at or job.updated_at,
        )
        if recovery_class == "user_required":
            created["workflow_blockers"] += int(was_created)
        else:
            created["system_failures"] += int(was_created)

    if any(created.values()):
        await db.commit()
    return created


async def _create_objective(
    db: AsyncSession,
    event: VAEvent,
    *,
    title: str,
    goal: str,
    category: str,
    priority: str = "normal",
    risk_level: str = "low",
    status: str = "detected",
    reason: str = "",
) -> tuple[VAObjective, bool]:
    correlation_key = f"event:{event.event_key}"[:255]
    existing = (
        await db.execute(select(VAObjective).where(VAObjective.correlation_key == correlation_key).limit(1))
    ).scalar_one_or_none()
    if existing is not None:
        return existing, False
    payload = _loads(event.payload_json, {})
    due_at = None
    if isinstance(payload, dict) and payload.get("due_at"):
        try:
            due_at = datetime.fromisoformat(str(payload["due_at"]).replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            due_at = None
    objective = VAObjective(
        correlation_key=correlation_key,
        source_event_id=event.id,
        source_type=event.source_type,
        source_id=event.source_id,
        title=title,
        goal=goal,
        category=category,
        priority=priority,
        risk_level=risk_level,
        status=status,
        due_at=due_at,
        context_json=_dump(payload if isinstance(payload, dict) else {}),
        needs_user_reason=reason if status == "needs_user" else "",
        user_intervention_count=1 if status == "needs_user" else 0,
        blocked_reason=reason if status.startswith("blocked") else "",
    )
    try:
        async with db.begin_nested():
            db.add(objective)
            await db.flush()
    except IntegrityError:
        existing = (
            await db.execute(select(VAObjective).where(VAObjective.correlation_key == correlation_key).limit(1))
        ).scalar_one()
        return existing, False
    await increment_metric(db, "objectives_created")
    if status == "needs_user":
        await increment_metric(db, "user_interventions")
    await write_audit(
        db,
        "va_objective_created",
        entity_type="va_objective",
        entity_id=str(objective.id),
        details={
            "correlation_key": objective.correlation_key,
            "source_event_id": event.id,
            "category": category,
            "status": status,
        },
    )
    return objective, True


async def _ensure_step(
    db: AsyncSession,
    objective: VAObjective,
    *,
    position: int,
    action_type: str,
    parameters: dict[str, Any],
    verification_type: str,
) -> VAObjectiveStep:
    existing = (
        await db.execute(
            select(VAObjectiveStep).where(
                VAObjectiveStep.objective_id == objective.id,
                VAObjectiveStep.position == position,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    key = f"va-objective:{objective.id}:step:{position}:{action_type}"[:255]
    step = VAObjectiveStep(
        objective_id=objective.id,
        position=position,
        action_type=action_type,
        idempotency_key=key,
        parameters_json=_dump(parameters),
        verification_type=verification_type,
        status="pending",
    )
    db.add(step)
    await db.flush()
    return step



async def _max_step_position(db: AsyncSession, objective_id: int) -> int:
    value = (
        await db.execute(
            select(func.max(VAObjectiveStep.position)).where(VAObjectiveStep.objective_id == objective_id)
        )
    ).scalar_one_or_none()
    return int(value or 0)


def _followup_body(previous_body: str) -> str:
    lower = (previous_body or "").casefold()
    if any(word in lower for word in (" graag ", " bedankt", " vriendelijke groet", "met vriendelijke", "kun je", "kunt u")):
        return "Even een korte opvolging van mijn vorige bericht. Laat je me weten wanneer je de kans hebt?"
    if any(word in lower for word in (" cordialement", " merci", " pouvez-vous", " pourriez-vous")):
        return "Petit suivi de mon message précédent. Pourriez-vous me tenir au courant lorsque vous en aurez l'occasion ?"
    if any(word in lower for word in (" vielen dank", " freundliche grüße", " können sie", " könntest du")):
        return "Kurze Nachfrage zu meiner vorherigen Nachricht. Gib mir bitte Bescheid, sobald du Gelegenheit dazu hast."
    return "Just following up on my previous message. Please let me know when you have a chance."


async def _handle_response_event(db: AsyncSession, event: VAEvent, payload: dict[str, Any]) -> VAObjective:
    prior_id = int(payload.get("prior_objective_id") or 0)
    prior = await db.get(VAObjective, prior_id) if prior_id > 0 else None
    if prior is None:
        objective, _ = await _create_objective(
            db,
            event,
            title=event.title,
            goal="Record the received counterparty response.",
            category="communication_response",
            status="completed",
        )
        await _add_evidence(
            db,
            objective,
            step=None,
            evidence_type="counterparty_response",
            provider=str(payload.get("provider") or payload.get("channel") or "communication"),
            external_ref=str(payload.get("message_id") or payload.get("communication_event_id") or event.source_id),
            details=payload,
        )
        objective.finished_at = objective.finished_at or utcnow()
        return objective

    # A real counterparty response satisfies any pending chase for this objective.
    # Keep this cancellation in the autonomous core as the final ownership boundary,
    # so every response-event path (not only Gmail thread registration) is safe.
    pending_followups = list(
        (
            await db.execute(
                select(VAFollowUp).where(
                    VAFollowUp.objective_id == prior.id,
                    VAFollowUp.status.in_(["pending", "due"]),
                )
            )
        ).scalars()
    )
    for followup in pending_followups:
        followup.status = "cancelled"

    waiting_steps = list(
        (
            await db.execute(
                select(VAObjectiveStep).where(
                    VAObjectiveStep.objective_id == prior.id,
                    VAObjectiveStep.action_type == "wait",
                    VAObjectiveStep.status.in_(["pending", "waiting", "retry"]),
                )
            )
        ).scalars()
    )
    await _add_evidence(
        db,
        prior,
        step=None,
        evidence_type="counterparty_response",
        provider=str(payload.get("provider") or payload.get("channel") or "communication"),
        external_ref=str(payload.get("message_id") or payload.get("communication_event_id") or event.source_id),
        details=payload,
    )
    for step in waiting_steps:
        step.status = "completed"
        step.finished_at = utcnow()
        step.outcome_json = _dump({"counterparty_response": True, "event_id": event.id})
    await _finish_if_all_steps_complete(db, prior)
    if prior.status != "completed":
        remaining_non_wait = int(
            (
                await db.execute(
                    select(func.count(VAObjectiveStep.id)).where(
                        VAObjectiveStep.objective_id == prior.id,
                        VAObjectiveStep.action_type != "wait",
                        VAObjectiveStep.status != "completed",
                    )
                )
            ).scalar_one()
        )
        if remaining_non_wait:
            await _transition_objective(
                db,
                prior,
                "verifying",
                reason="Counterparty responded; remaining provider postconditions are still being verified",
            )
        else:
            await _transition_objective(db, prior, "completed", reason="Counterparty response received and the waiting objective was satisfied")

    thread_id = int(payload.get("thread_record_id") or 0)
    if thread_id > 0:
        thread = await db.get(VACommunicationThread, thread_id)
        if thread is not None and thread.objective_id == prior.id:
            thread.objective_id = None
            thread.waiting_on = "va"
            thread.status = "open"
            thread.next_follow_up_at = None

    event.status = "processed"
    event.processed_at = utcnow()
    await db.commit()
    return prior


async def _handle_calendar_response_event(db: AsyncSession, event: VAEvent, payload: dict[str, Any]) -> VAObjective:
    prior_id = int(payload.get("prior_objective_id") or 0)
    prior = await db.get(VAObjective, prior_id) if prior_id > 0 else None
    if prior is None:
        objective, _ = await _create_objective(
            db,
            event,
            title=event.title,
            goal="Record the observed Calendar attendee response.",
            category="calendar_response",
            status="completed",
        )
        await _add_evidence(
            db,
            objective,
            step=None,
            evidence_type="calendar_attendee_response",
            provider="google_calendar",
            external_ref=str(payload.get("provider_event_id") or event.source_id),
            details=payload,
        )
        objective.finished_at = objective.finished_at or utcnow()
        event.status = "processed"
        event.processed_at = utcnow()
        await db.commit()
        return objective

    followups = list(
        (
            await db.execute(
                select(VAFollowUp).where(
                    VAFollowUp.objective_id == prior.id,
                    VAFollowUp.status.in_(["pending", "due", "dispatching"]),
                )
            )
        ).scalars()
    )
    for followup in followups:
        followup.status = "cancelled"

    waiting_steps = list(
        (
            await db.execute(
                select(VAObjectiveStep).where(
                    VAObjectiveStep.objective_id == prior.id,
                    VAObjectiveStep.action_type == "wait",
                    VAObjectiveStep.status.in_(["pending", "waiting", "retry"]),
                )
            )
        ).scalars()
    )
    await _add_evidence(
        db,
        prior,
        step=None,
        evidence_type="calendar_attendee_response",
        provider="google_calendar",
        external_ref=str(payload.get("provider_event_id") or event.source_id),
        details=payload,
    )
    for step in waiting_steps:
        step.status = "completed"
        step.finished_at = utcnow()
        step.outcome_json = _dump({"calendar_attendee_response": True, "event_id": event.id})
    await _finish_if_all_steps_complete(db, prior)
    if prior.status != "completed":
        remaining_non_wait = int(
            (
                await db.execute(
                    select(func.count(VAObjectiveStep.id)).where(
                        VAObjectiveStep.objective_id == prior.id,
                        VAObjectiveStep.action_type != "wait",
                        VAObjectiveStep.status != "completed",
                    )
                )
            ).scalar_one()
        )
        if remaining_non_wait:
            await _transition_objective(
                db,
                prior,
                "verifying",
                reason="Calendar attendees responded; remaining provider postconditions are still being verified",
            )
        else:
            await _transition_objective(
                db,
                prior,
                "completed",
                reason="Calendar attendees responded and the scheduling objective is satisfied",
            )
    event.status = "processed"
    event.processed_at = utcnow()
    await db.commit()
    return prior


async def _handle_followup_event(db: AsyncSession, event: VAEvent, payload: dict[str, Any]) -> VAObjective:
    followup = await db.get(VAFollowUp, int(event.source_id)) if event.source_id.isdigit() else None
    objective_id = int(payload.get("objective_id") or (followup.objective_id if followup else 0) or 0)
    objective = await db.get(VAObjective, objective_id) if objective_id > 0 else None
    if objective is None or followup is None:
        created, _ = await _create_objective(
            db,
            event,
            title=event.title,
            goal=str(payload.get("purpose") or event.title),
            category="follow_up",
            status="blocked_system",
            reason="The persisted follow-up no longer references a valid objective.",
        )
        return created

    # Reaching the follow-up deadline satisfies the previous waiting step; the same
    # objective continues to own the conversation through the chase and next wait.
    waiting_steps = list(
        (
            await db.execute(
                select(VAObjectiveStep).where(
                    VAObjectiveStep.objective_id == objective.id,
                    VAObjectiveStep.action_type == "wait",
                    VAObjectiveStep.status.in_(["pending", "waiting"]),
                )
            )
        ).scalars()
    )
    for waiting in waiting_steps:
        waiting.status = "completed"
        waiting.finished_at = utcnow()
        waiting.outcome_json = _dump({"follow_up_due": followup.id, "attempt": followup.attempts})

    channel = str(followup.channel or payload.get("channel") or "").lower()
    saved = _loads(followup.payload_json, {})
    saved = saved if isinstance(saved, dict) else {}
    position = await _max_step_position(db, objective.id) + 1
    if channel == "email":
        body = str(saved.get("follow_up_body") or _followup_body(str(saved.get("previous_body") or "")))
        await _ensure_step(
            db,
            objective,
            position=position,
            action_type="gmail_send_followup",
            parameters={
                "thread_record_id": int(saved.get("thread_record_id") or payload.get("thread_record_id") or 0),
                "recipient": followup.target,
                "subject": str(saved.get("subject") or "Re: Follow-up"),
                "body": body,
                "gmail_thread_id": str(saved.get("gmail_thread_id") or ""),
                "source_message_id": str(saved.get("source_message_id") or ""),
                "source_rfc_message_id": str(saved.get("source_rfc_message_id") or ""),
                "expect_reply": True,
                "follow_up_hours": int(followup.recurrence_hours or 48),
                "follow_up_id": followup.id,
                "follow_up_attempt": followup.attempts,
                "max_follow_up_attempts": followup.max_attempts,
            },
            verification_type="gmail_outbound_verified",
        )
        await _ensure_step(
            db,
            objective,
            position=position + 1,
            action_type="wait",
            parameters={"reason": "counterparty_response", "thread_record_id": int(saved.get("thread_record_id") or 0)},
            verification_type="counterparty_response",
        )
        followup.status = "dispatching"
        await _transition_objective(db, objective, "planned", reason="Follow-up deadline reached; automatic email chase queued")
    elif channel == "sms":
        # SMS can be initiated from the paired Android device even without an active
        # notification.  Persist a CommunicationAction first; the device worker owns
        # actual carrier dispatch and sent/delivery evidence.
        text = str(saved.get("follow_up_body") or _followup_body(str(saved.get("previous_body") or "")))
        action_key = f"followup:{followup.id}:attempt:{followup.attempts}:sms"
        action = (
            await db.execute(
                select(CommunicationAction).where(CommunicationAction.idempotency_key == action_key).limit(1)
            )
        ).scalar_one_or_none()
        if action is None:
            action = CommunicationAction(
                event_id=int(saved.get("communication_event_id") or 0),
                action_type="reply",
                target=followup.target,
                payload_json=_dump({"text": text, "channel": "sms", "follow_up_id": followup.id}),
                idempotency_key=action_key,
                status="pending",
                requires_user_action=False,
            )
            db.add(action)
            await db.flush()
        await _ensure_step(
            db,
            objective,
            position=position,
            action_type="device_followup_action",
            parameters={
                "communication_action_id": action.id,
                "channel": "sms",
                "thread_record_id": int(saved.get("thread_record_id") or 0),
                "target": followup.target,
                "expect_reply": True,
                "follow_up_hours": int(followup.recurrence_hours or 48),
                "follow_up_id": followup.id,
                "follow_up_attempt": followup.attempts,
                "max_follow_up_attempts": followup.max_attempts,
            },
            verification_type="device_action_verified",
        )
        await _ensure_step(
            db,
            objective,
            position=position + 1,
            action_type="wait",
            parameters={"reason": "counterparty_response", "thread_record_id": int(saved.get("thread_record_id") or 0)},
            verification_type="counterparty_response",
        )
        followup.status = "dispatching"
        await _transition_objective(db, objective, "planned", reason="Follow-up deadline reached; automatic SMS chase queued")
    else:
        # Android RemoteInput is a reply to an extant notification. It cannot safely
        # initiate a future WhatsApp/Signal/Telegram/Messenger message once that
        # notification action no longer exists. Do not pretend otherwise.
        followup.status = "blocked_capability"
        await _transition_objective(
            db,
            objective,
            "blocked_capability",
            reason=f"Automatic future {channel or 'notification-app'} follow-up requires an initiator API/browser executor; RemoteInput only replies to a live notification.",
        )

    event.status = "processed"
    event.processed_at = utcnow()
    await db.commit()
    return objective


async def objective_from_event(db: AsyncSession, event: VAEvent) -> VAObjective:
    payload = _loads(event.payload_json, {})
    payload = payload if isinstance(payload, dict) else {}

    if event.event_type == "manual_run_requested":
        objective, _ = await _create_objective(
            db,
            event,
            title="Run the VA now",
            goal="Run all currently configured safe VA automation stages and verify the durable workflow result.",
            category="operations",
            priority="high",
            status="planned",
        )
        await _ensure_step(
            db,
            objective,
            position=1,
            action_type="workflow_intent",
            parameters={"intent_type": "run_va", "payload": {}},
            verification_type="workflow_run_completed",
        )
        objective.plan_json = _dump({"steps": 1, "source": "deterministic_core"})
    elif event.event_type == "payment_authorization_required":
        objective, _ = await _create_objective(
            db,
            event,
            title=event.title,
            goal="Resume the already-created real bank operation after the bank-required authorization succeeds.",
            category="bank_authorization",
            priority="high",
            risk_level="high",
            status="needs_user",
            reason="The bank/provider requires strong customer authentication for this already-created operation.",
        )
    elif event.event_type == "workflow_user_blocker":
        objective, _ = await _create_objective(
            db,
            event,
            title=event.title,
            goal="Restore the provider connection/authorization and allow the durable workflow to continue.",
            category="provider_authentication",
            priority="high",
            status="needs_user",
            reason=str(payload.get("last_error") or "Provider authentication or configuration requires user action"),
        )
    elif event.event_type == "workflow_system_failure":
        objective, _ = await _create_objective(
            db,
            event,
            title=event.title,
            goal="Recover the failed workflow without repeating an ambiguous external action.",
            category="system_recovery",
            priority="high",
            status="blocked_system",
            reason=str(payload.get("last_error") or "Workflow is dead-lettered and requires system recovery"),
        )
    elif event.event_type == "task_needs_decision":
        objective, _ = await _create_objective(
            db,
            event,
            title=event.title,
            goal=str(payload.get("description") or event.title),
            category=str(payload.get("task_source_type") or "task"),
            priority=str(payload.get("priority") or "normal"),
            risk_level="high",
            status="needs_user",
            reason="The existing task is explicitly marked as requiring a material user decision/approval.",
        )
    elif event.event_type == "task_pending":
        objective, _ = await _create_objective(
            db,
            event,
            title=event.title,
            goal=str(payload.get("description") or event.title),
            category=str(payload.get("task_source_type") or "task"),
            priority=str(payload.get("priority") or "normal"),
            status="blocked_capability",
            reason="The task is now durably owned by the VA, but its domain executor has not yet been migrated into the unified objective engine.",
        )
    elif event.event_type == "email_reply_planned":
        objective, _ = await _create_objective(
            db,
            event,
            title=event.title,
            goal=f"Send and verify the safe Gmail reply to {payload.get('recipient') or payload.get('sender') or 'the counterparty'}.",
            category="email_reply",
            priority=str(payload.get("priority") or "normal"),
            status="planned",
        )
        thread_record_id = int(payload.get("thread_record_id") or 0)
        if thread_record_id > 0:
            from app.services.communication_ownership import link_thread_objective
            await link_thread_objective(db, thread_record_id=thread_record_id, objective_id=objective.id)
        await _ensure_step(
            db,
            objective,
            position=1,
            action_type="gmail_send_reply",
            parameters={
                "thread_record_id": thread_record_id,
                "recipient": str(payload.get("recipient") or payload.get("sender") or ""),
                "subject": str(payload.get("subject") or ""),
                "body": str(payload.get("body") or ""),
                "gmail_thread_id": str(payload.get("gmail_thread_id") or ""),
                "source_message_id": str(payload.get("source_message_id") or event.source_id),
                "source_rfc_message_id": str(payload.get("source_rfc_message_id") or ""),
                "expect_reply": bool(payload.get("expect_reply")),
                "follow_up_hours": int(payload.get("follow_up_hours") or 48),
                "policy": str(payload.get("policy") or "safe_autonomous_reply"),
            },
            verification_type="gmail_outbound_verified",
        )
        if bool(payload.get("expect_reply")):
            await _ensure_step(
                db,
                objective,
                position=2,
                action_type="wait",
                parameters={"reason": "counterparty_response", "thread_record_id": thread_record_id},
                verification_type="counterparty_response",
            )
        objective.plan_json = _dump({"steps": 2 if payload.get("expect_reply") else 1, "source": "communications_ownership", "expect_reply": bool(payload.get("expect_reply"))})
    elif event.event_type == "calendar_event_planned":
        operation = str(payload.get("operation") or "create").lower()
        provider_event_id = str(payload.get("provider_event_id") or "")
        attendees = payload.get("attendees") if isinstance(payload.get("attendees"), list) else []
        expect_response = bool(payload.get("expect_response")) and bool(attendees) and operation in {"create", "update"}
        objective, _ = await _create_objective(
            db,
            event,
            title=event.title,
            goal=f"{operation.title()} and verify the Google Calendar event {payload.get('summary') or provider_event_id or 'requested event'}.",
            category="calendar_scheduling",
            priority=str(payload.get("priority") or "normal"),
            risk_level=str(payload.get("risk_level") or "low"),
            status="planned",
        )
        await _ensure_step(
            db,
            objective,
            position=1,
            action_type="calendar_mutation",
            parameters={
                **payload,
                "operation": operation,
                "provider_event_id": provider_event_id,
                "expect_response": expect_response,
            },
            verification_type="calendar_mutation_verified",
        )
        if expect_response:
            await _ensure_step(
                db,
                objective,
                position=2,
                action_type="wait",
                parameters={"reason": "calendar_attendee_response", "provider_event_id": provider_event_id},
                verification_type="calendar_attendee_response",
            )
        objective.plan_json = _dump({
            "steps": 2 if expect_response else 1,
            "source": "calendar_ownership",
            "operation": operation,
            "expect_response": expect_response,
        })
    elif event.event_type == "calendar_attendee_response_received":
        return await _handle_calendar_response_event(db, event, payload)
    elif event.event_type == "browser_portal_operation_planned":
        operation_id = int(payload.get("browser_operation_id") or 0)
        portal_id = int(payload.get("portal_id") or 0)
        material_commitment = bool(payload.get("material_commitment"))
        objective, _ = await _create_objective(
            db,
            event,
            title=event.title,
            goal=str(payload.get("goal") or event.title),
            category="browser_portal",
            priority=str(payload.get("priority") or "normal"),
            risk_level="critical" if material_commitment else str(payload.get("risk_level") or "low"),
            status="planned",
        )
        await _ensure_step(
            db,
            objective,
            position=1,
            action_type="browser_operation",
            parameters={
                "browser_operation_id": operation_id,
                "portal_id": portal_id,
                "material_commitment": material_commitment,
            },
            verification_type="browser_operation_verified",
        )
        objective.plan_json = _dump({
            "steps": 1,
            "source": "secure_browser_operator",
            "browser_operation_id": operation_id,
            "portal_id": portal_id,
            "material_commitment": material_commitment,
        })
    elif event.event_type == "device_reply_planned":
        protected = bool(payload.get("protected"))
        objective, _ = await _create_objective(
            db,
            event,
            title=event.title,
            goal=f"Send and verify the safe {payload.get('channel') or 'device'} reply through the paired Android device.",
            category="communication_reply",
            priority=str(payload.get("priority") or "normal"),
            risk_level="high" if protected else "low",
            status="needs_user" if protected else "planned",
            reason="Protected communication requires a material user decision." if protected else "",
        )
        if not protected:
            thread_record_id = int(payload.get("thread_record_id") or 0)
            if thread_record_id > 0:
                from app.services.communication_ownership import link_thread_objective
                await link_thread_objective(db, thread_record_id=thread_record_id, objective_id=objective.id)
            await _ensure_step(
                db,
                objective,
                position=1,
                action_type="device_communication_action",
                parameters={
                    "communication_action_id": int(payload.get("communication_action_id") or 0),
                    "communication_event_id": int(payload.get("communication_event_id") or 0),
                    "thread_record_id": thread_record_id,
                    "channel": str(payload.get("channel") or ""),
                    "provider": str(payload.get("provider") or ""),
                    "target": str(payload.get("target") or ""),
                    "expect_reply": bool(payload.get("expect_reply")),
                    "follow_up_hours": int(payload.get("follow_up_hours") or 48),
                },
                verification_type="device_action_verified",
            )
            if bool(payload.get("expect_reply")):
                await _ensure_step(
                    db,
                    objective,
                    position=2,
                    action_type="wait",
                    parameters={"reason": "counterparty_response", "thread_record_id": thread_record_id},
                    verification_type="counterparty_response",
                )
            objective.plan_json = _dump({"steps": 2 if payload.get("expect_reply") else 1, "source": "communications_ownership", "expect_reply": bool(payload.get("expect_reply"))})
    elif event.event_type == "communication_response_received":
        return await _handle_response_event(db, event, payload)
    elif event.event_type in {"email_actionable", "communication_actionable"}:
        objective, _ = await _create_objective(
            db,
            event,
            title=event.title,
            goal=str(payload.get("reasoning_summary") or event.title),
            category=str(payload.get("category") or event.event_type),
            priority=str(payload.get("priority") or "normal"),
            status="blocked_capability",
            reason="The message is durably owned by the VA, but its requested domain action belongs to a later executor phase; it is not a user approval request.",
        )
        thread_record_id = int(payload.get("thread_record_id") or 0)
        if thread_record_id > 0:
            from app.services.communication_ownership import link_thread_objective
            await link_thread_objective(db, thread_record_id=thread_record_id, objective_id=objective.id)
    elif event.event_type == "followup_due":
        return await _handle_followup_event(db, event, payload)
    else:
        objective, _ = await _create_objective(
            db,
            event,
            title=event.title,
            goal=event.title,
            category=event.event_type,
            status="blocked_capability",
            reason=f"No Phase-1 executor is registered for event type {event.event_type}.",
        )

    event.status = "processed"
    event.processed_at = utcnow()
    await db.commit()
    return objective


async def process_pending_events(db: AsyncSession, *, limit: int = 100) -> int:
    events = list(
        (
            await db.execute(
                select(VAEvent)
                .where(VAEvent.status == "new")
                .order_by(VAEvent.occurred_at.asc().nullsfirst(), VAEvent.id.asc())
                .limit(max(1, min(limit, 500)))
            )
        ).scalars()
    )
    for event in events:
        await objective_from_event(db, event)
    return len(events)


async def _add_evidence(
    db: AsyncSession,
    objective: VAObjective,
    *,
    step: VAObjectiveStep | None,
    evidence_type: str,
    provider: str,
    external_ref: str,
    details: dict[str, Any],
) -> VAOutcomeEvidence:
    evidence = VAOutcomeEvidence(
        objective_id=objective.id,
        step_id=step.id if step else None,
        evidence_type=evidence_type,
        provider=provider,
        external_ref=external_ref,
        details_json=_dump(details),
    )
    db.add(evidence)
    await db.flush()
    await write_audit(
        db,
        "va_outcome_verified",
        entity_type="va_objective",
        entity_id=str(objective.id),
        details={
            "step_id": step.id if step else None,
            "evidence_type": evidence_type,
            "provider": provider,
            "external_ref": external_ref,
        },
    )
    return evidence


async def _execute_step(db: AsyncSession, objective: VAObjective, step: VAObjectiveStep) -> None:
    params = _loads(step.parameters_json, {})
    params = params if isinstance(params, dict) else {}
    decision = await authorize_step(db, objective=objective, action_type=step.action_type, parameters=params)
    step.policy_json = _dump(decision)
    if decision.get("capability"):
        step.capability_json = _dump(decision["capability"])

    if not decision.get("allowed"):
        reason = str(decision.get("reason") or "Step is not authorized")
        step.last_error = reason
        if decision.get("needs_user"):
            step.status = "blocked_user"
            await _transition_objective(db, objective, "needs_user", reason=reason)
        else:
            step.status = "blocked_capability"
            status = "blocked_system" if decision.get("resolution") in {"unsupported_action", "system_disabled"} else "blocked_capability"
            await _transition_objective(db, objective, status, reason=reason)
        await db.commit()
        return

    step.attempts += 1
    step.status = "running"
    await _transition_objective(db, objective, "executing")
    await db.commit()

    try:
        if step.action_type == "workflow_intent":
            # Import lazily to avoid creating a module cycle with the workflow engine.
            from app.services.autopilot_service import dispatch_intent

            intent_type = str(params.get("intent_type") or "")
            result = await dispatch_intent(
                db,
                {
                    "type": intent_type,
                    "payload": params.get("payload") if isinstance(params.get("payload"), dict) else {},
                    "correlation_key": step.idempotency_key,
                    "priority": 20,
                },
            )
            workflow_id = int(result.get("workflow_id") or 0)
            if workflow_id <= 0:
                raise RuntimeError("Durable workflow dispatch did not return a workflow_id")
            step.workflow_run_id = workflow_id
            step.external_ref = f"workflow:{workflow_id}"
            step.outcome_json = _dump(result)
            step.status = "verifying"
            step.run_after = utcnow() + timedelta(seconds=5)
            await _transition_objective(db, objective, "verifying")
            await write_audit(
                db,
                "va_objective_step_dispatched",
                entity_type="va_objective",
                entity_id=str(objective.id),
                details={"step_id": step.id, "action_type": step.action_type, "workflow_run_id": workflow_id},
            )
        elif step.action_type in {"gmail_send_reply", "gmail_send_followup"}:
            from app.services.gmail_delivery import prepare_gmail_outbound, send_or_reconcile_gmail_outbound

            outbound = await prepare_gmail_outbound(
                db,
                idempotency_key=step.idempotency_key,
                recipient=str(params.get("recipient") or ""),
                subject=str(params.get("subject") or ""),
                body=str(params.get("body") or ""),
                objective_id=objective.id,
                step_id=step.id,
                source_message_id=str(params.get("source_message_id") or ""),
                gmail_thread_id=str(params.get("gmail_thread_id") or ""),
                in_reply_to=str(params.get("source_rfc_message_id") or ""),
                references=str(params.get("source_rfc_message_id") or ""),
            )
            step.external_ref = f"gmail-outbound:{outbound.id}"
            outbound = await send_or_reconcile_gmail_outbound(db, outbound)
            step.outcome_json = _dump({"gmail_outbound_id": outbound.id, "status": outbound.status})
            if outbound.status == "failed_user":
                step.status = "blocked_user"
                step.last_error = outbound.last_error
                await _transition_objective(db, objective, "needs_user", reason=outbound.last_error or "Gmail authorization is required")
            elif outbound.status == "failed":
                step.status = "failed"
                step.finished_at = utcnow()
                step.last_error = outbound.last_error
                await _transition_objective(db, objective, "blocked_system", reason=outbound.last_error or "Gmail send failed without a verified outcome")
            else:
                step.status = "verifying"
                step.run_after = utcnow() if outbound.status == "verified" else max(outbound.verify_after, utcnow() + timedelta(seconds=5))
                await _transition_objective(db, objective, "verifying")
        elif step.action_type in {"device_communication_action", "device_followup_action"}:
            action_id = int(params.get("communication_action_id") or 0)
            action = await db.get(CommunicationAction, action_id) if action_id > 0 else None
            if action is None:
                raise RuntimeError("Persisted Android communication action no longer exists")
            step.external_ref = f"communication-action:{action.id}"
            step.outcome_json = _dump({"communication_action_id": action.id, "status": action.status})
            if action.status == "failed":
                step.status = "retry" if step.attempts < step.max_attempts else "failed"
                step.last_error = action.failure_reason or "Device communication action failed"
                step.run_after = utcnow() + timedelta(seconds=30)
                await _transition_objective(db, objective, "waiting" if step.status == "retry" else "blocked_system", reason=step.last_error)
            elif action.status == "cancelled":
                step.status = "failed"
                step.finished_at = utcnow()
                await _transition_objective(db, objective, "blocked_system", reason="Device communication action was cancelled before verified dispatch")
            else:
                # Backend never impersonates the device. The persisted action is
                # observed until the Android bridge posts real carrier/RemoteInput evidence.
                step.status = "verifying"
                step.run_after = utcnow() + timedelta(seconds=10)
                await _transition_objective(db, objective, "verifying")
        elif step.action_type == "calendar_mutation":
            from app.services.calendar_ownership import prepare_calendar_mutation, send_or_reconcile_calendar_mutation

            operation = str(params.get("operation") or "create").lower()
            desired = dict(params)
            desired.pop("operation", None)
            provider_event_id = str(params.get("provider_event_id") or "")
            mutation = await prepare_calendar_mutation(
                db,
                idempotency_key=step.idempotency_key,
                operation=operation,
                desired_event=desired,
                objective_id=objective.id,
                step_id=step.id,
                provider_event_id=provider_event_id,
            )
            step.external_ref = f"calendar-mutation:{mutation.id}"
            mutation = await send_or_reconcile_calendar_mutation(db, mutation)
            step.outcome_json = _dump(
                {
                    "calendar_mutation_id": mutation.id,
                    "provider_event_id": mutation.provider_event_id,
                    "status": mutation.status,
                }
            )
            if mutation.status == "failed_user":
                step.status = "blocked_user"
                step.last_error = mutation.last_error
                await _transition_objective(
                    db,
                    objective,
                    "needs_user",
                    reason=mutation.last_error or "Google Calendar authorization is required",
                )
            elif mutation.status == "needs_user_conflict":
                step.status = "blocked_user"
                step.last_error = mutation.last_error
                await _transition_objective(
                    db,
                    objective,
                    "needs_user",
                    reason="The requested meeting overlaps an existing busy period and no safe alternative window was supplied",
                )
            elif mutation.status == "failed":
                step.status = "failed"
                step.finished_at = utcnow()
                step.last_error = mutation.last_error
                await _transition_objective(
                    db,
                    objective,
                    "blocked_system",
                    reason=mutation.last_error or "Calendar mutation failed without a verified provider outcome",
                )
            else:
                step.status = "verifying"
                step.run_after = max(mutation.verify_after, utcnow() + timedelta(seconds=2))
                await _transition_objective(db, objective, "verifying")
        elif step.action_type == "browser_operation":
            from app.services.browser_operator import enqueue_browser_operation

            operation_id = int(params.get("browser_operation_id") or 0)
            operation = await db.get(BrowserOperation, operation_id) if operation_id > 0 else None
            if operation is None:
                raise RuntimeError("Persisted browser operation no longer exists")
            operation.objective_id = objective.id
            operation.step_id = step.id
            step.external_ref = f"browser-operation:{operation.id}"
            step.outcome_json = _dump({"browser_operation_id": operation.id, "status": operation.status})
            await db.commit()

            if operation.status == "verified":
                step.status = "verifying"
                step.run_after = utcnow()
                await _transition_objective(db, objective, "verifying")
            elif operation.status == "needs_user_auth":
                step.status = "blocked_user"
                step.last_error = operation.challenge_prompt or operation.last_error
                await _transition_objective(
                    db,
                    objective,
                    "needs_user",
                    reason=operation.challenge_prompt or "Portal authentication is required",
                )
            elif operation.status == "creation_uncertain":
                step.status = "failed"
                step.finished_at = utcnow()
                step.last_error = operation.last_error
                await _transition_objective(
                    db,
                    objective,
                    "blocked_system",
                    reason=operation.last_error or "Browser side-effect outcome is ambiguous; VAAPP will not risk a duplicate submission",
                )
            elif operation.status == "blocked_capability":
                step.status = "blocked_capability"
                step.last_error = operation.last_error
                await _transition_objective(
                    db,
                    objective,
                    "blocked_capability",
                    reason=operation.last_error or "Secure browser executor is unavailable",
                )
            elif operation.status == "failed":
                step.status = "failed"
                step.finished_at = utcnow()
                step.last_error = operation.last_error
                await _transition_objective(
                    db,
                    objective,
                    "blocked_system",
                    reason=operation.last_error or "Browser operation failed without a verified provider outcome",
                )
            else:
                if operation.status in {"pending", "retry"}:
                    await enqueue_browser_operation(db, operation)
                step.status = "verifying"
                step.run_after = utcnow() + timedelta(seconds=10)
                await _transition_objective(db, objective, "verifying")

        elif step.action_type == "record_only":
            step.status = "completed"
            step.outcome_json = _dump({"recorded": True})
            step.finished_at = utcnow()
            await _add_evidence(
                db,
                objective,
                step=step,
                evidence_type="internal_state",
                provider="vaapp",
                external_ref=step.idempotency_key,
                details={"recorded": True},
            )
        elif step.action_type == "wait":
            step.status = "waiting"
            await _transition_objective(db, objective, "waiting_external")
        elif step.action_type == "complete":
            step.status = "completed"
            step.outcome_json = _dump({"completed": True})
            step.finished_at = utcnow()
            await _add_evidence(
                db,
                objective,
                step=step,
                evidence_type="internal_completion",
                provider="vaapp",
                external_ref=step.idempotency_key,
                details={"completed": True},
            )
            await _finish_if_all_steps_complete(db, objective)
        else:
            raise RuntimeError(f"No Phase-1 executor exists for action type {step.action_type}")
    except Exception as exc:
        step.last_error = str(exc)[:8000]
        if step.attempts < step.max_attempts:
            step.status = "retry"
            step.run_after = utcnow() + timedelta(seconds=min(3600, 15 * (2 ** max(0, step.attempts - 1))))
            await _transition_objective(db, objective, "waiting", reason="Automatic retry scheduled", error=str(exc))
        else:
            step.status = "failed"
            step.finished_at = utcnow()
            await increment_metric(db, "provider_failures")
            await _transition_objective(db, objective, "blocked_system", reason="Step exhausted automatic retries", error=str(exc))
        await write_audit(
            db,
            "va_objective_step_failed",
            entity_type="va_objective",
            entity_id=str(objective.id),
            result="failed",
            details={"step_id": step.id, "action_type": step.action_type, "attempts": step.attempts, "error": str(exc)},
        )
    await db.commit()


async def execute_ready_steps(db: AsyncSession, *, limit: int = 25) -> int:
    now = utcnow()
    steps = list(
        (
            await db.execute(
                select(VAObjectiveStep)
                .where(
                    VAObjectiveStep.status.in_(["pending", "retry"]),
                    VAObjectiveStep.run_after <= now,
                )
                .order_by(VAObjectiveStep.run_after.asc(), VAObjectiveStep.objective_id.asc(), VAObjectiveStep.position.asc())
                .limit(max(1, min(limit, 100)))
            )
        ).scalars()
    )
    executed = 0
    for step in steps:
        objective = await db.get(VAObjective, step.objective_id)
        if objective is None or objective.status in TERMINAL_OBJECTIVE_STATES or objective.status == "needs_user":
            continue
        earlier_incomplete = int(
            (
                await db.execute(
                    select(func.count(VAObjectiveStep.id)).where(
                        VAObjectiveStep.objective_id == step.objective_id,
                        VAObjectiveStep.position < step.position,
                        VAObjectiveStep.status != "completed",
                    )
                )
            ).scalar_one()
        )
        if earlier_incomplete:
            continue
        await _execute_step(db, objective, step)
        executed += 1
    return executed


async def _finish_if_all_steps_complete(db: AsyncSession, objective: VAObjective) -> None:
    statuses = list(
        (
            await db.execute(
                select(VAObjectiveStep.status)
                .where(VAObjectiveStep.objective_id == objective.id)
                .order_by(VAObjectiveStep.position.asc())
            )
        ).scalars()
    )
    if statuses and all(status == "completed" for status in statuses):
        await _transition_objective(db, objective, "completed", reason="All persisted steps have verified outcomes")


async def verify_ready_steps(db: AsyncSession, *, limit: int = 50) -> int:
    now = utcnow()
    steps = list(
        (
            await db.execute(
                select(VAObjectiveStep)
                .where(VAObjectiveStep.status == "verifying", VAObjectiveStep.run_after <= now)
                .order_by(VAObjectiveStep.run_after.asc(), VAObjectiveStep.id.asc())
                .limit(max(1, min(limit, 200)))
            )
        ).scalars()
    )
    checked = 0
    for step in steps:
        objective = await db.get(VAObjective, step.objective_id)
        if objective is None:
            continue
        checked += 1
        params = _loads(step.parameters_json, {})
        params = params if isinstance(params, dict) else {}

        if step.verification_type == "gmail_outbound_verified":
            from app.integrations.google_api import modify_gmail_message
            from app.services.autonomy_policy import learn_successful_reply
            from app.services.communication_ownership import mark_thread_waiting_for_counterparty
            from app.services.gmail_delivery import ensure_gmail_outbound_verified

            outbound = (
                await db.execute(
                    select(GmailOutboundMessage).where(GmailOutboundMessage.step_id == step.id).limit(1)
                )
            ).scalar_one_or_none()
            if outbound is None:
                step.status = "failed"
                step.finished_at = now
                await _transition_objective(db, objective, "blocked_system", reason="Persisted Gmail outbound intent is missing")
                continue
            if outbound.status == "failed_user":
                step.status = "blocked_user"
                step.last_error = outbound.last_error
                await _transition_objective(db, objective, "needs_user", reason=outbound.last_error or "Gmail authorization is required")
                continue
            if outbound.status in {"failed", "failed_uncertain"}:
                step.status = "failed"
                step.finished_at = now
                step.last_error = outbound.last_error
                reason = (
                    "Gmail provider outcome is ambiguous; automatic duplicate send is disabled"
                    if outbound.status == "failed_uncertain"
                    else (outbound.last_error or "Gmail send failed without a verified outcome")
                )
                await _transition_objective(db, objective, "blocked_system", reason=reason, error=outbound.last_error)
                continue
            verified = await ensure_gmail_outbound_verified(db, outbound)
            if not verified:
                step.run_after = max(outbound.verify_after, now + timedelta(seconds=15))
                await _transition_objective(db, objective, "verifying")
                continue

            step.status = "completed"
            step.finished_at = now
            step.last_error = ""
            step.external_ref = f"gmail:{outbound.external_message_id}"
            step.outcome_json = _dump({"gmail_outbound_id": outbound.id, "gmail_message_id": outbound.external_message_id, "status": "verified"})
            await _add_evidence(
                db,
                objective,
                step=step,
                evidence_type="gmail_sent_message",
                provider="gmail",
                external_ref=outbound.external_message_id or outbound.rfc_message_id,
                details={
                    "gmail_thread_id": outbound.external_thread_id or outbound.gmail_thread_id,
                    "rfc_message_id": outbound.rfc_message_id,
                    "recipient": outbound.recipient,
                },
            )
            source_message_id = str(params.get("source_message_id") or "")
            if source_message_id:
                try:
                    await modify_gmail_message(db, source_message_id, remove_labels=["INBOX", "UNREAD"])
                except Exception as exc:
                    # Sending is already verified; archival is a secondary housekeeping
                    # action and must never create a duplicate reply.
                    await write_audit(
                        db,
                        "gmail_reply_archive_deferred",
                        entity_type="email",
                        entity_id=source_message_id,
                        result="deferred",
                        details={"error": str(exc)},
                    )
                message = (
                    await db.execute(select(Task).where(Task.source_type == "email_reply", Task.source_id == source_message_id, Task.status.in_(["open", "waiting"])).limit(1))
                ).scalar_one_or_none()
                if message is not None:
                    message.status = "completed"
                    message.requires_approval = False
                from app.models.entities import EmailMessage
                email_row = (
                    await db.execute(select(EmailMessage).where(EmailMessage.provider_message_id == source_message_id).limit(1))
                ).scalar_one_or_none()
                if email_row is not None:
                    email_row.status = "replied"
                    if step.action_type == "gmail_send_reply":
                        await learn_successful_reply(db, message=email_row, mode="autonomous_core")
                await write_audit(
                    db,
                    "email_reply_sent",
                    entity_type="email",
                    entity_id=source_message_id,
                    details={"gmail_message_id": outbound.external_message_id, "objective_id": objective.id, "autopilot": True},
                )

            if bool(params.get("expect_reply")):
                thread_record_id = int(params.get("thread_record_id") or 0)
                followup_id = int(params.get("follow_up_id") or 0)
                followup = await db.get(VAFollowUp, followup_id) if followup_id > 0 else None
                current_attempt = int(params.get("follow_up_attempt") or (followup.attempts if followup else 0) or 0)
                max_attempts = int(params.get("max_follow_up_attempts") or (followup.max_attempts if followup else 4) or 4)
                schedule_next = followup_id == 0 or current_attempt < max_attempts
                saved_payload = {
                    "thread_record_id": thread_record_id,
                    "gmail_thread_id": outbound.external_thread_id or outbound.gmail_thread_id,
                    "source_message_id": source_message_id,
                    "source_rfc_message_id": outbound.rfc_message_id,
                    "subject": outbound.subject,
                    "previous_body": outbound.body,
                }
                await mark_thread_waiting_for_counterparty(
                    db,
                    thread_record_id=thread_record_id,
                    objective_id=objective.id,
                    channel="email",
                    target=outbound.recipient,
                    purpose=f"Follow up on {outbound.subject or 'the previous email'}",
                    payload=saved_payload,
                    follow_up_hours=int(params.get("follow_up_hours") or 48),
                    max_attempts=max_attempts,
                    schedule_follow_up=schedule_next,
                )
                if followup is not None:
                    followup.last_external_ref = outbound.external_message_id
                    followup.last_sent_at = now
                    if not schedule_next:
                        followup.status = "exhausted"
                    elif thread_record_id <= 0:
                        # Calendar-owned reminder emails do not belong to a
                        # VACommunicationThread. Keep their persisted follow-up row
                        # recurring until the bounded attempt limit is reached.
                        followup.status = "pending"
                        followup.due_at = now + timedelta(hours=max(1, int(params.get("follow_up_hours") or followup.recurrence_hours or 48)))
                await _transition_objective(
                    db,
                    objective,
                    "waiting_external",
                    reason="Verified reply sent; waiting for the counterparty response",
                )
            else:
                await _finish_if_all_steps_complete(db, objective)
            continue

        if step.verification_type == "browser_operation_verified":
            operation_id = int(params.get("browser_operation_id") or 0)
            operation = await db.get(BrowserOperation, operation_id) if operation_id > 0 else None
            if operation is None:
                step.status = "failed"
                step.finished_at = now
                await _transition_objective(
                    db,
                    objective,
                    "blocked_system",
                    reason="Persisted browser operation intent is missing",
                )
                continue
            step.outcome_json = _dump({"browser_operation_id": operation.id, "status": operation.status})
            if operation.status == "needs_user_auth":
                step.status = "blocked_user"
                step.last_error = operation.challenge_prompt or operation.last_error
                await _transition_objective(
                    db,
                    objective,
                    "needs_user",
                    reason=operation.challenge_prompt or "Portal authentication is required",
                )
                continue
            if operation.status == "creation_uncertain":
                step.status = "failed"
                step.finished_at = now
                step.last_error = operation.last_error
                await _transition_objective(
                    db,
                    objective,
                    "blocked_system",
                    reason=operation.last_error or "Browser side-effect outcome is ambiguous; VAAPP will not blindly replay it",
                )
                continue
            if operation.status == "blocked_capability":
                step.status = "blocked_capability"
                step.last_error = operation.last_error
                await _transition_objective(
                    db,
                    objective,
                    "blocked_capability",
                    reason=operation.last_error or "Secure browser executor is unavailable",
                )
                continue
            if operation.status == "failed":
                step.status = "failed"
                step.finished_at = now
                step.last_error = operation.last_error
                await _transition_objective(
                    db,
                    objective,
                    "blocked_system",
                    reason=operation.last_error or "Browser provider postcondition could not be verified",
                )
                continue
            if operation.status != "verified":
                step.run_after = max(operation.verify_after, now + timedelta(seconds=10))
                await _transition_objective(db, objective, "verifying")
                continue

            step.status = "completed"
            step.finished_at = now
            step.last_error = ""
            step.external_ref = f"browser-operation:{operation.id}"
            await _add_evidence(
                db,
                objective,
                step=step,
                evidence_type="browser_postcondition_verified",
                provider="playwright_chromium",
                external_ref=str(operation.id),
                details={
                    "portal_id": operation.portal_id,
                    "url": operation.last_url,
                    "page_title": operation.page_title,
                    "browser_operation_id": operation.id,
                },
            )
            await _finish_if_all_steps_complete(db, objective)
            continue

        if step.verification_type == "calendar_mutation_verified":
            from app.services.calendar_ownership import ensure_calendar_mutation_verified

            mutation = (
                await db.execute(
                    select(CalendarMutation).where(CalendarMutation.step_id == step.id).limit(1)
                )
            ).scalar_one_or_none()
            if mutation is None:
                step.status = "failed"
                step.finished_at = now
                await _transition_objective(
                    db,
                    objective,
                    "blocked_system",
                    reason="Persisted Calendar mutation intent is missing",
                )
                continue
            if mutation.status == "failed_user":
                step.status = "blocked_user"
                step.last_error = mutation.last_error
                await _transition_objective(
                    db,
                    objective,
                    "needs_user",
                    reason=mutation.last_error or "Google Calendar authorization is required",
                )
                continue
            if mutation.status == "needs_user_conflict":
                step.status = "blocked_user"
                step.last_error = mutation.last_error
                await _transition_objective(
                    db,
                    objective,
                    "needs_user",
                    reason="The requested meeting conflicts with existing calendar availability",
                )
                continue
            if mutation.status == "failed":
                step.status = "failed"
                step.finished_at = now
                step.last_error = mutation.last_error
                await _transition_objective(
                    db,
                    objective,
                    "blocked_system",
                    reason=mutation.last_error or "Calendar provider mutation failed",
                )
                continue
            verified = await ensure_calendar_mutation_verified(db, mutation)
            if not verified:
                step.run_after = max(mutation.verify_after, now + timedelta(seconds=15))
                await _transition_objective(db, objective, "verifying")
                continue

            step.status = "completed"
            step.finished_at = now
            step.last_error = ""
            step.external_ref = f"google-calendar:{mutation.provider_event_id}"
            step.outcome_json = _dump(
                {
                    "calendar_mutation_id": mutation.id,
                    "provider_event_id": mutation.provider_event_id,
                    "operation": mutation.operation,
                    "status": "verified",
                }
            )
            await _add_evidence(
                db,
                objective,
                step=step,
                evidence_type="calendar_event_verified",
                provider="google_calendar",
                external_ref=mutation.provider_event_id,
                details={"operation": mutation.operation, "etag": mutation.etag},
            )
            if bool(params.get("expect_response")) and mutation.operation in {"create", "update"}:
                attendees = params.get("attendees") if isinstance(params.get("attendees"), list) else []
                target = ""
                if attendees:
                    first = attendees[0]
                    target = str(first.get("email") if isinstance(first, dict) else first).strip()
                if target:
                    existing_followup = (
                        await db.execute(
                            select(VAFollowUp).where(
                                VAFollowUp.objective_id == objective.id,
                                VAFollowUp.channel == "email",
                                VAFollowUp.status.in_(["pending", "due", "dispatching"]),
                            ).limit(1)
                        )
                    ).scalar_one_or_none()
                    if existing_followup is None:
                        follow_hours = max(1, min(int(params.get("follow_up_hours") or 24), 168))
                        summary = str(params.get("summary") or "the calendar invitation")
                        start_value = str(params.get("start") or "")
                        db.add(
                            VAFollowUp(
                                objective_id=objective.id,
                                channel="email",
                                target=target,
                                purpose=f"Confirm attendance for {summary}",
                                payload_json=_dump(
                                    {
                                        "subject": f"Re: {summary}",
                                        "follow_up_body": f"Just checking whether you can make {summary} at {start_value}. Please let me know.",
                                        "calendar_event_id": mutation.provider_event_id,
                                    }
                                ),
                                due_at=now + timedelta(hours=follow_hours),
                                recurrence_hours=follow_hours,
                                max_attempts=max(1, min(int(params.get("max_follow_up_attempts") or 2), 4)),
                                status="pending",
                            )
                        )
                    await _transition_objective(
                        db,
                        objective,
                        "waiting_external",
                        reason="Calendar event is verified; waiting for attendee responses",
                    )
                    continue
            await _finish_if_all_steps_complete(db, objective)
            continue

        if step.verification_type == "device_action_verified":
            from app.services.communication_ownership import mark_thread_waiting_for_counterparty

            action_id = int(params.get("communication_action_id") or 0)
            action = await db.get(CommunicationAction, action_id) if action_id > 0 else None
            if action is None:
                step.status = "failed"
                step.finished_at = now
                await _transition_objective(db, objective, "blocked_system", reason="Persisted device communication action is missing")
                continue
            evidence = list(
                (
                    await db.execute(
                        select(CommunicationDeliveryEvidence).where(
                            CommunicationDeliveryEvidence.communication_action_id == action.id
                        )
                    )
                ).scalars()
            )
            types = {row.evidence_type for row in evidence}
            channel = str(params.get("channel") or "").lower()
            verified_type = "sms_delivered" if "sms_delivered" in types else (
                "sms_sent" if channel == "sms" and "sms_sent" in types else (
                    "remote_input_dispatched" if channel != "sms" and "remote_input_dispatched" in types else ""
                )
            )
            if action.status == "failed":
                if step.attempts < step.max_attempts:
                    action.status = "pending"
                    action.failure_reason = ""
                    step.status = "retry"
                    step.run_after = now + timedelta(seconds=min(300, 30 * max(1, step.attempts)))
                    await _transition_objective(db, objective, "waiting", reason="Definitive device dispatch failure; safe retry queued")
                else:
                    step.status = "failed"
                    step.finished_at = now
                    step.last_error = action.failure_reason or "Device communication failed"
                    await _transition_objective(db, objective, "blocked_system", reason=step.last_error)
                continue
            if not verified_type:
                if action.status == "pending" and step.created_at <= now - timedelta(minutes=30):
                    step.status = "failed"
                    step.finished_at = now
                    step.last_error = "Android device did not report a definitive dispatch outcome; automatic resend is unsafe"
                    await _transition_objective(
                        db,
                        objective,
                        "blocked_system",
                        reason="Device dispatch outcome is unknown; VAAPP will not risk sending a duplicate message",
                    )
                    continue
                step.run_after = now + timedelta(seconds=15)
                await _transition_objective(db, objective, "verifying")
                continue

            strongest = next(row for row in evidence if row.evidence_type == verified_type)
            step.status = "completed"
            step.finished_at = now
            step.last_error = ""
            step.external_ref = strongest.external_ref or f"communication-action:{action.id}"
            step.outcome_json = _dump({"communication_action_id": action.id, "evidence_type": verified_type})
            await _add_evidence(
                db,
                objective,
                step=step,
                evidence_type=verified_type,
                provider="android_device",
                external_ref=strongest.external_ref or str(action.id),
                details=_loads(strongest.details_json, {}),
            )
            if bool(params.get("expect_reply")):
                followup_id = int(params.get("follow_up_id") or 0)
                followup = await db.get(VAFollowUp, followup_id) if followup_id > 0 else None
                current_attempt = int(params.get("follow_up_attempt") or (followup.attempts if followup else 0) or 0)
                max_attempts = int(params.get("max_follow_up_attempts") or (followup.max_attempts if followup else 4) or 4)
                schedule_next = followup_id == 0 or (channel == "sms" and current_attempt < max_attempts)
                action_payload = _loads(action.payload_json, {})
                await mark_thread_waiting_for_counterparty(
                    db,
                    thread_record_id=int(params.get("thread_record_id") or 0),
                    objective_id=objective.id,
                    channel=channel,
                    target=action.target,
                    purpose=f"Follow up with {action.target}",
                    payload={
                        "thread_record_id": int(params.get("thread_record_id") or 0),
                        "communication_event_id": action.event_id,
                        "previous_body": str(action_payload.get("text") or ""),
                    },
                    follow_up_hours=int(params.get("follow_up_hours") or 48),
                    max_attempts=max_attempts,
                    schedule_follow_up=schedule_next,
                )
                if followup is not None:
                    followup.last_external_ref = strongest.external_ref or str(action.id)
                    followup.last_sent_at = now
                    if not schedule_next:
                        followup.status = "exhausted"
                await _transition_objective(db, objective, "waiting_external", reason="Verified device reply dispatched; waiting for the counterparty response")
            else:
                await _finish_if_all_steps_complete(db, objective)
            continue

        if step.verification_type != "workflow_run_completed" or step.workflow_run_id is None:
            step.status = "failed"
            step.finished_at = now
            await _transition_objective(
                db,
                objective,
                "blocked_system",
                reason="No implemented verifier exists for this persisted step",
            )
            continue

        run = await db.get(WorkflowRun, step.workflow_run_id)
        if run is None:
            step.status = "failed"
            step.finished_at = now
            await _transition_objective(db, objective, "blocked_system", reason="Referenced workflow run no longer exists")
            continue

        if run.status == "completed":
            step.status = "completed"
            step.finished_at = now
            step.last_error = ""
            await _add_evidence(
                db,
                objective,
                step=step,
                evidence_type="workflow_run_terminal",
                provider="vaapp_workflow_engine",
                external_ref=str(run.id),
                details={"workflow_status": run.status, "workflow_type": run.workflow_type},
            )
            await _finish_if_all_steps_complete(db, objective)
            continue

        if run.status == "superseded":
            run_jobs = list(
                (
                    await db.execute(
                        select(WorkflowJob)
                        .where(WorkflowJob.workflow_run_id == run.id)
                        .order_by(WorkflowJob.id.asc())
                    )
                ).scalars()
            )
            unresolved_superseded: list[int] = []
            verified_by: list[int] = []
            for run_job in run_jobs:
                if run_job.status == "completed":
                    continue
                if run_job.status != "superseded":
                    unresolved_superseded.append(run_job.id)
                    continue
                outcome = _loads(run_job.result_json, {})
                replacement_id = int(outcome.get("superseded_by_job_id") or 0) if isinstance(outcome, dict) else 0
                replacement = await db.get(WorkflowJob, replacement_id) if replacement_id > 0 else None
                if replacement is not None and replacement.status == "completed":
                    verified_by.append(replacement.id)
                else:
                    unresolved_superseded.append(run_job.id)
            if not unresolved_superseded:
                step.status = "completed"
                step.finished_at = now
                step.last_error = ""
                await _add_evidence(
                    db,
                    objective,
                    step=step,
                    evidence_type="workflow_superseded_by_completed_work",
                    provider="vaapp_workflow_engine",
                    external_ref=str(run.id),
                    details={"workflow_status": run.status, "verified_by_job_ids": verified_by},
                )
                await _finish_if_all_steps_complete(db, objective)
            else:
                step.run_after = now + timedelta(seconds=30)
                await _transition_objective(
                    db,
                    objective,
                    "waiting_external",
                    reason="Superseded workflow is waiting for independently verified replacement work",
                )
            continue

        if run.status == "failed":
            jobs = list(
                (
                    await db.execute(
                        select(WorkflowJob)
                        .where(WorkflowJob.workflow_run_id == run.id, WorkflowJob.status == "dead_letter")
                        .order_by(WorkflowJob.id.asc())
                    )
                ).scalars()
            )
            classes = [failure_recovery_class(job.job_type, job.last_error) for job in jobs]
            if "user_required" in classes:
                error = next((job.last_error for job, cls in zip(jobs, classes) if cls == "user_required"), "Provider authorization required")
                step.status = "blocked_user"
                step.last_error = error
                await _transition_objective(db, objective, "needs_user", reason=error)
            elif "transient" in classes:
                # Existing workflow watchdog owns safe recovery. Do not create a
                # duplicate external intent from the objective layer.
                step.run_after = now + timedelta(minutes=2)
                await _transition_objective(db, objective, "waiting_external", reason="Transient workflow failure is being recovered automatically")
            else:
                error = jobs[0].last_error if jobs else "Workflow failed without a recoverable terminal job"
                step.status = "failed"
                step.last_error = error
                step.finished_at = now
                await increment_metric(db, "provider_failures")
                await _transition_objective(db, objective, "blocked_system", reason="Workflow failed and blind retry is unsafe", error=error)
            continue

        step.run_after = now + timedelta(seconds=15)
        await _transition_objective(db, objective, "verifying")

    if checked:
        await db.commit()
    return checked


async def reconcile_source_objectives(db: AsyncSession, *, limit: int = 250) -> dict[str, int]:
    objectives = list(
        (
            await db.execute(
                select(VAObjective)
                .where(VAObjective.status.not_in(["completed", "cancelled", "failed"]))
                .order_by(VAObjective.id.asc())
                .limit(max(1, min(limit, 1000)))
            )
        ).scalars()
    )
    result = {"completed": 0, "waiting": 0, "recovered": 0}
    for objective in objectives:
        if objective.source_type == "payment":
            row = await db.get(Payment, int(objective.source_id)) if objective.source_id.isdigit() else None
            if row is None:
                continue
            if row.status == "completed":
                await _add_evidence(
                    db,
                    objective,
                    step=None,
                    evidence_type="payment_status",
                    provider="bank",
                    external_ref=row.external_payment_id or str(row.id),
                    details={"status": row.status, "amount": str(row.amount), "currency": row.currency},
                )
                await _transition_objective(db, objective, "completed", reason="Bank payment is confirmed completed")
                result["completed"] += 1
            elif not row.requires_user_action and objective.status == "needs_user":
                await _transition_objective(db, objective, "waiting_external", reason="Bank authorization completed; waiting for settlement")
                result["waiting"] += 1
        elif objective.source_type == "own_account_transfer":
            row = await db.get(OwnAccountTransfer, int(objective.source_id)) if objective.source_id.isdigit() else None
            if row is None:
                continue
            if row.status == "completed":
                await _add_evidence(
                    db,
                    objective,
                    step=None,
                    evidence_type="own_transfer_status",
                    provider="bank",
                    external_ref=row.external_payment_id or str(row.id),
                    details={"status": row.status, "amount": str(row.amount), "currency": row.currency},
                )
                await _transition_objective(db, objective, "completed", reason="Own-account transfer is confirmed completed")
                result["completed"] += 1
            elif not row.requires_user_action and objective.status == "needs_user":
                await _transition_objective(db, objective, "waiting_external", reason="Bank authorization completed; waiting for settlement")
                result["waiting"] += 1
        elif objective.source_type == "workflow_job":
            row = await db.get(WorkflowJob, int(objective.source_id)) if objective.source_id.isdigit() else None
            if row is None:
                continue
            if row.status == "completed":
                await _add_evidence(
                    db,
                    objective,
                    step=None,
                    evidence_type="workflow_job_status",
                    provider="vaapp_workflow_engine",
                    external_ref=str(row.id),
                    details={"status": row.status, "job_type": row.job_type},
                )
                await _transition_objective(db, objective, "completed", reason="Previously blocked workflow job completed")
                result["completed"] += 1
            elif row.status in {"retry", "running", "pending"} and objective.status in {"needs_user", "blocked_system"}:
                await _transition_objective(db, objective, "waiting_external", reason="Workflow recovery is active")
                result["recovered"] += 1
        elif objective.source_type == "task":
            row = await db.get(Task, int(objective.source_id)) if objective.source_id.isdigit() else None
            if row is None:
                continue
            if row.status == "completed":
                await _add_evidence(
                    db,
                    objective,
                    step=None,
                    evidence_type="task_status",
                    provider="vaapp",
                    external_ref=str(row.id),
                    details={"status": row.status, "source_type": row.source_type},
                )
                await _transition_objective(db, objective, "completed", reason="Source task is completed")
                result["completed"] += 1
            elif row.status == "cancelled":
                await _transition_objective(db, objective, "cancelled", reason="Source task was cancelled")
    if any(result.values()):
        await db.commit()
    return result


async def _job_capability_is_available(db: AsyncSession, job: WorkflowJob) -> bool:
    key = _JOB_CAPABILITY.get(job.job_type)
    if key is None:
        return False
    capability = await capability_for_key(db, key)
    return bool(capability and capability.get("available"))


async def recover_resolved_user_blockers(db: AsyncSession, *, limit: int = 100) -> int:
    """Resume only dead letters whose prior blocker was user authentication/config.

    A job is requeued only after the corresponding real capability is live again.
    Unknown failures and ambiguous external outcomes are never replayed here.
    """

    objectives = list(
        (
            await db.execute(
                select(VAObjective)
                .where(VAObjective.status == "needs_user")
                .order_by(VAObjective.updated_at.asc(), VAObjective.id.asc())
                .limit(max(1, min(limit, 500)))
            )
        ).scalars()
    )
    recovered = 0
    for objective in objectives:
        # A direct workflow-job blocker created from the global dead-letter queue.
        if objective.source_type == "workflow_job" and objective.source_id.isdigit():
            job = await db.get(WorkflowJob, int(objective.source_id))
            if (
                job is not None
                and job.status == "dead_letter"
                and failure_recovery_class(job.job_type, job.last_error) == "user_required"
                and await _job_capability_is_available(db, job)
            ):
                await requeue_dead_letter(db, job.id)
                await increment_metric(db, "automatic_recoveries")
                await _transition_objective(
                    db,
                    objective,
                    "waiting_external",
                    reason="Provider connection is healthy again; the original durable job was safely requeued",
                )
                await db.commit()
                recovered += 1
            continue

        steps = list(
            (
                await db.execute(
                    select(VAObjectiveStep).where(
                        VAObjectiveStep.objective_id == objective.id,
                        VAObjectiveStep.status == "blocked_user",
                        VAObjectiveStep.workflow_run_id.is_not(None),
                    )
                )
            ).scalars()
        )
        for step in steps:
            assert step.workflow_run_id is not None
            dead = list(
                (
                    await db.execute(
                        select(WorkflowJob).where(
                            WorkflowJob.workflow_run_id == step.workflow_run_id,
                            WorkflowJob.status == "dead_letter",
                        )
                    )
                ).scalars()
            )
            user_dead = [job for job in dead if failure_recovery_class(job.job_type, job.last_error) == "user_required"]
            if not user_dead:
                continue
            if not all([await _job_capability_is_available(db, job) for job in user_dead]):
                continue
            for job in user_dead:
                await requeue_dead_letter(db, job.id)
                await increment_metric(db, "automatic_recoveries")
            step.status = "verifying"
            step.last_error = ""
            step.run_after = utcnow() + timedelta(seconds=10)
            await _transition_objective(
                db,
                objective,
                "waiting_external",
                reason="Provider connection is healthy again; blocked durable work resumed automatically",
            )
            await db.commit()
            recovered += 1
    return recovered


async def process_due_followups(db: AsyncSession, *, limit: int = 100) -> int:
    now = utcnow()
    rows = list(
        (
            await db.execute(
                select(VAFollowUp)
                .where(VAFollowUp.status == "pending", VAFollowUp.due_at <= now)
                .order_by(VAFollowUp.due_at.asc(), VAFollowUp.id.asc())
                .limit(max(1, min(limit, 500)))
            )
        ).scalars()
    )
    for row in rows:
        _, created = await record_event(
            db,
            event_key=f"followup:{row.id}:attempt:{row.attempts + 1}",
            source_type="follow_up",
            source_id=str(row.id),
            event_type="followup_due",
            title=f"Follow up: {row.purpose or row.target or row.id}",
            payload={
                "objective_id": row.objective_id,
                "channel": row.channel,
                "target": row.target,
                "purpose": row.purpose,
                "payload": _loads(row.payload_json, {}),
            },
            occurred_at=row.due_at,
        )
        if created:
            await increment_metric(db, "followups_due")
            row.attempts += 1
            # Phase 1 never pretends a channel action happened. The record remains
            # due/blocked until a later real executor owns it.
            row.status = "due"
    if rows:
        await db.commit()
    return len(rows)


async def run_core_cycle(db: AsyncSession, *, create_manual_run: bool = False) -> dict[str, Any]:
    if create_manual_run:
        await create_manual_run_event(db)
    seeded = await seed_system_events(db)
    processed = await process_pending_events(db)
    reconciled_before = await reconcile_source_objectives(db)
    recovered_user_blockers = await recover_resolved_user_blockers(db)
    followups = await process_due_followups(db)
    # Follow-up events created above are intentionally converted to objectives in
    # the same cycle so they are visible immediately.
    processed += await process_pending_events(db)
    executed = await execute_ready_steps(db)
    verified = await verify_ready_steps(db)
    reconciled_after = await reconcile_source_objectives(db)
    return {
        "seeded": seeded,
        "events_processed": processed,
        "steps_executed": executed,
        "steps_verified": verified,
        "followups_due": followups,
        "resolved_user_blockers_recovered": recovered_user_blockers,
        "source_reconciliation": {
            key: reconciled_before.get(key, 0) + reconciled_after.get(key, 0)
            for key in set(reconciled_before) | set(reconciled_after)
        },
    }


async def objective_public(db: AsyncSession, row: VAObjective, *, include_timeline: bool = False) -> dict[str, Any]:
    steps = list(
        (
            await db.execute(
                select(VAObjectiveStep)
                .where(VAObjectiveStep.objective_id == row.id)
                .order_by(VAObjectiveStep.position.asc())
            )
        ).scalars()
    )
    evidence_count = int(
        (
            await db.execute(
                select(func.count(VAOutcomeEvidence.id)).where(VAOutcomeEvidence.objective_id == row.id)
            )
        ).scalar_one()
    )
    payload = {
        "id": row.id,
        "correlation_key": row.correlation_key,
        "source_type": row.source_type,
        "source_id": row.source_id,
        "title": row.title,
        "goal": row.goal,
        "category": row.category,
        "priority": row.priority,
        "risk_level": row.risk_level,
        "status": row.status,
        "due_at": row.due_at,
        "needs_user_reason": row.needs_user_reason,
        "user_intervention_count": row.user_intervention_count,
        "blocked_reason": row.blocked_reason,
        "last_error": row.last_error,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "finished_at": row.finished_at,
        "context": _loads(row.context_json, {}),
        "plan": _loads(row.plan_json, {}),
        "evidence_count": evidence_count,
        "steps": [
            {
                "id": step.id,
                "position": step.position,
                "action_type": step.action_type,
                "idempotency_key": step.idempotency_key,
                "status": step.status,
                "verification_type": step.verification_type,
                "workflow_run_id": step.workflow_run_id,
                "external_ref": step.external_ref,
                "attempts": step.attempts,
                "max_attempts": step.max_attempts,
                "run_after": step.run_after,
                "last_error": step.last_error,
                "policy": _loads(step.policy_json, {}),
                "capability": _loads(step.capability_json, {}),
                "outcome": _loads(step.outcome_json, {}),
            }
            for step in steps
        ],
    }
    if include_timeline:
        audits = list(
            (
                await db.execute(
                    select(AuditLog)
                    .where(AuditLog.entity_type == "va_objective", AuditLog.entity_id == str(row.id))
                    .order_by(AuditLog.created_at.asc(), AuditLog.id.asc())
                    .limit(500)
                )
            ).scalars()
        )
        payload["timeline"] = [
            {
                "event_type": audit.event_type,
                "result": audit.result,
                "details": _loads(audit.details_json, {}),
                "created_at": audit.created_at,
            }
            for audit in audits
        ]
    return payload


async def list_objectives(
    db: AsyncSession,
    *,
    status: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    query = select(VAObjective).order_by(VAObjective.updated_at.desc(), VAObjective.id.desc()).limit(max(1, min(limit, 500)))
    if status:
        query = query.where(VAObjective.status == status)
    rows = list((await db.execute(query)).scalars())
    return [await objective_public(db, row) for row in rows]


async def get_objective(db: AsyncSession, objective_id: int) -> dict[str, Any] | None:
    row = await db.get(VAObjective, objective_id)
    return None if row is None else await objective_public(db, row, include_timeline=True)


async def va_overview(db: AsyncSession) -> dict[str, Any]:
    metrics = await autonomy_summary(db)
    capabilities = await capability_matrix(db)
    event_backlog = int(
        (
            await db.execute(select(func.count(VAEvent.id)).where(VAEvent.status == "new"))
        ).scalar_one()
    )
    due_followups = int(
        (
            await db.execute(
                select(func.count(VAFollowUp.id)).where(
                    VAFollowUp.status.in_(["pending", "due"]),
                    VAFollowUp.due_at <= utcnow(),
                )
            )
        ).scalar_one()
    )
    recent_rows = list(
        (
            await db.execute(
                select(VAObjective)
                .order_by(VAObjective.updated_at.desc(), VAObjective.id.desc())
                .limit(20)
            )
        ).scalars()
    )
    needs_rows = list(
        (
            await db.execute(
                select(VAObjective)
                .where(VAObjective.status == "needs_user")
                .order_by(VAObjective.priority.asc(), VAObjective.updated_at.desc())
                .limit(50)
            )
        ).scalars()
    )
    return {
        "status": "operational" if event_backlog == 0 else "processing",
        "metrics": metrics,
        "capabilities": capabilities,
        "event_backlog": event_backlog,
        "due_followups": due_followups,
        "needs_user": [await objective_public(db, row) for row in needs_rows],
        "recent_objectives": [await objective_public(db, row) for row in recent_rows],
        "checked_at": utcnow().isoformat() + "Z",
    }
