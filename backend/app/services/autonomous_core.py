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
    OwnAccountTransfer,
    Payment,
    Task,
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
    elif event.event_type == "followup_due":
        objective, _ = await _create_objective(
            db,
            event,
            title=event.title,
            goal=str(payload.get("purpose") or event.title),
            category="follow_up",
            priority="normal",
            status="blocked_capability",
            reason="Follow-up persistence is active; channel execution is implemented in the communications phase.",
        )
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
