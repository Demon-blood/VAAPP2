from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models.entities import GmailOutboundMessage
from app.services import gmail_delivery

NOW = datetime(2026, 8, 30, 20, 0, 0, tzinfo=UTC).replace(tzinfo=None)


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _outbound(db, *, status: str, age: timedelta) -> GmailOutboundMessage:
    key = f"v113-{status}-{int(age.total_seconds())}"
    row = GmailOutboundMessage(
        idempotency_key=key,
        recipient="counterparty@example.com",
        subject="Routine follow-up",
        body="Hello",
        rfc_message_id=gmail_delivery.deterministic_rfc_message_id(key),
        status=status,
        attempts=1,
        max_attempts=1,
        verify_after=NOW - timedelta(minutes=1),
        created_at=NOW - age,
    )
    db.add(row)
    await db.commit()
    return row


def _sent_message(row: GmailOutboundMessage) -> dict:
    return {
        "id": "gmail-late-evidence-1",
        "threadId": "gmail-thread-1",
        "labelIds": ["SENT"],
        "payload": {
            "headers": [
                {"name": "Message-ID", "value": row.rfc_message_id},
            ]
        },
    }


@pytest.mark.asyncio
async def test_ambiguous_send_older_than_thirty_minutes_stays_va_owned_without_resend(db, monkeypatch):
    row = await _outbound(db, status="creation_uncertain", age=timedelta(hours=2))

    async def no_match(_db, _message_id, *, sent_only):
        assert sent_only is True

    async def forbidden_send(*_args, **_kwargs):
        raise AssertionError("ambiguous Gmail intent must never be re-POSTed")

    monkeypatch.setattr(gmail_delivery, "utcnow", lambda: NOW)
    monkeypatch.setattr(gmail_delivery, "find_gmail_message_by_rfc_message_id", no_match)
    monkeypatch.setattr(gmail_delivery, "send_gmail_message", forbidden_send)

    verified = await gmail_delivery.ensure_gmail_outbound_verified(db, row)

    assert verified is False
    assert row.status == "creation_uncertain"
    assert row.attempts == 1
    assert row.verify_after == NOW + timedelta(minutes=15)
    assert "continue provider reconciliation" in row.last_error
    assert "without resending" in row.last_error


@pytest.mark.asyncio
async def test_historical_failed_uncertain_row_can_recover_from_late_sent_evidence(db, monkeypatch):
    row = await _outbound(db, status="failed_uncertain", age=timedelta(hours=3))

    async def late_match(_db, message_id, *, sent_only):
        assert sent_only is True
        assert message_id == row.rfc_message_id
        return _sent_message(row)

    async def forbidden_send(*_args, **_kwargs):
        raise AssertionError("historical uncertainty recovery must not send a second message")

    monkeypatch.setattr(gmail_delivery, "utcnow", lambda: NOW)
    monkeypatch.setattr(gmail_delivery, "find_gmail_message_by_rfc_message_id", late_match)
    monkeypatch.setattr(gmail_delivery, "send_gmail_message", forbidden_send)

    verified = await gmail_delivery.ensure_gmail_outbound_verified(db, row)

    assert verified is True
    assert row.status == "verified"
    assert row.external_message_id == "gmail-late-evidence-1"
    assert row.external_thread_id == "gmail-thread-1"
    assert row.attempts == 1
    assert row.last_error == ""


@pytest.mark.asyncio
async def test_historical_failed_uncertain_without_evidence_reenters_reconciliation_only(db, monkeypatch):
    row = await _outbound(db, status="failed_uncertain", age=timedelta(days=8))

    async def no_match(_db, _message_id, *, sent_only):
        assert sent_only is True

    async def forbidden_send(*_args, **_kwargs):
        raise AssertionError("historical ambiguity must not create a second provider submission")

    monkeypatch.setattr(gmail_delivery, "utcnow", lambda: NOW)
    monkeypatch.setattr(gmail_delivery, "find_gmail_message_by_rfc_message_id", no_match)
    monkeypatch.setattr(gmail_delivery, "send_gmail_message", forbidden_send)

    verified = await gmail_delivery.ensure_gmail_outbound_verified(db, row)

    assert verified is False
    assert row.status == "creation_uncertain"
    assert row.verify_after == NOW + timedelta(hours=6)
    assert row.attempts == 1


@pytest.mark.asyncio
async def test_provider_verification_outage_preserves_long_term_uncertainty_and_slow_backoff(db, monkeypatch):
    row = await _outbound(db, status="creation_uncertain", age=timedelta(days=2))

    async def provider_unavailable(_db, _row):
        raise RuntimeError("temporary Gmail verification outage")

    monkeypatch.setattr(gmail_delivery, "utcnow", lambda: NOW)
    monkeypatch.setattr(gmail_delivery, "reconcile_gmail_outbound", provider_unavailable)

    verified = await gmail_delivery.ensure_gmail_outbound_verified(db, row)

    assert verified is False
    assert row.status == "creation_uncertain"
    assert row.verify_after == NOW + timedelta(hours=1)
    assert "verification failed" in row.last_error


def test_uncertain_verification_backoff_is_bounded_and_age_sensitive():
    row = type("Row", (), {})()

    row.created_at = NOW - timedelta(minutes=5)
    assert gmail_delivery._gmail_uncertain_verify_delay(row, NOW) == timedelta(minutes=2)

    row.created_at = NOW - timedelta(hours=3)
    assert gmail_delivery._gmail_uncertain_verify_delay(row, NOW) == timedelta(minutes=15)

    row.created_at = NOW - timedelta(days=2)
    assert gmail_delivery._gmail_uncertain_verify_delay(row, NOW) == timedelta(hours=1)

    row.created_at = NOW - timedelta(days=8)
    assert gmail_delivery._gmail_uncertain_verify_delay(row, NOW) == timedelta(hours=6)
