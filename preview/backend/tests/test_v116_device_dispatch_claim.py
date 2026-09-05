from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models.entities import (
    AuditLog,
    CommunicationAction,
    CommunicationDeliveryEvidence,
    CommunicationDispatchClaim,
    CommunicationEvent,
    Device,
    VAObjective,
    VAObjectiveStep,
)
from app.services import autonomous_core
from app.services.communications_service import (
    claim_communication_action,
    complete_communication_action,
    pending_communication_actions,
)

NOW = datetime(2026, 9, 5, 16, 0, 0, tzinfo=UTC).replace(tzinfo=None)
LEGACY_UNCERTAINTY_ERROR = (
    "Android device did not report a definitive dispatch outcome; automatic resend is unsafe"
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


async def _device_case(
    db,
    *,
    action_status: str = "pending",
    step_status: str = "verifying",
    objective_status: str = "verifying",
    age: timedelta = timedelta(hours=2),
):
    token = f"{action_status}-{step_status}-{objective_status}-{int(age.total_seconds())}"
    event = CommunicationEvent(
        external_id=f"v116-event-{token}",
        channel="sms",
        provider="android_device",
        sender="Alice",
        recipient="+32000000000",
        body="See you later",
        direction="incoming",
        event_type="message",
    )
    db.add(event)
    await db.flush()

    action = CommunicationAction(
        event_id=event.id,
        action_type="reply",
        target="+32000000000",
        payload_json=json.dumps({"text": "Sounds good", "channel": "sms"}),
        idempotency_key=f"v116-action-{token}",
        status=action_status,
        created_at=NOW - age,
    )
    db.add(action)
    await db.flush()

    objective = VAObjective(
        correlation_key=f"v116-objective-{token}",
        source_type="communication",
        source_id=str(event.id),
        title="Reply to Alice",
        goal="Send one SMS reply and verify the device handoff",
        category="communication_reply",
        status=objective_status,
        blocked_reason="Device dispatch outcome is unknown" if objective_status == "blocked_system" else "",
    )
    db.add(objective)
    await db.flush()

    step = VAObjectiveStep(
        objective_id=objective.id,
        position=1,
        action_type="device_communication_action",
        idempotency_key=f"v116-step-{token}",
        parameters_json=json.dumps(
            {
                "communication_action_id": action.id,
                "channel": "sms",
                "expect_reply": False,
            }
        ),
        verification_type="device_action_verified",
        status=step_status,
        run_after=NOW - timedelta(minutes=1),
        created_at=NOW - age,
        last_error=LEGACY_UNCERTAINTY_ERROR if step_status == "failed" else "",
        finished_at=NOW - timedelta(minutes=5) if step_status == "failed" else None,
    )
    db.add(step)
    await db.commit()
    return event, action, objective, step


@pytest.mark.asyncio
async def test_backend_dispatch_claim_is_atomic_and_same_device_retry_is_idempotent(db):
    _event, action, _objective, _step = await _device_case(db)
    first_device = Device(name="phone-a", token_hash="a" * 64)
    second_device = Device(name="phone-b", token_hash="b" * 64)
    db.add_all([first_device, second_device])
    await db.commit()

    first, first_claimed = await claim_communication_action(db, action.id, device_id=first_device.id)
    same, same_claimed = await claim_communication_action(db, action.id, device_id=first_device.id)
    other, other_claimed = await claim_communication_action(db, action.id, device_id=second_device.id)

    assert first_claimed is True
    assert same_claimed is True
    assert other_claimed is False
    assert first.status == "dispatching"
    assert same.status == "dispatching"
    assert other.status == "dispatching"
    claims = list((await db.execute(select(CommunicationDispatchClaim))).scalars())
    assert len(claims) == 1
    assert claims[0].device_id == first_device.id
    feed = await pending_communication_actions(db)
    assert len(feed) == 1
    assert feed[0]["id"] == action.id
    assert feed[0]["status"] == "dispatching"
    assert feed[0]["can_background_dispatch"] is False
    assert feed[0]["can_resume_claimed_dispatch"] is True
    assert feed[0]["reconciliation_only"] is False

    audits = list(
        (
            await db.execute(
                select(AuditLog).where(AuditLog.event_type == "communication_action_dispatch_claimed")
            )
        ).scalars()
    )
    assert len(audits) == 1
    assert json.loads(audits[0].details_json)["device_id"] == first_device.id


@pytest.mark.asyncio
async def test_definitive_failure_releases_claim_but_ambiguous_multipart_does_not(db):
    _event, action, _objective, _step = await _device_case(db)
    first_device = Device(name="phone-c", token_hash="c" * 64)
    second_device = Device(name="phone-d", token_hash="d" * 64)
    db.add_all([first_device, second_device])
    await db.commit()

    _claimed_action, claimed = await claim_communication_action(
        db, action.id, device_id=first_device.id
    )
    assert claimed is True
    await complete_communication_action(
        db,
        action.id,
        status="creation_uncertain",
        failure_reason="One multipart SMS segment failed after dispatch began",
    )
    await db.refresh(action)
    assert action.status == "creation_uncertain"
    claims = list((await db.execute(select(CommunicationDispatchClaim))).scalars())
    assert len(claims) == 1
    feed = await pending_communication_actions(db)
    assert feed[0]["reconciliation_only"] is True
    assert feed[0]["can_resume_claimed_dispatch"] is False

    # A later provider-level success remains monotonic and proves the original action.
    await complete_communication_action(
        db, action.id, status="sent", external_ref=f"android-sms:{action.id}"
    )
    await db.refresh(action)
    assert action.status == "sent"

    # A separate definitive failed attempt releases its claim for the existing retry path.
    _event2, action2, _objective2, _step2 = await _device_case(db, age=timedelta(hours=4))
    _claimed2, claimed2 = await claim_communication_action(db, action2.id, device_id=first_device.id)
    assert claimed2 is True
    await complete_communication_action(db, action2.id, status="failed", failure_reason="No provider handoff")
    remaining = list(
        (
            await db.execute(
                select(CommunicationDispatchClaim).where(
                    CommunicationDispatchClaim.communication_action_id == action2.id
                )
            )
        ).scalars()
    )
    assert remaining == []
    action2.status = "pending"
    await db.commit()
    _retried, retry_claimed = await claim_communication_action(
        db, action2.id, device_id=second_device.id
    )
    assert retry_claimed is True


@pytest.mark.asyncio
async def test_old_pending_action_remains_va_owned_beyond_thirty_minutes(db, monkeypatch):
    _event, action, objective, step = await _device_case(db, age=timedelta(hours=3))
    monkeypatch.setattr(autonomous_core, "utcnow", lambda: NOW)

    checked = await autonomous_core.verify_ready_steps(db)

    assert checked == 1
    assert action.status == "pending"
    assert step.status == "verifying"
    assert step.finished_at is None
    assert step.last_error == "Waiting for the paired Android device to claim and report this action"
    assert step.run_after == NOW + timedelta(minutes=2)
    assert objective.status == "verifying"
    assert objective.blocked_reason == ""


@pytest.mark.asyncio
async def test_dispatching_action_waits_for_late_device_evidence_without_replay(db, monkeypatch):
    _event, action, objective, step = await _device_case(db, action_status="dispatching")
    monkeypatch.setattr(autonomous_core, "utcnow", lambda: NOW)

    checked = await autonomous_core.verify_ready_steps(db)

    assert checked == 1
    assert action.status == "dispatching"
    assert step.status == "verifying"
    assert step.last_error == (
        "Device dispatch was claimed; waiting for durable carrier or RemoteInput evidence"
    )
    assert objective.status == "verifying"


@pytest.mark.asyncio
async def test_late_sent_evidence_reopens_historical_step_and_completes_same_action(db, monkeypatch):
    _event, action, objective, step = await _device_case(
        db,
        step_status="failed",
        objective_status="blocked_system",
    )
    monkeypatch.setattr(autonomous_core, "utcnow", lambda: NOW)

    await complete_communication_action(
        db,
        action.id,
        status="sent",
        external_ref=f"android-sms:{action.id}",
        details={"reconciled_from_device": True},
    )

    checked = await autonomous_core.verify_ready_steps(db)

    assert checked == 1
    assert action.status == "sent"
    assert step.status == "completed"
    assert step.finished_at == NOW
    assert step.last_error == ""
    assert objective.status == "completed"
    evidence = list(
        (
            await db.execute(
                select(CommunicationDeliveryEvidence).where(
                    CommunicationDeliveryEvidence.communication_action_id == action.id
                )
            )
        ).scalars()
    )
    assert {row.evidence_type for row in evidence} == {"sms_sent"}
    audits = list(
        (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.event_type == "device_communication_legacy_uncertainty_reopened"
                )
            )
        ).scalars()
    )
    assert len(audits) == 1


@pytest.mark.asyncio
async def test_definitive_device_failure_is_not_reopened(db):
    _event, action, objective, step = await _device_case(
        db,
        action_status="failed",
        step_status="failed",
        objective_status="blocked_system",
    )
    step.last_error = "Carrier rejected the SMS"
    action.failure_reason = "Carrier rejected the SMS"
    await db.commit()

    recovered = await autonomous_core._recover_legacy_device_communication_uncertainty(db, NOW)

    assert recovered == 0
    assert action.status == "failed"
    assert step.status == "failed"
    assert objective.status == "blocked_system"
