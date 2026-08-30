from __future__ import annotations

import json
from datetime import datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models.entities import AuditLog, CommunicationEvent, VAObjective
from app.services.specific_authorization import apply_standing_authority_objectives
from app.services.standing_authority import (
    evaluate_standing_authority,
    list_standing_authorities,
    record_standing_authority_use,
    set_standing_authority,
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


@pytest.mark.asyncio
async def test_standing_authority_is_explicit_and_disabled_by_default(db):
    rows = await list_standing_authorities(db)
    assert {row["key"] for row in rows} == {
        "routine_communications",
        "browser_transactions",
    }
    assert all(row["enabled"] is False for row in rows)

    decision = await evaluate_standing_authority(
        db,
        action_type="send_email_reply",
        risk_level="low",
        proposal={"counterparty": "person@example.com", "proposed_reply": "Thanks, that works for me."},
    )
    assert decision["allowed"] is False
    assert decision["policy_key"] == "routine_communications"


@pytest.mark.asyncio
async def test_routine_communication_authority_is_bounded_and_audited(db):
    row = await set_standing_authority(
        db,
        "routine_communications",
        {"enabled": True, "max_actions_per_day": 1},
    )
    assert row["enabled"] is True
    assert row["max_actions_per_day"] == 1

    proposal = {
        "counterparty": "person@example.com",
        "summary": "Reply to routine scheduling message",
        "proposed_reply": "Tuesday at 10 works. Thank you.",
    }
    first = await evaluate_standing_authority(
        db,
        action_type="send_email_reply",
        risk_level="high",
        proposal=proposal,
    )
    assert first["allowed"] is True
    await record_standing_authority_use(
        db,
        decision=first,
        action_type="send_email_reply",
        proposal=proposal,
        entity_type="test",
        entity_id="1",
    )
    await db.commit()

    second = await evaluate_standing_authority(
        db,
        action_type="send_email_reply",
        risk_level="low",
        proposal=proposal,
    )
    assert second["allowed"] is False
    assert "daily action limit" in second["reason"]
    audit = (
        await db.execute(select(AuditLog).where(AuditLog.event_type == "va_standing_authority_used"))
    ).scalar_one()
    audit_details = json.loads(audit.details_json)
    assert audit_details["policy_key"] == "routine_communications"
    assert "counterparty" not in audit_details
    assert audit_details["counterparty_scoped"] is True


@pytest.mark.asyncio
async def test_human_boundaries_cannot_be_waived_by_standing_authority(db):
    await set_standing_authority(db, "routine_communications", {"enabled": True})

    for proposal in (
        {"summary": "Send the verification code", "proposed_reply": "My OTP is 123456"},
        {"summary": "Accept contract", "proposed_reply": "I agree to terms and sign contract"},
        {"summary": "Identity verification", "source_excerpt": "Upload passport and ID card"},
    ):
        decision = await evaluate_standing_authority(
            db,
            action_type="send_email_reply",
            risk_level="high",
            proposal=proposal,
        )
        assert decision["allowed"] is False
        assert decision["hard_boundary"] is True

    external = await evaluate_standing_authority(
        db,
        action_type="bank_authorization",
        risk_level="high",
        proposal={"summary": "Authorize bank payment"},
    )
    assert external["allowed"] is False
    assert external["hard_boundary"] is True


@pytest.mark.asyncio
async def test_toggling_authority_preserves_existing_bounds(db):
    await set_standing_authority(
        db,
        "browser_transactions",
        {
            "enabled": True,
            "max_risk": "medium",
            "max_actions_per_day": 2,
            "max_amount_eur": "35.00",
            "counterparties": ["shop.example"],
        },
    )
    disabled = await set_standing_authority(db, "browser_transactions", {"enabled": False})
    assert disabled["max_risk"] == "medium"
    assert disabled["max_actions_per_day"] == 2
    assert disabled["max_amount_eur"] == "35.00"
    assert disabled["counterparties"] == ["shop.example"]

    enabled = await set_standing_authority(db, "browser_transactions", {"enabled": True})
    assert enabled["max_risk"] == "medium"
    assert enabled["max_actions_per_day"] == 2
    assert enabled["max_amount_eur"] == "35.00"
    assert enabled["counterparties"] == ["shop.example"]


@pytest.mark.asyncio
async def test_protected_communication_cannot_be_delegated(db):
    await set_standing_authority(db, "routine_communications", {"enabled": True})
    decision = await evaluate_standing_authority(
        db,
        action_type="send_message_reply",
        risk_level="high",
        proposal={
            "summary": "Reply to protected message",
            "proposed_reply": "Confirmed.",
            "protected": True,
        },
    )
    assert decision["allowed"] is False
    assert decision["hard_boundary"] is True


@pytest.mark.asyncio
async def test_browser_transactions_require_known_amount_and_respect_cap(db):
    await set_standing_authority(
        db,
        "browser_transactions",
        {"enabled": True, "max_amount_eur": "50.00", "max_actions_per_day": 5},
    )

    allowed = await evaluate_standing_authority(
        db,
        action_type="browser_operation",
        risk_level="high",
        proposal={"summary": "Purchase replacement charger EUR 35.00", "provider": "shop.example"},
    )
    assert allowed["allowed"] is True
    assert allowed["amount_eur"] == "35.00"

    too_much = await evaluate_standing_authority(
        db,
        action_type="browser_operation",
        risk_level="high",
        proposal={"summary": "Purchase replacement charger EUR 75.00", "provider": "shop.example"},
    )
    assert too_much["allowed"] is False
    assert "exceeds" in too_much["reason"]

    unknown = await evaluate_standing_authority(
        db,
        action_type="browser_operation",
        risk_level="high",
        proposal={"summary": "Purchase replacement charger", "provider": "shop.example"},
    )
    assert unknown["allowed"] is False
    assert "amount is known" in unknown["reason"]


@pytest.mark.asyncio
async def test_needs_user_sms_reply_resumes_automatically_under_explicit_authority(db, monkeypatch):
    await set_standing_authority(db, "routine_communications", {"enabled": True})
    event = CommunicationEvent(
        external_id="test:v107:sms:1",
        channel="sms",
        provider="device",
        sender="+32000000000",
        body="Can you confirm Tuesday at 10?",
        direction="incoming",
        action_required=True,
        protected=False,
        decision_json=json.dumps({"reply_text": "Yes, Tuesday at 10 works."}),
        occurred_at=datetime.utcnow(),
    )
    db.add(event)
    await db.flush()
    objective = VAObjective(
        correlation_key="test:v107:objective:1",
        source_type="communication_event",
        source_id=str(event.id),
        title="Confirm Tuesday appointment",
        goal="Confirm the appointment",
        category="communication_reply",
        priority="normal",
        risk_level="high",
        status="needs_user",
        needs_user_reason="Material communication decision",
        context_json="{}",
        plan_json="{}",
    )
    db.add(objective)
    await db.commit()

    async def queued(*args, **kwargs):
        return "queued"

    monkeypatch.setattr(
        "app.services.specific_authorization._queue_authorized_device_reply",
        queued,
    )
    result = await apply_standing_authority_objectives(db)
    await db.refresh(objective)
    assert result["authorized"] == 1
    assert objective.status == "cancelled"
    context = json.loads(objective.context_json)
    assert context["standing_authority"]["policy_key"] == "routine_communications"
    assert context["standing_authority"]["decision"] == "authorized"
    assert event.action_required is False
    audits = list(
        (
            await db.execute(select(AuditLog).where(AuditLog.event_type == "va_standing_authority_used"))
        ).scalars()
    )
    assert audits
