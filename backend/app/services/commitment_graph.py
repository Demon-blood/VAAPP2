from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import VACommitmentEdge, VAFollowUp, VAObjective, VAObjectiveStep, VAOutcomeEvidence
from app.services.audit import write_audit

_TERMINAL = {"completed", "cancelled", "failed"}
_ACTIVE_STEP_STATES = {"pending", "retry", "running", "verifying", "waiting", "blocked_user", "blocked_capability"}
_PRIORITY_WEIGHT = {"urgent": 1000, "high": 700, "normal": 400, "low": 100}
_RISK_WEIGHT = {"critical": 140, "high": 90, "medium": 40, "low": 0}
_ACTION_LABELS = {
    "workflow_intent": "Run the configured VA workflow and verify the outcome",
    "gmail_send_reply": "Send the reply and verify that Gmail accepted it",
    "gmail_send_followup": "Follow up and verify that the message was sent",
    "device_communication_action": "Send the message from the phone and verify dispatch",
    "device_followup_action": "Send the follow-up from the phone and verify dispatch",
    "calendar_mutation": "Update the calendar and verify the provider state",
    "browser_operation": "Complete the portal operation and verify the provider result",
    "record_only": "Record the result as durable evidence",
    "complete": "Verify the remaining completion condition",
}


def _now() -> datetime:
    return datetime.utcnow()


def _loads(value: str | None) -> dict[str, Any]:
    try:
        decoded = json.loads(value or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() + "Z" if value is not None else None


def _stage(objective: VAObjective) -> str:
    status = str(objective.status or "detected")
    if status == "completed":
        return "finished"
    if status in {"cancelled", "failed"}:
        return "closed"
    if status == "needs_user":
        return "needs_user"
    if status in {"blocked_capability", "blocked_system"}:
        return "blocked_internal"
    if status in {"waiting_external", "waiting_provider"}:
        return "waiting"
    if status == "verifying":
        return "verifying"
    if status in {"executing", "planned", "detected", "waiting"}:
        return "working"
    return "working"


def _waiting_on(objective: VAObjective, steps: list[VAObjectiveStep]) -> str:
    stage = _stage(objective)
    if stage == "needs_user":
        return "user"
    if stage == "blocked_internal":
        return "system"
    if stage != "waiting":
        return "none"
    waiting = next(
        (
            step
            for step in steps
            if step.status in {"pending", "waiting", "verifying", "retry"}
            and (step.action_type == "wait" or step.verification_type in {"counterparty_response", "calendar_attendee_response"})
        ),
        None,
    )
    if waiting is not None and waiting.verification_type in {"counterparty_response", "calendar_attendee_response"}:
        return "counterparty"
    return "provider"


def _next_check(steps: list[VAObjectiveStep], followups: list[VAFollowUp]) -> datetime | None:
    values: list[datetime] = []
    for step in steps:
        if step.status in _ACTIVE_STEP_STATES and step.run_after is not None and step.action_type != "wait":
            values.append(step.run_after)
    for followup in followups:
        if followup.status in {"pending", "due", "dispatching"} and followup.due_at is not None:
            values.append(followup.due_at)
    return min(values) if values else None


def _next_action(objective: VAObjective, steps: list[VAObjectiveStep], followups: list[VAFollowUp]) -> str:
    stage = _stage(objective)
    if stage == "finished":
        return "Outcome verified and closed"
    if stage == "closed":
        return "No further action is scheduled"
    if stage == "needs_user":
        return str(objective.needs_user_reason or "Complete the unavoidable human step so the VA can resume")
    if stage == "blocked_internal":
        return "Recover the missing capability or system failure internally, then resume automatically"
    pending = next((step for step in steps if step.status in _ACTIVE_STEP_STATES), None)
    if pending is not None:
        if pending.action_type == "wait":
            due = next((item for item in followups if item.status in {"pending", "due", "dispatching"}), None)
            if due is not None:
                return f"Wait for the response and follow up automatically by {due.due_at.isoformat()}Z"
            return "Wait for the external response and continue when new evidence arrives"
        return _ACTION_LABELS.get(pending.action_type, f"Continue {pending.action_type.replace('_', ' ')}")
    if stage == "waiting":
        return "Observe the external state and follow up when due"
    if stage == "verifying":
        return "Verify the provider/source postcondition before closing"
    return "Continue working toward the committed outcome"


def _verification_policy(objective: VAObjective, steps: list[VAObjectiveStep]) -> list[str]:
    values: list[str] = []
    for step in steps:
        value = str(step.verification_type or "").strip()
        if value and value not in values:
            values.append(value)
    if values:
        return values
    if objective.source_type == "payment":
        return ["payment_status"]
    if objective.source_type == "own_account_transfer":
        return ["transfer_status"]
    return ["source_state"]


def _rank_score(objective: VAObjective, stage: str, now: datetime) -> int:
    score = _PRIORITY_WEIGHT.get(str(objective.priority or "normal").lower(), 400)
    score += _RISK_WEIGHT.get(str(objective.risk_level or "low").lower(), 0)
    if objective.due_at is not None:
        remaining = objective.due_at - now
        if remaining.total_seconds() < 0:
            score += 500
        elif remaining <= timedelta(hours=6):
            score += 350
        elif remaining <= timedelta(days=1):
            score += 250
        elif remaining <= timedelta(days=3):
            score += 150
        elif remaining <= timedelta(days=7):
            score += 70
    score += {
        "needs_user": 320,
        "working": 200,
        "verifying": 180,
        "blocked_internal": 150,
        "waiting": 80,
        "finished": -400,
        "closed": -500,
    }.get(stage, 0)
    if objective.updated_at is not None and objective.updated_at < now - timedelta(days=7) and stage not in {"finished", "closed"}:
        score += 80
    return score


async def commitment_projection(db: AsyncSession, objective: VAObjective, *, include_edges: bool = False) -> dict[str, Any]:
    steps = list(
        (
            await db.execute(
                select(VAObjectiveStep)
                .where(VAObjectiveStep.objective_id == objective.id)
                .order_by(VAObjectiveStep.position.asc())
            )
        ).scalars()
    )
    followups = list(
        (
            await db.execute(
                select(VAFollowUp)
                .where(VAFollowUp.objective_id == objective.id)
                .order_by(VAFollowUp.due_at.asc(), VAFollowUp.id.asc())
            )
        ).scalars()
    )
    evidence_count = int(
        (
            await db.execute(
                select(func.count(VAOutcomeEvidence.id)).where(VAOutcomeEvidence.objective_id == objective.id)
            )
        ).scalar_one()
    )
    now = _now()
    stage = _stage(objective)
    next_check = _next_check(steps, followups)
    projection: dict[str, Any] = {
        "objective_id": objective.id,
        "owner": "va",
        "desired_outcome": objective.goal,
        "stage": stage,
        "waiting_on": _waiting_on(objective, steps),
        "next_action": _next_action(objective, steps, followups),
        "next_check_at": _iso(next_check),
        "verification_required": _verification_policy(objective, steps),
        "verified_evidence": evidence_count,
        "rank_score": _rank_score(objective, stage, now),
        "due_at": _iso(objective.due_at),
    }
    if include_edges:
        edges = list(
            (
                await db.execute(
                    select(VACommitmentEdge).where(
                        (VACommitmentEdge.from_objective_id == objective.id)
                        | (VACommitmentEdge.to_objective_id == objective.id)
                    )
                )
            ).scalars()
        )
        projection["edges"] = [
            {
                "id": edge.id,
                "from_objective_id": edge.from_objective_id,
                "to_objective_id": edge.to_objective_id,
                "relation": edge.relation,
                "source": edge.source,
            }
            for edge in edges
        ]
    return projection


def _dependency_specs(objective: VAObjective) -> list[tuple[int, str, str]]:
    context = _loads(objective.context_json)
    plan = _loads(objective.plan_json)
    specs: list[tuple[int, str, str]] = []
    for payload, source in ((context, "context"), (plan, "plan")):
        single = {
            "prior_objective_id": "continues",
            "parent_objective_id": "child_of",
            "depends_on_objective_id": "depends_on",
            "blocked_by_objective_id": "blocked_by",
        }
        for key, relation in single.items():
            try:
                value = int(payload.get(key) or 0)
            except (TypeError, ValueError):
                value = 0
            if value > 0 and value != objective.id:
                specs.append((value, relation, f"{source}:{key}"))
        raw_many = payload.get("dependency_objective_ids")
        if isinstance(raw_many, list):
            for item in raw_many:
                try:
                    value = int(item)
                except (TypeError, ValueError):
                    continue
                if value > 0 and value != objective.id:
                    specs.append((value, "depends_on", f"{source}:dependency_objective_ids"))
    return specs


async def reconcile_commitment_graph(db: AsyncSession, *, limit: int = 1000) -> dict[str, int]:
    objectives = list(
        (
            await db.execute(
                select(VAObjective).order_by(VAObjective.id.asc()).limit(max(1, min(limit, 5000)))
            )
        ).scalars()
    )
    ids = {row.id for row in objectives}
    created = 0
    for objective in objectives:
        for dependency_id, relation, source in _dependency_specs(objective):
            if dependency_id not in ids:
                continue
            existing = (
                await db.execute(
                    select(VACommitmentEdge).where(
                        VACommitmentEdge.from_objective_id == objective.id,
                        VACommitmentEdge.to_objective_id == dependency_id,
                        VACommitmentEdge.relation == relation,
                    ).limit(1)
                )
            ).scalar_one_or_none()
            if existing is not None:
                continue
            edge = VACommitmentEdge(
                from_objective_id=objective.id,
                to_objective_id=dependency_id,
                relation=relation,
                source=source,
            )
            try:
                async with db.begin_nested():
                    db.add(edge)
                    await db.flush()
            except IntegrityError:
                continue
            created += 1
    if created:
        await write_audit(
            db,
            "va_commitment_graph_reconciled",
            entity_type="va_commitment_graph",
            entity_id="global",
            details={"objectives": len(objectives), "edges_created": created},
        )
        await db.commit()
    return {"objectives": len(objectives), "edges_created": created}


def _objective_summary(objective: VAObjective, commitment: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": objective.id,
        "title": objective.title,
        "goal": objective.goal,
        "category": objective.category,
        "priority": objective.priority,
        "risk_level": objective.risk_level,
        "status": objective.status,
        "due_at": objective.due_at,
        "needs_user_reason": objective.needs_user_reason,
        "blocked_reason": objective.blocked_reason,
        "updated_at": objective.updated_at,
        "finished_at": objective.finished_at,
        "commitment": commitment,
    }


async def executive_commitment_overview(db: AsyncSession, *, limit: int = 300) -> dict[str, Any]:
    rows = list(
        (
            await db.execute(
                select(VAObjective)
                .order_by(VAObjective.updated_at.desc(), VAObjective.id.desc())
                .limit(max(20, min(limit, 1000)))
            )
        ).scalars()
    )
    projected: list[tuple[VAObjective, dict[str, Any]]] = []
    for row in rows:
        projected.append((row, await commitment_projection(db, row)))
    projected.sort(key=lambda pair: (int(pair[1].get("rank_score") or 0), pair[0].updated_at), reverse=True)

    working: list[dict[str, Any]] = []
    waiting: list[dict[str, Any]] = []
    needs_user: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    finished: list[dict[str, Any]] = []
    for objective, commitment in projected:
        item = _objective_summary(objective, commitment)
        stage = commitment["stage"]
        if stage == "needs_user":
            needs_user.append(item)
        elif stage in {"working", "verifying"}:
            working.append(item)
        elif stage == "waiting":
            waiting.append(item)
        elif stage == "blocked_internal":
            blocked.append(item)
        elif stage == "finished":
            finished.append(item)

    active_count = len(working) + len(waiting) + len(blocked) + len(needs_user)
    if needs_user:
        summary = (
            f"I am looking after {active_count} open commitment{'s' if active_count != 1 else ''}. "
            f"{len(needs_user)} genuinely need{'s' if len(needs_user) == 1 else ''} your input; the rest remain with me."
        )
    elif active_count:
        summary = (
            f"I am looking after {active_count} open commitment{'s' if active_count != 1 else ''}. "
            "Nothing needs your attention right now; I will keep working, checking and following up."
        )
    else:
        summary = "Everything currently entrusted to me is closed or verified. Nothing needs your attention."

    return {
        "summary": summary,
        "counts": {
            "working": len(working),
            "waiting": len(waiting),
            "needs_user": len(needs_user),
            "resolving_internal": len(blocked),
        },
        "working_now": working[:15],
        "waiting_external": waiting[:15],
        "needs_user": needs_user[:25],
        "resolving_internal": blocked[:15],
        "recently_completed": sorted(
            finished,
            key=lambda item: item.get("finished_at") or item.get("updated_at") or datetime.min,
            reverse=True,
        )[:12],
    }


async def list_commitments(db: AsyncSession, *, stage: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    rows = list(
        (
            await db.execute(
                select(VAObjective)
                .order_by(VAObjective.updated_at.desc(), VAObjective.id.desc())
                .limit(max(1, min(limit, 1000)))
            )
        ).scalars()
    )
    result: list[dict[str, Any]] = []
    for objective in rows:
        commitment = await commitment_projection(db, objective)
        if stage and commitment["stage"] != stage:
            continue
        result.append(_objective_summary(objective, commitment))
    result.sort(key=lambda item: int((item.get("commitment") or {}).get("rank_score") or 0), reverse=True)
    return result


async def commitment_detail(db: AsyncSession, objective_id: int) -> dict[str, Any] | None:
    objective = await db.get(VAObjective, objective_id)
    if objective is None:
        return None
    commitment = await commitment_projection(db, objective, include_edges=True)
    return _objective_summary(objective, commitment)
