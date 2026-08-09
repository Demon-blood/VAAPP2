from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import AuditLog, Bill, EmailMessage, SupportCase, Task
from app.schemas.api import AutomationDecision

_OPEN_STATUSES = {"open", "waiting"}
_BILL_REVIEW_STATUSES = {"detected", "requires_review"}


async def _audit_exists(db: AsyncSession, event_type: str, message_id: str) -> bool:
    row = (
        await db.execute(
            select(AuditLog.id)
            .where(
                AuditLog.event_type == event_type,
                AuditLog.entity_type == "email",
                AuditLog.entity_id == message_id,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    return row is not None


def _new_task(
    *,
    title: str,
    description: str,
    source_type: str,
    source_id: str,
    priority: str,
    requires_approval: bool,
) -> Task:
    return Task(
        title=title,
        description=description,
        source_type=source_type,
        source_id=source_id,
        priority=priority,
        requires_approval=requires_approval,
    )


async def reconcile_action_queue(db: AsyncSession, *, limit: int = 500) -> dict[str, int]:
    """Ensure every email action is either resolved or represented by a concrete task.

    The reconciler never invents a side effect. It only closes action flags when a recorded
    execution exists, or creates an explicit task for the remaining human/exception step.
    """

    emails = list(
        (
            await db.execute(
                select(EmailMessage)
                .where(EmailMessage.action_required.is_(True))
                .order_by(EmailMessage.received_at.asc().nullsfirst(), EmailMessage.id)
                .limit(limit)
            )
        ).scalars()
    )
    result = {"resolved": 0, "queued": 0, "created_tasks": 0}

    for record in emails:
        message_id = record.provider_message_id
        try:
            decision = AutomationDecision.model_validate_json(record.analysis_json or "{}")
        except Exception:
            decision = None

        tasks = list(
            (
                await db.execute(
                    select(Task).where(Task.source_id == message_id).order_by(Task.id)
                )
            ).scalars()
        )
        open_tasks = [task for task in tasks if task.status in _OPEN_STATUSES]
        completed_types = {task.source_type for task in tasks if task.status == "completed"}

        calendar_done = await _audit_exists(db, "calendar_event_created", message_id)
        reply_done = await _audit_exists(db, "email_reply_sent", message_id)
        bill = (
            await db.execute(select(Bill).where(Bill.source_message_id == message_id).limit(1))
        ).scalar_one_or_none()
        support_case = (
            await db.execute(select(SupportCase).where(SupportCase.source_message_id == message_id).limit(1))
        ).scalar_one_or_none()

        # Close stale exception tasks automatically when the underlying action is now complete.
        for task in open_tasks:
            if task.source_type == "calendar_review" and calendar_done:
                task.status = "completed"
            elif task.source_type == "email_reply" and reply_done:
                task.status = "completed"
            elif task.source_type == "bill_review" and bill is not None and bill.status not in _BILL_REVIEW_STATUSES:
                task.status = "completed"
            elif task.source_type == "support_followup" and support_case is not None and support_case.status in {"resolved", "closed"}:
                task.status = "completed"

        open_tasks = [task for task in tasks if task.status in _OPEN_STATUSES]
        open_types = {task.source_type for task in open_tasks}

        if decision is None:
            if "email_action" not in open_types and "email_action" not in completed_types:
                db.add(
                    _new_task(
                        title=f"Review email action: {record.subject or '(No subject)'}",
                        description="The saved decision could not be reconstructed. Review this message before clearing the action.",
                        source_type="email_action",
                        source_id=message_id,
                        priority=record.priority,
                        requires_approval=True,
                    )
                )
                result["created_tasks"] += 1
                open_types.add("email_action")
        else:
            if decision.task and "email" not in open_types and "email" not in completed_types:
                db.add(
                    _new_task(
                        title=str(decision.task.get("title") or record.subject or "Email action"),
                        description=str(decision.task.get("description") or decision.reasoning_summary),
                        source_type="email",
                        source_id=message_id,
                        priority=decision.priority,
                        requires_approval=bool(decision.task.get("requires_approval", False)),
                    )
                )
                result["created_tasks"] += 1
                open_types.add("email")

            if decision.reply and not reply_done and "email_reply" not in open_types and "email_reply" not in completed_types:
                db.add(
                    _new_task(
                        title=f"Approve reply: {record.subject}",
                        description=str(decision.reply.get("body") or decision.reasoning_summary),
                        source_type="email_reply",
                        source_id=message_id,
                        priority=decision.priority,
                        requires_approval=True,
                    )
                )
                result["created_tasks"] += 1
                open_types.add("email_reply")

            if decision.calendar_event and not calendar_done and "calendar_review" not in open_types and "calendar_review" not in completed_types:
                db.add(
                    _new_task(
                        title=f"Review calendar item: {record.subject}",
                        description=decision.reasoning_summary or "The calendar action still needs confirmation or retry.",
                        source_type="calendar_review",
                        source_id=message_id,
                        priority="high",
                        requires_approval=True,
                    )
                )
                result["created_tasks"] += 1
                open_types.add("calendar_review")

            if bill is not None and bill.status in _BILL_REVIEW_STATUSES and "bill_review" not in open_types:
                db.add(
                    _new_task(
                        title=f"Review bill: {bill.creditor_name}",
                        description=bill.risk_reason or "Approve the creditor/IBAN policy before automatic payment can continue.",
                        source_type="bill_review",
                        source_id=message_id,
                        priority="high" if decision.priority in {"high", "urgent"} else decision.priority,
                        requires_approval=True,
                    )
                )
                result["created_tasks"] += 1
                open_types.add("bill_review")

            if (
                support_case is not None
                and support_case.status in {"open", "waiting"}
                and decision.action_required
                and "support_followup" not in open_types
                and "support_followup" not in completed_types
            ):
                db.add(
                    _new_task(
                        title=f"Follow up: {support_case.subject}",
                        description=support_case.last_action or decision.reasoning_summary,
                        source_type="support_followup",
                        source_id=message_id,
                        priority=support_case.priority,
                        requires_approval=False,
                    )
                )
                result["created_tasks"] += 1
                open_types.add("support_followup")

            has_structured_pending = bool(open_types)
            completed_structured = (
                (decision.task is not None and "email" in completed_types)
                or (decision.reply is not None and "email_reply" in completed_types)
                or (decision.calendar_event is not None and "calendar_review" in completed_types)
                or "email_action" in completed_types
                or "support_followup" in completed_types
            )
            if (
                decision.action_required
                and not has_structured_pending
                and not completed_structured
                and not calendar_done
                and not reply_done
                and not (bill is not None and bill.status not in _BILL_REVIEW_STATUSES)
            ):
                db.add(
                    _new_task(
                        title=f"Complete email action: {record.subject or '(No subject)'}",
                        description=decision.reasoning_summary or "This message requires a follow-up that cannot be executed safely without confirmation.",
                        source_type="email_action",
                        source_id=message_id,
                        priority=decision.priority,
                        requires_approval=True,
                    )
                )
                result["created_tasks"] += 1
                open_types.add("email_action")

        await db.flush()
        remaining = (
            await db.execute(
                select(Task.id).where(
                    Task.source_id == message_id,
                    Task.status.in_(["open", "waiting"]),
                )
            )
        ).first()
        bill_pending = bill is not None and bill.status in _BILL_REVIEW_STATUSES
        support_pending = (
            support_case is not None
            and support_case.status in {"open", "waiting"}
            and "support_followup" not in completed_types
        )
        unresolved = remaining is not None or bill_pending or support_pending

        if unresolved:
            result["queued"] += 1
        else:
            record.action_required = False
            result["resolved"] += 1

    await db.commit()
    return result
