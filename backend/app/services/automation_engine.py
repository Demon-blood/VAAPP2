from __future__ import annotations

import json
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import AutomationRule, ServiceConnector, Task
from app.services.audit import write_audit
from app.services.connector_service import execute_connector
from app.services.runtime_config import get_runtime_value


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
        except (ValueError, TypeError, json.JSONDecodeError):
            rule.last_result = "Invalid rule configuration"
            outcome["failed"] += 1
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
        except Exception as exc:
            rule.last_result = str(exc)[:4000]
            outcome["failed"] += 1
            existing_task = (
                await db.execute(
                    select(Task).where(
                        Task.source_type == "automation_rule",
                        Task.source_id == str(rule.id),
                        Task.status.in_(["open", "waiting"]),
                    )
                )
            ).scalar_one_or_none()
            if existing_task is None:
                db.add(
                    Task(
                        title=f"Automation failed: {rule.name}",
                        description=str(exc),
                        source_type="automation_rule",
                        source_id=str(rule.id),
                        priority="high",
                        requires_approval=False,
                    )
                )
            await write_audit(
                db,
                "scheduled_connector_rule_failed",
                entity_type="automation_rule",
                entity_id=str(rule.id),
                result="failed",
                details={"connector": connector_slug, "operation": operation, "error": str(exc)},
            )
    await db.commit()
    return outcome
