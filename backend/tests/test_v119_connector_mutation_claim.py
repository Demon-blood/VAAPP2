import base64
import json
import os
from datetime import UTC, datetime, timedelta

os.environ.setdefault("PUBLIC_BASE_URL", "https://va.example.test")
os.environ.setdefault("PAIRING_SECRET", "x" * 32)
os.environ.setdefault(
    "TOKEN_ENCRYPTION_KEY",
    base64.urlsafe_b64encode(b"1" * 32).decode(),
)

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models.entities import (
    AutomationRule,
    ScheduledConnectorMutationIntent,
    ServiceConnector,
    Task,
    WorkflowJob,
)
from app.services.automation_engine import run_connector_automation_rules
from app.services.connector_mutation_recovery import (
    claim_scheduled_connector_mutation,
    connector_operation_is_mutating,
    prepare_scheduled_connector_mutation,
)
from app.services.workflow_engine import repair_v119_connector_rule_retry_backlog


def _dt(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=UTC).replace(tzinfo=None)


async def _sessions():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _connector() -> ServiceConnector:
    return ServiceConnector(
        slug="scheduled-webhook",
        display_name="Scheduled webhook",
        category="universal",
        connector_type="webhook",
        config_json_encrypted="unused-in-test",
        capabilities_json='["send"]',
        enabled=True,
        status="connected",
    )


def _rule(*, interval: int = 60) -> AutomationRule:
    return AutomationRule(
        rule_type="connector_schedule",
        name="Scheduled provider write",
        conditions_json=json.dumps({"interval_minutes": interval}),
        actions_json=json.dumps(
            {
                "connector_slug": "scheduled-webhook",
                "operation": "send",
                "parameters": {"payload": {"kind": "daily-report"}},
            }
        ),
        enabled=True,
    )


@pytest.mark.asyncio
async def test_ambiguous_scheduled_mutation_is_never_replayed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, sessions = await _sessions()
    calls = 0

    async def ambiguous_execute(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise TimeoutError("provider accepted request but response was lost")

    fixed_now = _dt(2026, 9, 5, 10)

    class FakeDatetime(datetime):
        @classmethod
        def utcnow(cls):
            return fixed_now

    monkeypatch.setattr("app.services.automation_engine.datetime", FakeDatetime)
    monkeypatch.setattr("app.services.automation_engine.execute_connector", ambiguous_execute)

    async with sessions() as db:
        connector = _connector()
        rule = _rule()
        db.add_all([connector, rule])
        await db.commit()

        first = await run_connector_automation_rules(db)
        assert first["failed"] == 1
        assert calls == 1

        intent = (
            await db.execute(select(ScheduledConnectorMutationIntent))
        ).scalar_one()
        assert intent.status == "execution_uncertain"
        assert intent.attempts == 1
        assert rule.last_run_at is not None

        # Simulate a stale workflow retry that lost the rule timestamp but retained
        # the durable occurrence claim. The provider call must still not repeat.
        rule.last_run_at = None
        await db.commit()
        second = await run_connector_automation_rules(db)
        assert second["skipped"] == 1
        assert calls == 1

        tasks = list(
            (
                await db.execute(
                    select(Task).where(
                        Task.source_type == "automation_rule",
                        Task.source_id == str(rule.id),
                        Task.status.in_(["open", "waiting"]),
                    )
                )
            ).scalars()
        )
        assert tasks == []
    await engine.dispose()


@pytest.mark.asyncio
async def test_atomic_claim_allows_only_one_worker_to_dispatch() -> None:
    engine, sessions = await _sessions()
    async with sessions() as db:
        connector = _connector()
        rule = _rule()
        db.add_all([connector, rule])
        await db.commit()

        now = _dt(2026, 9, 5, 12)
        intent = await prepare_scheduled_connector_mutation(
            db,
            rule=rule,
            connector=connector,
            operation="send",
            parameters={"payload": {"kind": "daily-report"}},
            interval_minutes=60,
            now=now,
        )
        first = await claim_scheduled_connector_mutation(
            db,
            intent=intent,
            rule=rule,
            claimed_at=now,
        )
        second = await claim_scheduled_connector_mutation(
            db,
            intent=intent,
            rule=rule,
            claimed_at=now,
        )
        assert first is True
        assert second is False
        assert intent.status == "submitting"
        assert intent.attempts == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_next_scheduled_occurrence_remains_independent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, sessions = await _sessions()
    calls = 0
    current = _dt(2026, 9, 5, 10)

    class FakeDatetime(datetime):
        @classmethod
        def utcnow(cls):
            return current

    async def execute_once_per_occurrence(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError("first occurrence outcome unknown")
        return {"status": 200, "body": {"ok": True}}

    monkeypatch.setattr("app.services.automation_engine.datetime", FakeDatetime)
    monkeypatch.setattr(
        "app.services.automation_engine.execute_connector",
        execute_once_per_occurrence,
    )

    async with sessions() as db:
        db.add_all([_connector(), _rule(interval=60)])
        await db.commit()

        first = await run_connector_automation_rules(db)
        assert first["failed"] == 1
        assert calls == 1

        current = current + timedelta(minutes=61)
        second = await run_connector_automation_rules(db)
        assert second["executed"] == 1
        assert calls == 2

        intents = list(
            (
                await db.execute(
                    select(ScheduledConnectorMutationIntent).order_by(
                        ScheduledConnectorMutationIntent.id
                    )
                )
            ).scalars()
        )
        assert [row.status for row in intents] == [
            "execution_uncertain",
            "succeeded",
        ]
        assert intents[0].occurrence_key != intents[1].occurrence_key
    await engine.dispose()


@pytest.mark.asyncio
async def test_read_only_rule_keeps_normal_transient_retry_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, sessions = await _sessions()

    async def failing_read(*args, **kwargs):
        raise TimeoutError("temporary read timeout")

    monkeypatch.setattr("app.services.automation_engine.execute_connector", failing_read)

    async with sessions() as db:
        connector = ServiceConnector(
            slug="feed",
            display_name="Feed",
            category="universal",
            connector_type="rss",
            config_json_encrypted="unused-in-test",
            capabilities_json='["read"]',
            enabled=True,
            status="connected",
        )
        rule = AutomationRule(
            rule_type="connector_schedule",
            name="Read feed",
            conditions_json='{"interval_minutes": 60}',
            actions_json=json.dumps(
                {
                    "connector_slug": "feed",
                    "operation": "latest",
                    "parameters": {"limit": 10},
                }
            ),
            enabled=True,
        )
        db.add_all([connector, rule])
        await db.commit()

        with pytest.raises(TimeoutError):
            await run_connector_automation_rules(db)
        assert (
            await db.execute(select(ScheduledConnectorMutationIntent))
        ).scalar_one_or_none() is None
    await engine.dispose()


@pytest.mark.asyncio
async def test_legacy_connector_retry_backlog_is_quarantined_once() -> None:
    engine, sessions = await _sessions()
    async with sessions() as db:
        rows = [
            WorkflowJob(
                job_type="connectors.rules.run",
                idempotency_key="legacy:retry",
                status="retry",
                run_after=_dt(2026, 9, 5),
            ),
            WorkflowJob(
                job_type="connectors.rules.run",
                idempotency_key="legacy:dead",
                status="dead_letter",
                run_after=_dt(2026, 9, 5),
            ),
            WorkflowJob(
                job_type="connectors.rules.run",
                idempotency_key="legacy:running",
                status="running",
                run_after=_dt(2026, 9, 5),
                lease_owner="old-worker",
            ),
            WorkflowJob(
                job_type="gmail.sync",
                idempotency_key="unrelated",
                status="retry",
                run_after=_dt(2026, 9, 5),
            ),
        ]
        db.add_all(rows)
        await db.commit()

        repaired = await repair_v119_connector_rule_retry_backlog(db)
        assert repaired == {"superseded": 3, "already_repaired": 0}
        await db.refresh(rows[0])
        await db.refresh(rows[1])
        await db.refresh(rows[2])
        await db.refresh(rows[3])
        assert [rows[index].status for index in range(3)] == [
            "superseded",
            "superseded",
            "superseded",
        ]
        assert rows[3].status == "retry"
        assert rows[2].lease_owner == ""

        again = await repair_v119_connector_rule_retry_backlog(db)
        assert again == {"superseded": 0, "already_repaired": 1}
    await engine.dispose()


def test_connector_write_classification_is_fail_closed() -> None:
    assert connector_operation_is_mutating("webhook", "send", {}) is True
    assert connector_operation_is_mutating(
        "rest_api",
        "request",
        {"method": "POST"},
    ) is True
    assert connector_operation_is_mutating(
        "oauth2",
        "request",
        {"method": "DELETE"},
    ) is True
    assert connector_operation_is_mutating("telegram_bot", "send_message", {}) is True
    assert connector_operation_is_mutating("imap_smtp", "send", {}) is True
    assert connector_operation_is_mutating("webdav", "upload", {}) is True
    assert connector_operation_is_mutating("sftp", "upload", {}) is True
    assert connector_operation_is_mutating("browserless", "function", {}) is True
    assert connector_operation_is_mutating("rss", "latest", {}) is False
    assert connector_operation_is_mutating("telegram_bot", "get_updates", {}) is False
    assert connector_operation_is_mutating(
        "rest_api",
        "request",
        {"method": "GET"},
    ) is False
