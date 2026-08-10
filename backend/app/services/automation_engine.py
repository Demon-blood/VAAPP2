from __future__ import annotations

import json
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import AutomationRule, ServiceConnector, Task
from app.services.audit import write_audit
from app.services.connector_service import execute_connector
from app.services.runtime_config import get_runtime_value
from app.services.workflow_engine import failure_recovery_class


async def _ensure_rule_exception_task(
    db: AsyncSession,
    rule: AutomationRule,
    *,
    description: str,
) -> Task:
    existing = (
        await db.execute(
            select(Task).where(
                Task.source_type == "automation_rule",
                Task.source_id == str(rule.id),
                Task.status.in_(["open", "waiting"]),
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.description = description
        existing.requires_approval = True
        existing.priority = "high"
        return existing
    task = Task(
        title=f"Automation decision needed: {rule.name}",
        description=description,
        source_type="automation_rule",
        source_id=str(rule.id),
        priority="high",
        requires_approval=True,
    )
    db.add(task)
    return task


async def run_connector_automation_rules(db: AsyncSession) -> dict[str, int]:
    enabled = (await get_runtime_value(db, "connector_automation_enabled", "true")).lower() == "true"
    if not enabled:
        return {"executed": 0, "skipped": 0, "failed": 0}

    rules = list(
        (
            await db.execute(
                select(AutomationRule).where(
                    AutomationRule.rule_type == "connector_schedule",
                    AutomationRule.enabled.is_(True),
                )
            )
        ).scalars()
    )
    now = datetime.utcnow()
    outcome = {"executed": 0, "skipped": 0, "failed": 0}
    for rule in rules:
        try:
            conditions = json.loads(rule.conditions_json or "{}")
            actions = json.loads(rule.actions_json or "{}")
            interval = max(1, min(int(conditions.get("interval_minutes") or 60), 43_200))
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            rule.last_result = "Invalid rule configuration"
            outcome["failed"] += 1
            await _ensure_rule_exception_task(
                db,
                rule,
                description=f"The scheduled connector rule is ambiguous or invalid and cannot run unattended: {exc}",
            )
            continue
        if rule.last_run_at and now < rule.last_run_at + timedelta(minutes=interval):
            outcome["skipped"] += 1
            continue
        connector_slug = str(actions.get("connector_slug") or "")
        operation = str(actions.get("operation") or "")
        connector = (
            await db.execute(select(ServiceConnector).where(ServiceConnector.slug == connector_slug))
        ).scalar_one_or_none()
        rule.last_run_at = now
        if connector is None or not operation:
            rule.last_result = "Connector or operation is missing"
            outcome["failed"] += 1
            await _ensure_rule_exception_task(
                db,
                rule,
                description="The connector or requested operation is missing. Connect/configure it before this rule can continue.",
            )
            continue
        try:
            result = await execute_connector(
                db,
                connector,
                operation,
                dict(actions.get("parameters") or {}),
            )
            rule.last_result = json.dumps(result, ensure_ascii=False)[:4000]
            outcome["executed"] += 1
            await write_audit(
                db,
                "scheduled_connector_rule_executed",
                entity_type="automation_rule",
                entity_id=str(rule.id),
                details={"connector": connector_slug, "operation": operation},
            )
            stale_task = (
                await db.execute(
                    select(Task).where(
                        Task.source_type == "automation_rule",
                        Task.source_id == str(rule.id),
                        Task.status.in_(["open", "waiting"]),
                    )
                )
            ).scalar_one_or_none()
            if stale_task is not None:
                stale_task.status = "completed"
        except Exception as exc:
            rule.last_result = str(exc)[:4000]
            outcome["failed"] += 1
            recovery_class = failure_recovery_class("connectors.rules.run", str(exc))
            if recovery_class in {"transient", "user_required"}:
                # The durable workflow engine owns provider backoff/recovery and will surface
                # OAuth/security setup only after retries cannot resolve it.
                raise
            await _ensure_rule_exception_task(
                db,
                rule,
                description=f"Autopilot cannot safely infer how to repair this connector rule: {exc}",
            )
            await write_audit(
                db,
                "scheduled_connector_rule_failed",
                entity_type="automation_rule",
                entity_id=str(rule.id),
                result="needs_user",
                details={
                    "connector": connector_slug,
                    "operation": operation,
                    "error": str(exc),
                    "recovery_class": recovery_class,
                },
            )
    await db.commit()
    return outcome
