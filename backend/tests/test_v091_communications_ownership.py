from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models.entities import (
    CommunicationAction,
    CommunicationDeliveryEvidence,
    CommunicationEvent,
    GmailOutboundMessage,
    VACommunicationThread,
    VAEvent,
    VAFollowUp,
    VAObjective,
    VAObjectiveStep,
)
from app.services.autonomous_core import objective_from_event, process_due_followups, record_event
from app.services.communication_ownership import mark_thread_waiting_for_counterparty
from app.services.communications_service import complete_communication_action


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_email_reply_event_becomes_durable_send_and_wait_steps(db):
    event, _ = await record_event(
        db,
        event_key="gmail-message:m1:reply-plan",
        source_type="email",
        source_id="m1",
        event_type="email_reply_planned",
        title="Reply: status request",
        payload={
            "thread_record_id": 0,
            "gmail_thread_id": "thread-1",
            "source_message_id": "m1",
            "source_rfc_message_id": "<source@example.invalid>",
            "recipient": "person@example.invalid",
            "subject": "Re: status",
            "body": "Thanks, I will handle this.",
            "priority": "normal",
            "expect_reply": True,
            "follow_up_hours": 48,
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
    assert [step.action_type for step in steps] == ["gmail_send_reply", "wait"]
    assert steps[0].verification_type == "gmail_outbound_verified"
    assert steps[1].verification_type == "counterparty_response"


@pytest.mark.asyncio
async def test_counterparty_response_cancels_chase_and_completes_waiting_objective(db):
    source, _ = await record_event(
        db,
        event_key="source-email",
        source_type="email",
        source_id="m1",
        event_type="email_reply_planned",
        title="Reply",
        payload={
            "gmail_thread_id": "thread-1",
            "source_message_id": "m1",
            "recipient": "person@example.invalid",
            "subject": "Re: status",
            "body": "Please confirm.",
            "expect_reply": True,
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
        purpose="Chase response",
        due_at=datetime.utcnow() + timedelta(hours=48),
        status="pending",
    )
    db.add(followup)
    await db.commit()

    response, _ = await record_event(
        db,
        event_key="gmail-thread:thread-1:response:m2",
        source_type="gmail_thread",
        source_id="thread-1",
        event_type="communication_response_received",
        title="Response received",
        payload={
            "prior_objective_id": objective.id,
            "thread_record_id": 0,
            "channel": "email",
            "provider": "gmail",
            "message_id": "m2",
        },
    )
    returned = await objective_from_event(db, response)
    assert returned.id == objective.id
    await db.refresh(objective)
    await db.refresh(followup)
    assert objective.status == "completed"
    assert followup.status == "cancelled"


@pytest.mark.asyncio
async def test_due_email_followup_extends_same_objective_without_fake_send(db):
    event, _ = await record_event(
        db,
        event_key="gmail-plan-for-followup",
        source_type="email",
        source_id="m1",
        event_type="email_reply_planned",
        title="Reply",
        payload={
            "gmail_thread_id": "thread-1",
            "source_message_id": "m1",
            "recipient": "person@example.invalid",
            "subject": "Re: status",
            "body": "Please confirm.",
            "expect_reply": True,
        },
    )
    objective = await objective_from_event(db, event)
    original_steps = list(
        (
            await db.execute(
                select(VAObjectiveStep)
                .where(VAObjectiveStep.objective_id == objective.id)
                .order_by(VAObjectiveStep.position)
            )
        ).scalars()
    )
    original_steps[0].status = "completed"
    original_steps[1].status = "waiting"
    objective.status = "waiting_external"
    row = VAFollowUp(
        objective_id=objective.id,
        channel="email",
        target="person@example.invalid",
        purpose="Chase response",
        payload_json='{"gmail_thread_id":"thread-1","source_message_id":"m1","subject":"Re: status","previous_body":"Please confirm."}',
        due_at=datetime.utcnow() - timedelta(minutes=1),
        recurrence_hours=48,
        max_attempts=4,
        status="pending",
    )
    db.add(row)
    await db.commit()

    assert await process_due_followups(db) == 1
    follow_event = (
        await db.execute(select(VAEvent).where(VAEvent.event_type == "followup_due"))
    ).scalar_one()
    returned = await objective_from_event(db, follow_event)
    assert returned.id == objective.id
    steps = list(
        (
            await db.execute(
                select(VAObjectiveStep)
                .where(VAObjectiveStep.objective_id == objective.id)
                .order_by(VAObjectiveStep.position)
            )
        ).scalars()
    )
    assert [step.action_type for step in steps] == ["gmail_send_reply", "wait", "gmail_send_followup", "wait"]
    await db.refresh(row)
    assert row.status == "dispatching"
    assert row.last_sent_at is None


@pytest.mark.asyncio
async def test_device_result_creates_real_dispatch_evidence_and_does_not_downgrade(db):
    event = CommunicationEvent(
        external_id="sms:test:1",
        channel="sms",
        provider="android_sms",
        sender="+32000000000",
        recipient="me",
        body="Hello",
        direction="incoming",
        status="processed",
    )
    db.add(event)
    await db.flush()
    action = CommunicationAction(
        event_id=event.id,
        action_type="reply",
        target="+32000000000",
        payload_json='{"text":"Hi","channel":"sms"}',
        idempotency_key="communication:test:reply",
        status="pending",
    )
    db.add(action)
    await db.commit()

    await complete_communication_action(db, action.id, status="sent", external_ref="android-sms:1")
    await complete_communication_action(db, action.id, status="delivery_failed", failure_reason="recipient unavailable")
    await db.refresh(action)
    assert action.status == "sent"
    evidence = list(
        (
            await db.execute(
                select(CommunicationDeliveryEvidence)
                .where(CommunicationDeliveryEvidence.communication_action_id == action.id)
            )
        ).scalars()
    )
    assert {row.evidence_type for row in evidence} == {"sms_sent", "sms_delivery_failed"}


@pytest.mark.asyncio
async def test_ambiguous_gmail_provider_outcome_is_reconciliation_only(db, monkeypatch):
    import app.services.gmail_delivery as delivery

    row = GmailOutboundMessage(
        idempotency_key="gmail:test:ambiguous",
        recipient="person@example.invalid",
        subject="Re: status",
        body="Following up.",
        rfc_message_id="<vaapp-test@example.invalid>",
        status="creation_uncertain",
        attempts=1,
        max_attempts=1,
        verify_after=datetime.utcnow() - timedelta(minutes=1),
    )
    db.add(row)
    await db.commit()

    async def not_found(*args, **kwargs):
        return False

    send_calls = 0

    async def should_not_send(*args, **kwargs):
        nonlocal send_calls
        send_calls += 1
        raise AssertionError("ambiguous Gmail intent must never be submitted twice")

    monkeypatch.setattr(delivery, "reconcile_gmail_outbound", not_found)
    monkeypatch.setattr(delivery, "send_gmail_message", should_not_send)
    returned = await delivery.send_or_reconcile_gmail_outbound(db, row)
    assert returned.status == "creation_uncertain"
    assert send_calls == 0
