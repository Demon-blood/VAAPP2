from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.entities import AuditLog, WorkflowJob, WorkflowJobDependency, WorkflowRun
from app.services.workflow_engine import (
    _backoff_seconds,
    complete_job,
    create_workflow,
    enqueue_job,
    fail_job,
    lease_due_jobs,
    recover_expired_leases,
    requeue_dead_letter,
)


@pytest.fixture
async def workflow_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(WorkflowRun.__table__.create)
        await connection.run_sync(WorkflowJob.__table__.create)
        await connection.run_sync(WorkflowJobDependency.__table__.create)
        await connection.run_sync(AuditLog.__table__.create)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as db:
        yield db
    await engine.dispose()


def test_backoff_is_exponential_and_capped():
    assert _backoff_seconds(1) == 15
    assert _backoff_seconds(2) == 30
    assert _backoff_seconds(3) == 60
    assert _backoff_seconds(20) == 3600


@pytest.mark.asyncio
async def test_idempotency_and_dependency_gating(workflow_db):
    run, created = await create_workflow(
        workflow_db,
        workflow_type="bill_lifecycle",
        correlation_key="bill:42",
        intent={"bill_id": 42},
    )
    assert created is True
    same_run, created_again = await create_workflow(
        workflow_db,
        workflow_type="bill_lifecycle",
        correlation_key="bill:42",
    )
    assert created_again is False
    assert same_run.id == run.id

    first, created = await enqueue_job(
        workflow_db,
        workflow_run_id=run.id,
        job_type="test.first",
        idempotency_key="bill:42:first",
    )
    assert created is True
    duplicate, duplicate_created = await enqueue_job(
        workflow_db,
        workflow_run_id=run.id,
        job_type="test.first",
        idempotency_key="bill:42:first",
    )
    assert duplicate_created is False
    assert duplicate.id == first.id

    second, _ = await enqueue_job(
        workflow_db,
        workflow_run_id=run.id,
        job_type="test.second",
        idempotency_key="bill:42:second",
        dependency_ids=[first.id],
    )

    leased = await lease_due_jobs(workflow_db, worker_id="worker-a", limit=10)
    assert [job.id for job in leased] == [first.id]
    await complete_job(workflow_db, leased[0], worker_id="worker-a", result={"ok": True})

    leased = await lease_due_jobs(workflow_db, worker_id="worker-a", limit=10)
    assert [job.id for job in leased] == [second.id]
    await complete_job(workflow_db, leased[0], worker_id="worker-a")
    await workflow_db.refresh(run)
    assert run.status == "completed"


@pytest.mark.asyncio
async def test_retry_dead_letter_requeue_and_watchdog(workflow_db):
    job, _ = await enqueue_job(
        workflow_db,
        job_type="test.failure",
        idempotency_key="failure:1",
        max_attempts=2,
    )
    leased = await lease_due_jobs(workflow_db, worker_id="worker-a", limit=1)
    assert leased[0].id == job.id
    status = await fail_job(workflow_db, leased[0], worker_id="worker-a", error=RuntimeError("first"))
    assert status == "retry"

    job = await workflow_db.get(WorkflowJob, job.id)
    job.run_after = datetime.utcnow() - timedelta(seconds=1)
    await workflow_db.commit()
    leased = await lease_due_jobs(workflow_db, worker_id="worker-a", limit=1)
    status = await fail_job(workflow_db, leased[0], worker_id="worker-a", error=RuntimeError("second"))
    assert status == "dead_letter"

    replayed = await requeue_dead_letter(workflow_db, job.id)
    assert replayed.status == "retry"
    assert replayed.attempts == 0

    replayed.run_after = datetime.utcnow() - timedelta(seconds=1)
    await workflow_db.commit()
    leased = await lease_due_jobs(workflow_db, worker_id="crashed-worker", limit=1, lease_seconds=30)
    leased[0].lease_expires_at = datetime.utcnow() - timedelta(seconds=1)
    await workflow_db.commit()
    outcome = await recover_expired_leases(workflow_db)
    assert outcome == {"recovered": 1, "dead_lettered": 0}
    recovered = await workflow_db.get(WorkflowJob, job.id)
    assert recovered.status == "retry"
    assert recovered.lease_owner == ""
