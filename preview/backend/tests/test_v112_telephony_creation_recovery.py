from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import ClassVar

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.crypto import encrypt_text, hash_token
from app.core.database import Base
from app.models.telephony_entities import TelephonyCall, TelephonyEvidence
from app.services import telephony_service


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _uncertain_call(db, *, started_at: datetime | None = None) -> TelephonyCall:
    started = started_at or datetime(2026, 8, 30, 19, 0, 0, tzinfo=UTC)
    token = "v112-telephony-token"
    call = TelephonyCall(
        idempotency_key="v112-call-intent",
        series_key="v112-call-intent",
        attempt=1,
        max_attempts=3,
        direction="outbound",
        webhook_token_hash=hash_token(token),
        webhook_token_encrypted=encrypt_text(token),
        target_hash="target-hash",
        target_encrypted=encrypt_text("+32470123456"),
        from_number_encrypted=encrypt_text("+3221234567"),
        purpose_encrypted=encrypt_text("Confirm a routine appointment"),
        expected_outcome_encrypted=encrypt_text("Appointment confirmation received"),
        status="creation_uncertain",
        provider_status="",
        verification_status="unverified",
        failure_reason="Twilio call creation outcome is uncertain; automatic creation retry is blocked.",
        started_at=started,
    )
    db.add(call)
    await db.commit()
    return call


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class FakeClient:
    payload: ClassVar[dict[str, list[dict]]] = {"calls": []}
    requests: ClassVar[list[dict]] = []

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, endpoint, *, auth, params=None):
        self.__class__.requests.append({"endpoint": endpoint, "auth": auth, "params": dict(params or {})})
        return FakeResponse(self.__class__.payload)


def _configure(monkeypatch):
    async def fake_config(_db):
        return {
            "enabled": "true",
            "account_sid": "AC" + "1" * 32,
            "auth_token": "secret",
            "from_number": "+3221234567",
            "language": "en-GB",
            "owner_name": "",
            "max_turns": "10",
            "max_duration": "600",
            "max_attempts": "3",
        }

    FakeClient.requests = []
    monkeypatch.setattr(telephony_service, "_twilio_config", fake_config)
    monkeypatch.setattr(telephony_service.httpx, "AsyncClient", FakeClient)


@pytest.mark.asyncio
async def test_unique_provider_candidate_recovers_existing_call_without_redial(db, monkeypatch):
    _configure(monkeypatch)
    call = await _uncertain_call(db)
    sid = "CA" + "2" * 32
    FakeClient.payload = {
        "calls": [
            {
                "sid": sid,
                "to": "+32470123456",
                "from": "+3221234567",
                "direction": "outbound-api",
                "date_created": "Sun, 30 Aug 2026 19:04:00 +0000",
                "status": "completed",
            }
        ]
    }

    recovered = await telephony_service._recover_uncertain_call_creation(db, call)

    assert recovered is True
    assert call.external_call_sid == sid
    assert call.provider_status == "completed"
    assert call.status == "provider_completed_unverified"
    assert call.verification_status == "unverified"
    assert call.failure_reason == ""
    assert FakeClient.requests[0]["params"]["To"] == "+32470123456"
    assert FakeClient.requests[0]["params"]["From"] == "+3221234567"
    assert int(FakeClient.requests[0]["params"]["PageSize"]) <= 100
    count = int((await db.execute(select(func.count(TelephonyCall.id)))).scalar_one())
    assert count == 1
    evidence = (
        await db.execute(
            select(TelephonyEvidence).where(
                TelephonyEvidence.call_id == call.id,
                TelephonyEvidence.event_type == "provider_create_recovered",
            )
        )
    ).scalar_one()
    assert evidence.external_ref == sid


@pytest.mark.asyncio
async def test_multiple_provider_candidates_remain_va_owned_and_unbound(db, monkeypatch):
    _configure(monkeypatch)
    call = await _uncertain_call(db)
    FakeClient.payload = {
        "calls": [
            {
                "sid": "CA" + "3" * 32,
                "to": "+32470123456",
                "from": "+3221234567",
                "direction": "outbound-api",
                "date_created": "Sun, 30 Aug 2026 19:03:00 +0000",
                "status": "completed",
            },
            {
                "sid": "CA" + "4" * 32,
                "to": "+32470123456",
                "from": "+3221234567",
                "direction": "outbound-api",
                "date_created": "Sun, 30 Aug 2026 19:06:00 +0000",
                "status": "no-answer",
            },
        ]
    }

    recovered = await telephony_service._recover_uncertain_call_creation(db, call)

    assert recovered is False
    assert call.status == "creation_uncertain"
    assert call.external_call_sid is None
    assert call.needs_user is False
    assert "Multiple Twilio calls match" in call.failure_reason
    assert "blind redial remains blocked" in call.failure_reason
    count = int((await db.execute(select(func.count(TelephonyCall.id)))).scalar_one())
    assert count == 1


@pytest.mark.asyncio
async def test_retry_child_is_blocked_until_provider_identity_and_terminal_state(db):
    call = await _uncertain_call(db)
    call.next_retry_at = datetime.now(UTC) - timedelta(minutes=1)
    await db.commit()

    child = await telephony_service._create_retry_call(db, call)
    assert child is None
    assert call.next_retry_at is not None
    assert int((await db.execute(select(func.count(TelephonyCall.id)))).scalar_one()) == 1

    call.external_call_sid = "CA" + "5" * 32
    call.provider_status = "ringing"
    await db.commit()
    child = await telephony_service._create_retry_call(db, call)
    assert child is None
    assert call.next_retry_at is not None
    assert int((await db.execute(select(func.count(TelephonyCall.id)))).scalar_one()) == 1
