from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models.entities import (
    AuditLog,
    BrowserOperation,
    BrowserPortal,
    DocumentObligation,
    FormSubmission,
    VAObjective,
    VAObjectiveStep,
)
from app.services import autonomous_core, browser_operator, document_ownership

NOW = datetime(2026, 9, 5, 12, 0, 0, tzinfo=UTC).replace(tzinfo=None)


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _browser_case(
    db,
    *,
    step_status: str,
    objective_status: str,
    with_marker: bool = True,
):
    portal = BrowserPortal(
        slug=f"v115-{step_status}-{objective_status}-{int(with_marker)}",
        name="v1.0.15 test portal",
        base_url="https://example.com",
        allowed_hosts_json='["example.com"]',
    )
    db.add(portal)
    await db.flush()

    objective = VAObjective(
        correlation_key=f"v115-objective-{step_status}-{objective_status}-{int(with_marker)}",
        source_type="document_form",
        source_id="v115",
        title="Submit provider form",
        goal="Submit exactly once and verify the provider postcondition",
        category="browser_portal",
        status=objective_status,
        blocked_reason="Provider outcome was ambiguous" if objective_status == "blocked_system" else "",
    )
    db.add(objective)
    await db.flush()

    step = VAObjectiveStep(
        objective_id=objective.id,
        position=1,
        action_type="browser_operation",
        idempotency_key=f"v115-step-{step_status}-{objective_status}-{int(with_marker)}",
        status=step_status,
        parameters_json="{}",
        verification_type="browser_operation_verified",
        run_after=NOW - timedelta(minutes=1),
        last_error="Provider outcome was ambiguous" if step_status == "failed" else "",
        finished_at=NOW - timedelta(minutes=5) if step_status == "failed" else None,
    )
    db.add(step)
    await db.flush()

    operation = BrowserOperation(
        idempotency_key=f"v115-operation-{step_status}-{objective_status}-{int(with_marker)}",
        portal_id=portal.id,
        objective_id=objective.id,
        step_id=step.id,
        title="Submit provider form",
        plan_json='{"steps":[]}',
        verification_json='{"text_contains":"submitted"}',
        status="creation_uncertain",
        current_step=1,
        side_effect_step=1 if with_marker else None,
        side_effect_started_at=NOW - timedelta(minutes=5) if with_marker else None,
        verify_after=NOW - timedelta(minutes=1),
        last_error="Provider response was lost after submit",
    )
    db.add(operation)
    await db.flush()
    step.parameters_json = json.dumps({"browser_operation_id": operation.id})
    await db.commit()
    return objective, step, operation, portal


def _resume_same_operation(operation: BrowserOperation):
    async def fake_resume(_db, operation_id: int):
        assert operation_id == operation.id
        operation.resume_sequence += 1
        operation.status = "pending"
        operation.verify_after = NOW
        operation.last_error = ""
        return operation

    return fake_resume


@pytest.mark.asyncio
async def test_legacy_failed_browser_step_reopens_same_operation_for_reconciliation(db, monkeypatch):
    objective, step, operation, _portal = await _browser_case(
        db,
        step_status="failed",
        objective_status="blocked_system",
    )
    monkeypatch.setattr(browser_operator, "resume_browser_operation", _resume_same_operation(operation))

    recovered = await autonomous_core._recover_legacy_browser_uncertainty(db, NOW)

    assert recovered == 1
    assert operation.status == "pending"
    assert operation.resume_sequence == 1
    assert step.status == "verifying"
    assert step.finished_at is None
    assert step.run_after == NOW + timedelta(seconds=10)
    assert objective.status == "verifying"
    assert objective.blocked_reason == ""
    audits = list(
        (
            await db.execute(
                select(AuditLog).where(AuditLog.event_type == "browser_operation_legacy_uncertainty_reopened")
            )
        ).scalars()
    )
    assert len(audits) == 1
    details = json.loads(audits[0].details_json)
    assert details["automatic_replay"] is False


@pytest.mark.asyncio
async def test_live_verifier_keeps_creation_uncertainty_under_reconciliation(db, monkeypatch):
    objective, step, operation, _portal = await _browser_case(
        db,
        step_status="verifying",
        objective_status="verifying",
    )
    monkeypatch.setattr(autonomous_core, "utcnow", lambda: NOW)
    monkeypatch.setattr(browser_operator, "resume_browser_operation", _resume_same_operation(operation))

    checked = await autonomous_core.verify_ready_steps(db)

    assert checked == 1
    assert operation.status == "pending"
    assert operation.resume_sequence == 1
    assert step.status == "verifying"
    assert step.finished_at is None
    assert objective.status == "verifying"
    assert objective.needs_user_reason == ""


@pytest.mark.asyncio
async def test_markerless_uncertainty_is_not_reopened_or_replayed(db, monkeypatch):
    objective, step, operation, _portal = await _browser_case(
        db,
        step_status="failed",
        objective_status="blocked_system",
        with_marker=False,
    )

    async def forbidden_resume(*_args, **_kwargs):
        raise AssertionError("markerless uncertainty must fail closed")

    monkeypatch.setattr(browser_operator, "resume_browser_operation", forbidden_resume)

    recovered = await autonomous_core._recover_legacy_browser_uncertainty(db, NOW)

    assert recovered == 0
    assert operation.status == "creation_uncertain"
    assert operation.resume_sequence == 0
    assert step.status == "failed"
    assert step.finished_at is not None
    assert objective.status == "blocked_system"


@pytest.mark.asyncio
async def test_document_form_projection_stays_in_progress_during_browser_reconciliation(db):
    objective, _step, operation, portal = await _browser_case(
        db,
        step_status="verifying",
        objective_status="verifying",
    )
    obligation = DocumentObligation(
        correlation_key="v115-document-obligation",
        title="Provider application",
        obligation_type="form",
        status="blocked_system",
        portal_id=portal.id,
        objective_id=objective.id,
        browser_operation_id=operation.id,
        last_error="Provider response was lost after submit",
    )
    db.add(obligation)
    await db.flush()
    submission = FormSubmission(
        idempotency_key="v115-form-submission",
        obligation_id=obligation.id,
        portal_id=portal.id,
        browser_operation_id=operation.id,
        status="blocked_system",
        last_error="Provider response was lost after submit",
    )
    db.add(submission)
    await db.flush()
    obligation.form_submission_id = submission.id
    await db.commit()

    result = await document_ownership._sync_obligation_statuses(db)

    assert result["blocked"] == 0
    assert result["in_progress"] == 1
    assert obligation.status == "in_progress"
    assert submission.status == "in_progress"
    assert "lost after submit" in obligation.last_error


@pytest.mark.asyncio
async def test_document_form_projection_keeps_markerless_uncertainty_system_blocked(db):
    objective, _step, operation, portal = await _browser_case(
        db,
        step_status="failed",
        objective_status="blocked_system",
        with_marker=False,
    )
    obligation = DocumentObligation(
        correlation_key="v115-markerless-document-obligation",
        title="Provider application",
        obligation_type="form",
        status="blocked_system",
        portal_id=portal.id,
        objective_id=objective.id,
        browser_operation_id=operation.id,
        last_error="Provider response was lost after submit",
    )
    db.add(obligation)
    await db.flush()
    submission = FormSubmission(
        idempotency_key="v115-markerless-form-submission",
        obligation_id=obligation.id,
        portal_id=portal.id,
        browser_operation_id=operation.id,
        status="blocked_system",
        last_error="Provider response was lost after submit",
    )
    db.add(submission)
    await db.flush()
    obligation.form_submission_id = submission.id
    await db.commit()

    result = await document_ownership._sync_obligation_statuses(db)

    assert result["blocked"] == 1
    assert result["in_progress"] == 0
    assert obligation.status == "blocked_system"
    assert submission.status == "blocked_system"
