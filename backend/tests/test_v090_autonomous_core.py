from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models.entities import (
    OAuthConnection,
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
from app.services.autonomous_core import (
    objective_from_event,
    process_due_followups,
    record_event,
    reconcile_source_objectives,
    recover_resolved_user_blockers,
    run_core_cycle,
    seed_system_events,
    verify_ready_steps,
)


@pytest.fixture
async def core_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        yield db
    await engine.dispose()


@pytest.mark.asyncio
async def test_event_and_objective_creation_are_idempotent(core_db):
    event, created = await record_event(
        core_db,
        event_key="test:event:1",
        source_type="test",
        source_id="1",
        event_type="manual_run_requested",
        title="Run VA",
        payload={},
    )
    same, created_again = await record_event(
        core_db,
        event_key="test:event:1",
        source_type="test",
        source_id="1",
        event_type="manual_run_requested",
        title="Run VA again",
        payload={},
    )
    assert created is True
    assert created_again is False
    assert same.id == event.id

    objective = await objective_from_event(core_db, event)
    same_objective = await objective_from_event(core_db, event)
    assert same_objective.id == objective.id
    steps = list(
        (
            await core_db.execute(
                select(VAObjectiveStep).where(VAObjectiveStep.objective_id == objective.id)
            )
        ).scalars()
    )
    assert len(steps) == 1
    assert steps[0].idempotency_key == f"va-objective:{objective.id}:step:1:workflow_intent"
    assert steps[0].verification_type == "workflow_run_completed"


@pytest.mark.asyncio
async def test_manual_run_dispatches_existing_durable_workflow_and_verifies_completion(core_db):
    result = await run_core_cycle(core_db, create_manual_run=True)
    assert result["steps_executed"] == 1

    objective = (
        await core_db.execute(
            select(VAObjective).where(VAObjective.source_type == "manual").order_by(VAObjective.id.desc())
        )
    ).scalars().first()
    assert objective is not None
    assert objective.status == "verifying"
    step = (
        await core_db.execute(
            select(VAObjectiveStep).where(VAObjectiveStep.objective_id == objective.id)
        )
    ).scalar_one()
    assert step.workflow_run_id is not None

    run = await core_db.get(WorkflowRun, step.workflow_run_id)
    assert run is not None
    assert run.workflow_type == "run_va"
    jobs = list(
        (
            await core_db.execute(select(WorkflowJob).where(WorkflowJob.workflow_run_id == run.id))
        ).scalars()
    )
    assert len(jobs) == 6
    for job in jobs:
        job.status = "completed"
        job.finished_at = datetime.utcnow()
    run.status = "completed"
    run.finished_at = datetime.utcnow()
    step.run_after = datetime.utcnow() - timedelta(seconds=1)
    await core_db.commit()

    assert await verify_ready_steps(core_db) == 1
    await core_db.refresh(objective)
    await core_db.refresh(step)
    assert step.status == "completed"
    assert objective.status == "completed"
    evidence = list(
        (
            await core_db.execute(
                select(VAOutcomeEvidence).where(VAOutcomeEvidence.objective_id == objective.id)
            )
        ).scalars()
    )
    assert len(evidence) == 1
    assert evidence[0].evidence_type == "workflow_run_terminal"


@pytest.mark.asyncio
async def test_needs_user_is_reserved_for_real_payment_authorization_and_auto_reconciles(core_db):
    payment = Payment(
        bill_id=1,
        amount=Decimal("42.50"),
        currency="EUR",
        status="authorization_required",
        requires_user_action=True,
        authorization_url="https://bank.example/authorize",
    )
    core_db.add(payment)
    await core_db.commit()

    seeded = await seed_system_events(core_db)
    assert seeded["payments"] == 1
    event = (
        await core_db.execute(select(VAEvent).where(VAEvent.source_type == "payment"))
    ).scalar_one()
    objective = await objective_from_event(core_db, event)
    assert objective.status == "needs_user"
    assert "authentication" in objective.needs_user_reason.lower()

    payment.requires_user_action = False
    payment.status = "completed"
    payment.external_payment_id = "provider-payment-42"
    await core_db.commit()
    result = await reconcile_source_objectives(core_db)
    assert result["completed"] == 1
    await core_db.refresh(objective)
    assert objective.status == "completed"
    assert objective.needs_user_reason == ""


@pytest.mark.asyncio
async def test_unmigrated_routine_task_is_va_owned_not_wrongly_put_in_needs_user(core_db):
    task = Task(
        title="Routine follow-up",
        description="Follow up with the supplier",
        source_type="support_followup",
        source_id="case-1",
        status="open",
        requires_approval=False,
    )
    core_db.add(task)
    await core_db.commit()
    await seed_system_events(core_db)
    event = (
        await core_db.execute(select(VAEvent).where(VAEvent.source_type == "task"))
    ).scalar_one()
    objective = await objective_from_event(core_db, event)
    assert objective.status == "blocked_capability"
    assert objective.needs_user_reason == ""
    assert "durably owned" in objective.blocked_reason


@pytest.mark.asyncio
async def test_dead_letter_classification_separates_auth_blockers_from_system_failures(core_db):
    auth_job = WorkflowJob(
        job_type="gmail.sync",
        payload_json="{}",
        idempotency_key="dead:auth",
        status="dead_letter",
        priority=20,
        attempts=8,
        max_attempts=8,
        run_after=datetime.utcnow(),
        last_error="HttpError 401 Unauthorized",
    )
    system_job = WorkflowJob(
        job_type="gmail.sync",
        payload_json="{}",
        idempotency_key="dead:unknown",
        status="dead_letter",
        priority=20,
        attempts=8,
        max_attempts=8,
        run_after=datetime.utcnow(),
        last_error="unexpected parser invariant failure",
    )
    core_db.add_all([auth_job, system_job])
    await core_db.commit()
    seeded = await seed_system_events(core_db)
    assert seeded["workflow_blockers"] == 1
    assert seeded["system_failures"] == 1

    events = list(
        (
            await core_db.execute(
                select(VAEvent).where(VAEvent.source_type == "workflow_job").order_by(VAEvent.id)
            )
        ).scalars()
    )
    objectives = [await objective_from_event(core_db, event) for event in events]
    by_source = {int(row.source_id): row for row in objectives}
    assert by_source[auth_job.id].status == "needs_user"
    assert by_source[system_job.id].status == "blocked_system"
    assert by_source[system_job.id].needs_user_reason == ""


@pytest.mark.asyncio
async def test_due_followup_is_persisted_as_work_without_faking_channel_delivery(core_db):
    event, _ = await record_event(
        core_db,
        event_key="followup-parent",
        source_type="test",
        source_id="parent",
        event_type="manual_run_requested",
        title="Parent",
        payload={},
    )
    objective = await objective_from_event(core_db, event)
    row = VAFollowUp(
        objective_id=objective.id,
        channel="email",
        target="recipient@example.invalid",
        purpose="Chase the outstanding reply",
        payload_json="{}",
        due_at=datetime.utcnow() - timedelta(minutes=1),
        recurrence_hours=48,
        max_attempts=4,
        status="pending",
    )
    core_db.add(row)
    await core_db.commit()

    assert await process_due_followups(core_db) == 1
    await core_db.refresh(row)
    assert row.status == "due"
    assert row.attempts == 1
    assert row.last_sent_at is None
    follow_event = (
        await core_db.execute(select(VAEvent).where(VAEvent.source_type == "follow_up"))
    ).scalar_one()
    assert follow_event.status == "new"
    assert follow_event.event_type == "followup_due"


@pytest.mark.asyncio
async def test_user_auth_dead_letter_resumes_automatically_after_real_connection_returns(core_db):
    job = WorkflowJob(
        job_type="gmail.sync",
        payload_json="{}",
        idempotency_key="dead:reauth",
        status="dead_letter",
        priority=20,
        attempts=8,
        max_attempts=8,
        run_after=datetime.utcnow(),
        last_error="HttpError 401 Unauthorized - OAuth connection requires reauthorization",
    )
    core_db.add(job)
    await core_db.commit()
    await seed_system_events(core_db)
    event = (
        await core_db.execute(
            select(VAEvent).where(VAEvent.source_type == "workflow_job", VAEvent.source_id == str(job.id))
        )
    ).scalar_one()
    objective = await objective_from_event(core_db, event)
    assert objective.status == "needs_user"

    core_db.add(
        OAuthConnection(
            provider="google",
            account_key="user@example.invalid",
            display_name="Test Google",
            access_token_encrypted="test-token",
            refresh_token_encrypted=None,
            scope="gmail.readonly gmail.modify",
            enabled=True,
        )
    )
    await core_db.commit()

    assert await recover_resolved_user_blockers(core_db) == 1
    await core_db.refresh(job)
    await core_db.refresh(objective)
    assert job.status == "retry"
    assert objective.status == "waiting_external"
    assert objective.needs_user_reason == ""


def test_v090_release_and_routes_contract():
    root = Path(__file__).parents[2]
    version = (root / "backend/app/core/version.py").read_text()
    pubspec = (root / "android/pubspec.yaml").read_text()
    routes = (root / "backend/app/api/routes.py").read_text()
    scheduler = (root / "backend/app/services/scheduler.py").read_text()
    workflow = (root / "backend/app/services/workflow_engine.py").read_text()
    assert 'APP_VERSION = "1.0.12"' in version
    assert "version: 1.0.12+55" in pubspec
    for path in (
        "/api/va/overview",
        "/api/va/capabilities",
        "/api/va/objectives",
        "/api/va/objectives/{objective_id}",
        "/api/va/objectives/{objective_id}/recheck",
        "/api/va/run",
    ):
        assert path in routes
    assert 'job_type="va.core.cycle"' in scheduler
    assert '@job_handler("va.core.cycle")' in workflow


def test_phase1_has_no_fake_or_simulated_executor_paths():
    root = Path(__file__).parents[2]
    for relative in (
        "backend/app/services/autonomous_core.py",
        "backend/app/services/capability_registry.py",
        "backend/app/services/va_policy.py",
    ):
        lowered = (root / relative).read_text().lower()
        assert "paper_mode" not in lowered
        assert "simulation_mode" not in lowered
        assert "fake_success" not in lowered
