from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models.entities import CalendarMutation, VAFollowUp, VAObjectiveStep
from app.services.autonomous_core import objective_from_event, record_event
from app.services.calendar_ownership import (
    deterministic_calendar_event_id,
    prepare_calendar_mutation,
    send_or_reconcile_calendar_mutation,
)


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def test_deterministic_calendar_event_id_is_stable_and_provider_safe() -> None:
    first = deterministic_calendar_event_id("objective:17:calendar:create")
    second = deterministic_calendar_event_id("objective:17:calendar:create")
    third = deterministic_calendar_event_id("objective:18:calendar:create")
    assert first == second
    assert first != third
    assert first.startswith("va")
    assert set(first) <= set("0123456789abcdefv")


@pytest.mark.asyncio
async def test_calendar_plan_becomes_mutation_and_response_wait(db) -> None:
    event, _ = await record_event(
        db,
        event_key="calendar-plan:test-1",
        source_type="manual",
        source_id="request-1",
        event_type="calendar_event_planned",
        title="Schedule project review",
        payload={
            "operation": "create",
            "summary": "Project review",
            "start": "2026-08-20T14:00:00+02:00",
            "end": "2026-08-20T14:30:00+02:00",
            "timezone": "Europe/Brussels",
            "attendees": ["person@example.invalid"],
            "expect_response": True,
        },
    )
    objective = await objective_from_event(db, event)
    steps = list(
        (
            await db.execute(
                select(VAObjectiveStep)
                .where(VAObjectiveStep.objective_id == objective.id)
                .order_by(VAObjectiveStep.position)
            )
        ).scalars()
    )
    assert objective.status == "planned"
    assert [row.action_type for row in steps] == ["calendar_mutation", "wait"]
    assert steps[0].verification_type == "calendar_mutation_verified"
    assert steps[1].verification_type == "calendar_attendee_response"


@pytest.mark.asyncio
async def test_conflict_blocks_before_provider_create_without_fake_completion(db, monkeypatch) -> None:
    row = await prepare_calendar_mutation(
        db,
        idempotency_key="calendar-conflict-test",
        operation="create",
        desired_event={
            "summary": "Double booking",
            "start": "2026-08-20T14:00:00+02:00",
            "end": "2026-08-20T14:30:00+02:00",
            "avoid_conflicts": True,
        },
    )

    async def not_yet(*_args, **_kwargs):
        return False

    async def conflicts(*_args, **_kwargs):
        return [{"start": "2026-08-20T12:00:00Z", "end": "2026-08-20T12:30:00Z"}]

    monkeypatch.setattr("app.services.calendar_ownership.reconcile_calendar_mutation", not_yet)
    monkeypatch.setattr("app.services.calendar_ownership.find_calendar_conflicts", conflicts)
    returned = await send_or_reconcile_calendar_mutation(db, row)
    assert returned.status == "needs_user_conflict"
    assert returned.verified_at is None


@pytest.mark.asyncio
async def test_calendar_attendee_response_cancels_chase_and_completes_wait(db) -> None:
    source, _ = await record_event(
        db,
        event_key="calendar-plan:response-test",
        source_type="manual",
        source_id="request-response",
        event_type="calendar_event_planned",
        title="Schedule response test",
        payload={
            "operation": "create",
            "summary": "Response test",
            "start": "2026-08-20T14:00:00+02:00",
            "end": "2026-08-20T14:30:00+02:00",
            "attendees": ["person@example.invalid"],
            "expect_response": True,
        },
    )
    objective = await objective_from_event(db, source)
    steps = list(
        (
            await db.execute(
                select(VAObjectiveStep)
                .where(VAObjectiveStep.objective_id == objective.id)
                .order_by(VAObjectiveStep.position)
            )
        ).scalars()
    )
    steps[0].status = "completed"
    steps[0].finished_at = datetime.utcnow()
    steps[1].status = "waiting"
    objective.status = "waiting_external"
    followup = VAFollowUp(
        objective_id=objective.id,
        channel="email",
        target="person@example.invalid",
        purpose="Confirm attendance",
        due_at=datetime.utcnow() + timedelta(hours=24),
        status="pending",
    )
    db.add(followup)
    await db.commit()

    response, _ = await record_event(
        db,
        event_key="calendar:event-1:attendee-response:accepted",
        source_type="calendar",
        source_id="event-1",
        event_type="calendar_attendee_response_received",
        title="Calendar responses received",
        payload={
            "prior_objective_id": objective.id,
            "provider_event_id": "event-1",
            "attendees": [{"email": "person@example.invalid", "responseStatus": "accepted"}],
        },
    )
    returned = await objective_from_event(db, response)
    assert returned.id == objective.id
    await db.refresh(objective)
    await db.refresh(followup)
    assert objective.status == "completed"
    assert followup.status == "cancelled"
