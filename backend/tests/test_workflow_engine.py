from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.entities import AuditLog, WorkflowJob, WorkflowJobDependency, WorkflowRun
from app.services.workflow_engine import (
    _backoff_seconds,
    compact_duplicate_dead_letters,
    complete_job,
    create_workflow,
    enqueue_job,
    fail_job,
    failure_signature,
    lease_due_jobs,
    recover_autopilot_exceptions,
    recover_expired_leases,
    repair_v052_gmail_conflict_backlog,
    repair_v062_gmail_label_conflict_backlog,
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


def test_failure_signature_collapses_http_conflicts():
    first = failure_signature(
        "gmail.sync",
        '<HttpError 409 when requesting https://gmail.googleapis.com/gmail/v1/users/me/labels returned "Conflict">',
    )
    second = failure_signature(
        "gmail.sync",
        '<HttpError 409 when requesting https://gmail.googleapis.com/gmail/v1/users/me/messages/abc returned "Aborted">',
    )
    assert first == "gmail.sync:http:409"
    assert second == first
    assert failure_signature("gmail.sync", "HttpError 401") == "gmail.sync:http:401"


@pytest.mark.asyncio
async def test_duplicate_dead_letters_are_compacted_and_bulk_recovery_requeues_one(workflow_db):
    now = datetime.utcnow()
    first = WorkflowJob(
        job_type="gmail.sync",
        payload_json="{}",
        idempotency_key="gmail:dead:1",
        status="dead_letter",
        priority=20,
        attempts=8,
        max_attempts=8,
        run_after=now,
        lease_owner="",
        result_json="{}",
        last_error='<HttpError 409 when requesting https://gmail.googleapis.com/a returned "Conflict">',
        finished_at=now,
    )
    second = WorkflowJob(
        job_type="gmail.sync",
        payload_json="{}",
        idempotency_key="gmail:dead:2",
        status="dead_letter",
        priority=20,
        attempts=8,
        max_attempts=8,
        run_after=now,
        lease_owner="",
        result_json="{}",
        last_error='<HttpError 409 when requesting https://gmail.googleapis.com/b returned "Conflict">',
        finished_at=now,
    )
    workflow_db.add_all([first, second])
    await workflow_db.commit()

    compacted = await compact_duplicate_dead_letters(workflow_db)
    assert compacted["superseded"] == 1

    statuses = sorted(
        list(
            (
                await workflow_db.execute(
                    select(WorkflowJob.status).where(
                        WorkflowJob.id.in_([first.id, second.id])
                    )
                )
            ).scalars()
        )
    )
    assert statuses == ["dead_letter", "superseded"]

    recovered = await recover_autopilot_exceptions(workflow_db)
    assert recovered["requeued"] == 1
    statuses = sorted(
        list(
            (
                await workflow_db.execute(
                    select(WorkflowJob.status).where(
                        WorkflowJob.id.in_([first.id, second.id])
                    )
                )
            ).scalars()
        )
    )
    assert statuses == ["retry", "superseded"]


@pytest.mark.asyncio
async def test_v052_gmail_409_backlog_repair_runs_only_once(workflow_db):
    now = datetime.utcnow()
    old_retry = WorkflowJob(
        job_type="gmail.sync",
        payload_json="{}",
        idempotency_key="gmail:legacy:retry",
        status="retry",
        priority=20,
        attempts=2,
        max_attempts=8,
        run_after=now,
        lease_owner="",
        result_json="{}",
        last_error="<HttpError 409 old conflict>",
    )
    old_dead = WorkflowJob(
        job_type="gmail.sync",
        payload_json="{}",
        idempotency_key="gmail:legacy:dead",
        status="dead_letter",
        priority=20,
        attempts=8,
        max_attempts=8,
        run_after=now,
        lease_owner="",
        result_json="{}",
        last_error="<HttpError 409 old conflict>",
        finished_at=now,
    )
    workflow_db.add_all([old_retry, old_dead])
    await workflow_db.commit()

    outcome = await repair_v052_gmail_conflict_backlog(workflow_db)
    assert outcome == {"superseded": 2, "already_repaired": 0}
    await workflow_db.refresh(old_retry)
    await workflow_db.refresh(old_dead)
    assert old_retry.status == "superseded"
    assert old_dead.status == "superseded"

    new_retry = WorkflowJob(
        job_type="gmail.sync",
        payload_json="{}",
        idempotency_key="gmail:new:retry",
        status="retry",
        priority=20,
        attempts=1,
        max_attempts=8,
        run_after=now,
        lease_owner="",
        result_json="{}",
        last_error="<HttpError 409 new conflict>",
    )
    workflow_db.add(new_retry)
    await workflow_db.commit()

    second = await repair_v052_gmail_conflict_backlog(workflow_db)
    assert second == {"superseded": 0, "already_repaired": 1}
    await workflow_db.refresh(new_retry)
    assert new_retry.status == "retry"




@pytest.mark.asyncio
async def test_v062_gmail_label_conflict_backlog_repair_runs_only_once(workflow_db):
    now = datetime.utcnow()
    retry = WorkflowJob(
        job_type="gmail.sync",
        payload_json="{}",
        idempotency_key="gmail:v061:label-conflict:retry",
        status="retry",
        priority=20,
        attempts=3,
        max_attempts=8,
        run_after=now,
        lease_owner="",
        result_json="{}",
        last_error='<HttpError 409 returned "Label name exists or conflicts">',
    )
    unrelated = WorkflowJob(
        job_type="gmail.sync",
        payload_json="{}",
        idempotency_key="gmail:v061:other-409",
        status="retry",
        priority=20,
        attempts=1,
        max_attempts=8,
        run_after=now,
        lease_owner="",
        result_json="{}",
        last_error='<HttpError 409 returned "Concurrent message mutation">',
    )
    workflow_db.add_all([retry, unrelated])
    await workflow_db.commit()

    first = await repair_v062_gmail_label_conflict_backlog(workflow_db)
    assert first == {"superseded": 1, "already_repaired": 0}
    await workflow_db.refresh(retry)
    await workflow_db.refresh(unrelated)
    assert retry.status == "superseded"
    assert unrelated.status == "retry"

    second = await repair_v062_gmail_label_conflict_backlog(workflow_db)
    assert second == {"superseded": 0, "already_repaired": 1}

def test_failure_recovery_class_distinguishes_transient_from_human_auth():
    from app.services.workflow_engine import failure_recovery_class

    assert failure_recovery_class("gmail.sync", "HttpError 429 Too Many Requests") == "transient"
    assert failure_recovery_class("banking.autopilot", "503 Service Unavailable") == "transient"
    assert failure_recovery_class("gmail.sync", "HttpError 401 Unauthorized") == "user_required"
    assert failure_recovery_class("banking.autopilot", "Itsme SCA authorization required") == "user_required"
    assert failure_recovery_class("connectors.rules.run", "Unexpected invalid mapping") == "unknown"


@pytest.mark.asyncio
async def test_automatic_recovery_requeues_only_transient_dead_letters(workflow_db):
    from app.services.workflow_engine import auto_recover_transient_failures

    old = datetime.utcnow() - timedelta(minutes=30)
    transient = WorkflowJob(
        job_type="gmail.sync",
        payload_json="{}",
        idempotency_key="recover:transient",
        status="dead_letter",
        priority=20,
        attempts=8,
        max_attempts=8,
        run_after=old,
        result_json="{}",
        last_error="HttpError 429 Too Many Requests",
        finished_at=old,
        updated_at=old,
    )
    auth = WorkflowJob(
        job_type="banking.autopilot",
        payload_json="{}",
        idempotency_key="recover:auth",
        status="dead_letter",
        priority=20,
        attempts=8,
        max_attempts=8,
        run_after=old,
        result_json="{}",
        last_error="HttpError 401 Unauthorized - bank authorization required",
        finished_at=old,
        updated_at=old,
    )
    workflow_db.add_all([transient, auth])
    await workflow_db.commit()

    outcome = await auto_recover_transient_failures(workflow_db, cooldown_minutes=15)
    assert outcome["recovered"] == 1
    assert outcome["skipped_user_required"] == 1
    await workflow_db.refresh(transient)
    await workflow_db.refresh(auth)
    assert transient.status == "retry"
    assert transient.attempts == 0
    assert auth.status == "dead_letter"
