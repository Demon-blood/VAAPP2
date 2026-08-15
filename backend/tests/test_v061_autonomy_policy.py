from datetime import datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.entities import EmailMessage, OperationPreference, RelationshipIdentity, RuntimeSetting
from app.schemas.api import AutomationDecision
from app.services.autonomy_policy import (
    record_learned_preference,
    reply_autonomy_decision,
    task_requires_human,
)


@pytest.fixture
async def autonomy_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        for table in (RuntimeSetting, OperationPreference, EmailMessage, RelationshipIdentity):
            await connection.run_sync(table.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        yield db
    await engine.dispose()


def _decision(**updates) -> AutomationDecision:
    data = {
        "category": "general",
        "financial_document_type": "none",
        "priority": "normal",
        "action_required": True,
        "preserve": False,
        "archive": False,
        "trash": False,
        "labels": [],
        "task": None,
        "bill": None,
        "calendar_event": None,
        "reply": {"to": None, "subject": "Re: Meeting", "body": "Thanks, Thursday at 10 works for me."},
        "support_case": None,
        "order": None,
        "subscription": None,
        "archive_attachments": False,
        "reasoning_summary": "Confirm the proposed appointment time.",
    }
    data.update(updates)
    return AutomationDecision.model_validate(data)


@pytest.mark.asyncio
async def test_low_risk_reply_is_autonomous_by_default(autonomy_db):
    message = EmailMessage(
        provider_message_id="m1",
        thread_id="t1",
        sender="Clinic <clinic@example.com>",
        subject="Appointment time",
        snippet="Can you do Thursday at 10?",
        received_at=datetime.utcnow(),
        category="appointments",
    )
    allowed, reason = await reply_autonomy_decision(autonomy_db, message=message, decision=_decision())
    assert allowed is True
    assert reason == "deterministic_low_risk"


@pytest.mark.asyncio
async def test_financial_or_security_reply_stays_human_gated(autonomy_db):
    message = EmailMessage(
        provider_message_id="m2",
        thread_id="t2",
        sender="Bank <bank@example.com>",
        subject="Authorize transfer",
        snippet="Confirm the payment in Itsme.",
        received_at=datetime.utcnow(),
        category="banking",
    )
    decision = _decision(
        category="banking",
        financial_document_type="statement_or_notice",
        reply={"to": None, "subject": "Re: transfer", "body": "I authorize the payment."},
    )
    allowed, reason = await reply_autonomy_decision(autonomy_db, message=message, decision=decision)
    assert allowed is False
    assert reason in {"protected_category", "financial_context", "risky_commitment_or_sensitive_content"}


@pytest.mark.asyncio
async def test_learned_preference_requires_repeated_matching_outcomes(autonomy_db):
    for expected_samples in (1, 2, 3):
        row = await record_learned_preference(
            autonomy_db,
            domain="email_reply",
            key="sender:trusted@example.com",
            value={"auto_send": True, "category": "general"},
            minimum_samples=3,
        )
        assert row is not None
        assert row.sample_count == expected_samples
        assert row.enabled is (expected_samples >= 3)
        await autonomy_db.commit()


@pytest.mark.parametrize(
    ("task", "expected"),
    [
        ({"title": "File the warranty", "description": "Save the attachment", "due_at": None, "requires_approval": True}, False),
        ({"title": "Choose a supplier", "description": "Select one of the quotes", "due_at": None, "requires_approval": False}, True),
    ],
)
def test_task_gate_only_keeps_real_ambiguity_for_user(task, expected):
    decision = _decision(reply=None, task=task)
    requires_user, _ = task_requires_human(decision)
    assert requires_user is expected
