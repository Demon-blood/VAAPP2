from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.entities import Base, CommunicationAction, CommunicationEvent, Task
from app.schemas.api import CommunicationIngestRequest
from app.services import communications_service
from app.services.communications_service import ingest_communication


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
async def test_sensitive_message_never_auto_replies(db, monkeypatch) -> None:
    async def unsafe_ai(*_args, **_kwargs):
        return {
            "category": "Conversation",
            "priority": "low",
            "action_required": False,
            "protected": False,
            "spam": False,
            "auto_reply_safe": True,
            "reply_text": "Sure, I will transfer it.",
            "call_action": "allow",
            "reasoning_summary": "unsafe provider output",
        }

    monkeypatch.setattr(communications_service, "analyze_communication", unsafe_ai)
    result = await ingest_communication(
        db,
        CommunicationIngestRequest(
            external_id="sms-sensitive-1",
            channel="sms",
            sender="+32000000000",
            body="Please transfer money to IBAN BE00000000000000",
            supports_direct_reply=True,
        ),
    )
    assert result["decision"]["protected"] is True
    assert result["decision"]["auto_reply_safe"] is False
    assert result["device_action"] is None
    task = (await db.execute(select(Task).where(Task.source_type == "communication"))).scalar_one()
    assert task.requires_approval is True


@pytest.mark.asyncio
async def test_safe_reply_is_idempotent_across_duplicate_device_events(db, monkeypatch) -> None:
    async def safe_decision(_db, _payload):
        return {
            "category": "Routine conversation",
            "priority": "normal",
            "action_required": False,
            "protected": False,
            "spam": False,
            "auto_reply_safe": True,
            "reply_text": "I can make 18:00.",
            "call_action": "allow",
            "reasoning_summary": "Routine scheduling acknowledgement.",
        }

    monkeypatch.setattr(communications_service, "_decision_for", safe_decision)
    payload = CommunicationIngestRequest(
        external_id="wa-safe-1",
        channel="whatsapp",
        sender="Alice",
        body="Can you make it at six?",
        supports_direct_reply=True,
    )
    first = await ingest_communication(db, payload)
    second = await ingest_communication(db, payload)
    assert first["device_action"]["text"] == "I can make 18:00."
    assert second["duplicate"] is True
    actions = list((await db.execute(select(CommunicationAction))).scalars())
    events = list((await db.execute(select(CommunicationEvent))).scalars())
    assert len(actions) == 1
    assert len(events) == 1


@pytest.mark.asyncio
async def test_outgoing_messages_and_calls_never_create_reply_action(db, monkeypatch) -> None:
    async def should_not_matter(_db, _payload):
        return {
            "category": "Conversation", "priority": "normal", "action_required": False,
            "protected": False, "spam": False, "auto_reply_safe": True,
            "reply_text": "loop", "call_action": "allow", "reasoning_summary": "test",
        }

    monkeypatch.setattr(communications_service, "_decision_for", should_not_matter)
    outgoing = await ingest_communication(
        db,
        CommunicationIngestRequest(external_id="sms-out-1", channel="sms", direction="outgoing", body="hello", sender="me", recipient="Bob"),
    )
    call = await ingest_communication(
        db,
        CommunicationIngestRequest(external_id="call-1", channel="call", body="incoming", sender="+321", event_type="incoming_call"),
    )
    assert outgoing["event_id"] > 0
    assert call["event_id"] > 0
    actions = list((await db.execute(select(CommunicationAction))).scalars())
    assert actions == []
