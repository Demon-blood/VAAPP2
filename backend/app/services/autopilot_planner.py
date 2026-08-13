from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import AuditLog, Bill, EmailMessage, GmailOutboundMessage, Payment, Task
from app.schemas.api import AutomationDecision
from app.services.audit import write_audit
from app.services.autonomy_policy import reply_autonomy_decision
from app.services.communication_ownership import queue_saved_email_reply
from app.services.runtime_config import get_runtime_value
from app.services.workflow_engine import enqueue_job


def utcnow() -> datetime:
    return datetime.utcnow()


def _bill_version_key(bill: Bill) -> str:
    stamp = bill.updated_at or bill.created_at or utcnow()
    return f"autoplan:bill:{bill.id}:{int(stamp.timestamp())}"


async def _reply_already_sent(db: AsyncSession, message_id: str) -> bool:
    audit = (
        await db.execute(
            select(AuditLog.id).where(
                AuditLog.event_type == "email_reply_sent",
                AuditLog.entity_type == "email",
                AuditLog.entity_id == message_id,
            ).limit(1)
        )
    ).scalar_one_or_none()
    if audit is not None:
        return True
    verified = (
        await db.execute(
            select(GmailOutboundMessage.id).where(
                GmailOutboundMessage.source_message_id == message_id,
                GmailOutboundMessage.status == "verified",
            ).limit(1)
        )
    ).scalar_one_or_none()
    return verified is not None


async def _execute_safe_queued_replies(db: AsyncSession, *, limit: int = 30) -> dict[str, int]:
    tasks = list(
        (
            await db.execute(
                select(Task)
                .where(Task.source_type == "email_reply", Task.status.in_(["open", "waiting"]))
                .order_by(Task.created_at.asc())
                .limit(limit)
            )
        ).scalars()
    )
    queued = 0
    retained = 0
    for task in tasks:
        if not task.source_id:
            retained += 1
            continue
        message = (
            await db.execute(
                select(EmailMessage).where(EmailMessage.provider_message_id == task.source_id).limit(1)
            )
        ).scalar_one_or_none()
        if message is None:
            retained += 1
            continue
        if await _reply_already_sent(db, message.provider_message_id):
            task.status = "completed"
            continue
        try:
            decision = AutomationDecision.model_validate_json(message.analysis_json or "{}")
        except Exception:
            retained += 1
            continue
        if not decision.reply:
            retained += 1
            continue
        allowed, reason = await reply_autonomy_decision(db, message=message, decision=decision)
        if not allowed:
            task.requires_approval = True
            retained += 1
            continue
        await queue_saved_email_reply(
            db,
            record=message,
            recipient=str(decision.reply.get("to") or message.sender),
            subject=str(decision.reply.get("subject") or f"Re: {message.subject}"),
            body=str(decision.reply.get("body") or ""),
            priority=message.priority,
            expect_reply=bool(message.action_required),
            follow_up_hours=48,
            policy=reason,
        )
        task.status = "waiting"
        task.requires_approval = False
        await write_audit(
            db,
            "email_reply_migrated_to_objective",
            entity_type="email",
            entity_id=message.provider_message_id,
            details={"task_id": task.id, "policy": reason},
        )
        queued += 1
    if tasks:
        await db.commit()
    # Keep the historical `sent` key for callers, but Phase 2 never equates queueing
    # durable work with a provider-confirmed send.
    return {"sent": 0, "queued": queued, "retained_for_user": retained}


async def _plan_bills(db: AsyncSession) -> dict[str, int]:
    if (await get_runtime_value(db, "auto_pay_enabled", "true")).lower() != "true":
        return {"eligible": 0, "enqueued": 0, "disabled": 1}
    now = utcnow()
    try:
        days = max(0, min(int(await get_runtime_value(db, "auto_pay_days_before_due", "3")), 30))
    except ValueError:
        days = 3
    horizon = now + timedelta(days=days)
    bills = list(
        (
            await db.execute(
                select(Bill).where(
                    Bill.status == "validated",
                    (Bill.due_at.is_(None) | (Bill.due_at <= horizon)),
                )
            )
        ).scalars()
    )
    enqueued = 0
    for bill in bills:
        active_payment = (
            await db.execute(
                select(Payment.id).where(
                    Payment.bill_id == bill.id,
                    Payment.status.not_in(["failed", "cancelled", "rejected"]),
                ).limit(1)
            )
        ).scalar_one_or_none()
        if active_payment is not None:
            continue
        _, created = await enqueue_job(
            db,
            job_type="bill.lifecycle",
            payload={"bill_id": bill.id, "source": "proactive_planner"},
            idempotency_key=_bill_version_key(bill),
            priority=8,
            max_attempts=10,
        )
        enqueued += int(created)
    return {"eligible": len(bills), "enqueued": enqueued}


async def _escalate_unexecutable_due_tasks(db: AsyncSession, *, limit: int = 30) -> dict[str, int]:
    """Convert due tasks without a deterministic executor into explicit ambiguity exceptions.

    This avoids pretending the VA completed work it cannot safely execute while keeping routine executable
    work autonomous. It intentionally excludes retryable infrastructure tasks, which provider recovery handles.
    """

    now = utcnow()
    unsupported = {"email", "email_action", "support_followup"}
    rows = list(
        (
            await db.execute(
                select(Task).where(
                    Task.status.in_(["open", "waiting"]),
                    Task.requires_approval.is_(False),
                    Task.source_type.in_(unsupported),
                    Task.due_at.is_not(None),
                    Task.due_at <= now,
                ).order_by(Task.due_at.asc()).limit(limit)
            )
        ).scalars()
    )
    for task in rows:
        task.requires_approval = True
        task.priority = "high" if task.priority == "normal" else task.priority
        await write_audit(
            db,
            "autopilot_task_escalated",
            entity_type="task",
            entity_id=str(task.id),
            result="needs_user",
            details={"source_type": task.source_type, "reason": "no deterministic executor available at deadline"},
        )
    if rows:
        await db.commit()
    return {"escalated": len(rows)}


async def proactive_plan(db: AsyncSession) -> dict[str, Any]:
    enabled = (await get_runtime_value(db, "autopilot_planner_enabled", "true")).lower() == "true"
    if not enabled:
        return {"planned_at": utcnow().isoformat() + "Z", "disabled": True}
    bills = await _plan_bills(db)
    replies = await _execute_safe_queued_replies(db)
    tasks = await _escalate_unexecutable_due_tasks(db)
    from app.services.action_reconciler import reconcile_action_queue

    reconciled = await reconcile_action_queue(db)
    return {
        "planned_at": utcnow().isoformat() + "Z",
        "bills": bills,
        "replies": replies,
        "tasks": tasks,
        "action_reconciliation": reconciled,
    }
