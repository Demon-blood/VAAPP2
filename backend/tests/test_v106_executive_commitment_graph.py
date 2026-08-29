from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models.entities import VACommitmentEdge, VAFollowUp, VAObjective, VAObjectiveStep
from app.services.commitment_graph import (
    commitment_projection,
    executive_commitment_overview,
    reconcile_commitment_graph,
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


def objective(**kwargs):
    defaults = dict(
        correlation_key=f"test:{kwargs.get('title', 'objective')}:{datetime.utcnow().timestamp()}",
        source_type="test",
        source_id="1",
        title="Handle commitment",
        goal="Reach and verify the requested outcome",
        category="test",
        priority="normal",
        risk_level="low",
        status="planned",
        context_json="{}",
        plan_json="{}",
    )
    defaults.update(kwargs)
    return VAObjective(**defaults)


@pytest.mark.asyncio
async def test_waiting_commitment_remains_va_owned_and_knows_follow_up(db):
    row = objective(status="waiting_external", title="Get an answer")
    db.add(row)
    await db.flush()
    step = VAObjectiveStep(
        objective_id=row.id,
        position=1,
        action_type="wait",
        idempotency_key=f"wait:{row.id}",
        status="waiting",
        verification_type="counterparty_response",
    )
    due = datetime.utcnow() + timedelta(hours=12)
    db.add(step)
    db.add(
        VAFollowUp(
            objective_id=row.id,
            channel="email",
            target="person@example.com",
            purpose="Get the promised answer",
            due_at=due,
            recurrence_hours=24,
            status="pending",
        )
    )
    await db.commit()

    commitment = await commitment_projection(db, row)
    assert commitment["owner"] == "va"
    assert commitment["stage"] == "waiting"
    assert commitment["waiting_on"] == "counterparty"
    assert "follow up automatically" in commitment["next_action"]
    assert commitment["next_check_at"] is not None
    assert commitment["verification_required"] == ["counterparty_response"]


@pytest.mark.asyncio
async def test_internal_failure_never_turns_into_user_ownership(db):
    row = objective(
        status="blocked_system",
        title="Recover provider",
        blocked_reason="Provider returned 500",
        priority="high",
    )
    db.add(row)
    await db.commit()

    commitment = await commitment_projection(db, row)
    assert commitment["owner"] == "va"
    assert commitment["waiting_on"] == "system"
    assert commitment["stage"] == "blocked_internal"
    assert "Recover" in commitment["next_action"]


@pytest.mark.asyncio
async def test_executive_queue_prioritizes_due_high_priority_work(db):
    low = objective(title="Someday", correlation_key="test:low", priority="low", due_at=datetime.utcnow() + timedelta(days=20))
    high = objective(title="Today", correlation_key="test:high", priority="high", due_at=datetime.utcnow() - timedelta(minutes=10))
    db.add_all([low, high])
    await db.commit()

    overview = await executive_commitment_overview(db)
    assert overview["counts"]["working"] == 2
    assert overview["working_now"][0]["id"] == high.id
    assert "Nothing needs your attention" in overview["summary"]


@pytest.mark.asyncio
async def test_commitment_graph_persists_dependency_edges(db):
    parent = objective(title="Parent", correlation_key="test:parent")
    db.add(parent)
    await db.flush()
    child = objective(
        title="Child",
        correlation_key="test:child",
        context_json=json.dumps({"depends_on_objective_id": parent.id}),
    )
    db.add(child)
    await db.commit()

    first = await reconcile_commitment_graph(db)
    second = await reconcile_commitment_graph(db)
    assert first["edges_created"] == 1
    assert second["edges_created"] == 0
    edge = (await db.execute(select(VACommitmentEdge))).scalar_one()
    assert edge.from_objective_id == child.id
    assert edge.to_objective_id == parent.id
    assert edge.relation == "depends_on"
