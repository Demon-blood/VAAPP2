from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import (
    AutomationRule,
    ScheduledConnectorMutationIntent,
    ServiceConnector,
)


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def connector_operation_is_mutating(
    connector_type: str,
    operation: str,
    parameters: dict[str, Any],
) -> bool:
    """Conservatively classify scheduled connector operations before dispatch."""

    kind = str(connector_type or "").strip().lower()
    op = str(operation or "").strip().lower()
    if kind in {"rest_api", "oauth2", "client_credentials"}:
        if op != "request":
            raise ValueError(f"{kind} connector supports operation=request")
        method = str(parameters.get("method") or "GET").upper()
        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            raise ValueError("Unsupported connector HTTP method")
        return method != "GET"
    if kind == "webhook":
        if op != "send":
            raise ValueError("Webhook connector supports operation=send")
        return True
    if kind == "telegram_bot":
        if op == "get_updates":
            return False
        if op == "send_message":
            return True
        raise ValueError("Telegram connector supports get_updates or send_message")
    if kind == "browserless":
        if op == "content":
            return False
        if op in {"function", "bql"}:
            # User-supplied browser code/query can mutate arbitrary provider state.
            return True
        raise ValueError("Browserless connector supports content, function or bql")
    if kind == "rss":
        if op != "latest":
            raise ValueError("RSS connector supports operation=latest")
        return False
    if kind == "imap_smtp":
        if op == "unread":
            return False
        if op == "send":
            return True
        raise ValueError("Mail connector supports operation=unread or operation=send")
    if kind == "webdav":
        if op == "list":
            return False
        if op == "upload":
            return True
        raise ValueError("WebDAV connector supports operation=list or operation=upload")
    if kind == "sftp":
        if op == "list":
            return False
        if op == "upload":
            return True
        raise ValueError("SFTP connector supports operation=list or operation=upload")
    raise ValueError(f"Unsupported connector type: {kind}")


def scheduled_connector_occurrence_key(
    *,
    rule_id: int,
    interval_minutes: int,
    now: datetime,
) -> str:
    interval = max(1, min(int(interval_minutes), 43_200))
    epoch = datetime(1970, 1, 1, tzinfo=UTC).replace(tzinfo=None)
    bucket = int((now - epoch).total_seconds()) // (interval * 60)
    return f"rule:{rule_id}:interval:{interval}:bucket:{bucket}"


def connector_request_fingerprint(
    connector: ServiceConnector,
    operation: str,
    parameters: dict[str, Any],
) -> str:
    canonical = json.dumps(
        {
            "connector_slug": connector.slug,
            "connector_type": connector.connector_type,
            "operation": operation,
            "parameters": parameters,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def prepare_scheduled_connector_mutation(
    db: AsyncSession,
    *,
    rule: AutomationRule,
    connector: ServiceConnector,
    operation: str,
    parameters: dict[str, Any],
    interval_minutes: int,
    now: datetime,
) -> ScheduledConnectorMutationIntent:
    occurrence_key = scheduled_connector_occurrence_key(
        rule_id=rule.id,
        interval_minutes=interval_minutes,
        now=now,
    )
    fingerprint = connector_request_fingerprint(connector, operation, parameters)
    existing = (
        await db.execute(
            select(ScheduledConnectorMutationIntent)
            .where(
                ScheduledConnectorMutationIntent.automation_rule_id == rule.id,
                ScheduledConnectorMutationIntent.occurrence_key == occurrence_key,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.request_fingerprint != fingerprint:
            raise RuntimeError(
                "Scheduled connector occurrence changed after it was durably prepared"
            )
        return existing

    row = ScheduledConnectorMutationIntent(
        automation_rule_id=rule.id,
        service_connector_id=connector.id,
        occurrence_key=occurrence_key,
        connector_slug=connector.slug,
        connector_type=connector.connector_type,
        operation=operation,
        request_fingerprint=fingerprint,
        status="prepared",
    )
    db.add(row)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = (
            await db.execute(
                select(ScheduledConnectorMutationIntent)
                .where(
                    ScheduledConnectorMutationIntent.automation_rule_id == rule.id,
                    ScheduledConnectorMutationIntent.occurrence_key == occurrence_key,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if existing is None:
            raise
        if existing.request_fingerprint != fingerprint:
            raise RuntimeError(
                "Scheduled connector occurrence collision has different request content"
            )
        return existing
    await db.refresh(row)
    return row


async def claim_scheduled_connector_mutation(
    db: AsyncSession,
    *,
    intent: ScheduledConnectorMutationIntent,
    rule: AutomationRule,
    claimed_at: datetime,
) -> bool:
    result = await db.execute(
        update(ScheduledConnectorMutationIntent)
        .where(
            ScheduledConnectorMutationIntent.id == intent.id,
            ScheduledConnectorMutationIntent.status == "prepared",
        )
        .values(
            status="submitting",
            attempts=ScheduledConnectorMutationIntent.attempts + 1,
            started_at=claimed_at,
            last_error="",
            updated_at=claimed_at,
        )
    )
    claimed = int(getattr(result, "rowcount", 0) or 0) == 1
    if claimed:
        # Commit schedule ownership together with the no-replay provider claim.
        rule.last_run_at = claimed_at
    await db.commit()
    await db.refresh(intent)
    return claimed


async def complete_scheduled_connector_mutation(
    db: AsyncSession,
    *,
    intent: ScheduledConnectorMutationIntent,
    result: dict[str, Any],
) -> None:
    intent.status = "succeeded"
    intent.result_json = json.dumps(result, ensure_ascii=False, default=str)[:20_000]
    intent.last_error = ""
    intent.finished_at = utcnow()
    await db.commit()


async def mark_scheduled_connector_mutation_uncertain(
    db: AsyncSession,
    *,
    intent: ScheduledConnectorMutationIntent,
    error: Exception,
) -> None:
    intent.status = "execution_uncertain"
    intent.last_error = (
        "Provider mutation outcome is uncertain; automatic replay of this scheduled "
        f"occurrence is disabled: {error}"
    )[:4000]
    await db.commit()


def mutation_replay_status(intent: ScheduledConnectorMutationIntent) -> str:
    if intent.status == "succeeded":
        return "Scheduled connector occurrence already completed; duplicate dispatch suppressed"
    if intent.status == "execution_uncertain":
        return (
            "Scheduled connector provider outcome is uncertain; this occurrence is "
            "reconciliation-only and will not be replayed"
        )
    if intent.status == "submitting":
        return (
            "Scheduled connector dispatch is already claimed; duplicate provider "
            "mutation is suppressed"
        )
    return f"Scheduled connector occurrence is in state {intent.status}"
