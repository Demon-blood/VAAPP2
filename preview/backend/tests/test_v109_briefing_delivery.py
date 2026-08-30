from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models.entities import BriefingDelivery, Device
from app.services.briefing_delivery import (
    acknowledge_briefing_delivery,
    issue_briefing_delivery_token,
    resolve_briefing_window_start,
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


async def _device(db, name: str, token_hash: str) -> Device:
    row = Device(name=name, token_hash=token_hash, enabled=True)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


@pytest.mark.asyncio
async def test_no_acknowledgement_uses_normal_fallback_lookback(db):
    device = await _device(db, "phone-a", "a" * 64)
    now = datetime(2026, 8, 30, 18, 0, 0)
    start, acknowledged = await resolve_briefing_window_start(
        db, device_id=device.id, now=now, fallback_hours=24
    )
    assert start == now - timedelta(hours=24)
    assert acknowledged is None


@pytest.mark.asyncio
async def test_acknowledged_morning_delivery_becomes_evening_window_start(db):
    device = await _device(db, "phone-a", "a" * 64)
    morning_start = datetime(2026, 8, 29, 19, 0, 0)
    morning_end = datetime(2026, 8, 30, 8, 0, 0)
    token = issue_briefing_delivery_token(
        device,
        delivery_key="2026-08-30:morning",
        window_start=morning_start,
        window_end=morning_end,
    )
    await acknowledge_briefing_delivery(
        db,
        device=device,
        delivery_key="2026-08-30:morning",
        delivery_token=token,
    )

    start, acknowledged = await resolve_briefing_window_start(
        db,
        device_id=device.id,
        now=datetime(2026, 8, 30, 18, 0, 0),
        fallback_hours=24,
    )
    assert start == morning_end
    assert acknowledged is not None
    assert acknowledged.delivery_key == "2026-08-30:morning"


@pytest.mark.asyncio
async def test_repeated_acknowledgement_is_idempotent(db):
    device = await _device(db, "phone-a", "a" * 64)
    start = datetime(2026, 8, 30, 7, 0, 0)
    end = datetime(2026, 8, 30, 8, 0, 0)
    token = issue_briefing_delivery_token(
        device,
        delivery_key="2026-08-30:morning",
        window_start=start,
        window_end=end,
    )
    first = await acknowledge_briefing_delivery(
        db, device=device, delivery_key="2026-08-30:morning", delivery_token=token
    )
    second = await acknowledge_briefing_delivery(
        db, device=device, delivery_key="2026-08-30:morning", delivery_token=token
    )
    count = int((await db.execute(select(func.count(BriefingDelivery.id)))).scalar_one())
    assert first["idempotent"] is False
    assert second["idempotent"] is True
    assert count == 1


@pytest.mark.asyncio
async def test_device_ledgers_are_isolated_and_tokens_are_device_bound(db):
    first = await _device(db, "phone-a", "a" * 64)
    second = await _device(db, "phone-b", "b" * 64)
    boundary = datetime(2026, 8, 30, 8, 0, 0)
    token = issue_briefing_delivery_token(
        first,
        delivery_key="2026-08-30:morning",
        window_start=boundary - timedelta(hours=12),
        window_end=boundary,
    )
    await acknowledge_briefing_delivery(
        db, device=first, delivery_key="2026-08-30:morning", delivery_token=token
    )

    now = datetime(2026, 8, 30, 18, 0, 0)
    first_start, _ = await resolve_briefing_window_start(
        db, device_id=first.id, now=now, fallback_hours=24
    )
    second_start, second_ack = await resolve_briefing_window_start(
        db, device_id=second.id, now=now, fallback_hours=24
    )
    assert first_start == boundary
    assert second_start == now - timedelta(hours=24)
    assert second_ack is None
    with pytest.raises(ValueError, match="invalid briefing delivery token|another device"):
        await acknowledge_briefing_delivery(
            db, device=second, delivery_key="2026-08-30:morning", delivery_token=token
        )


@pytest.mark.asyncio
async def test_generated_but_unacknowledged_briefing_never_advances_window(db):
    device = await _device(db, "phone-a", "a" * 64)
    now = datetime(2026, 8, 30, 18, 0, 0)
    _ = issue_briefing_delivery_token(
        device,
        delivery_key="2026-08-30:evening",
        window_start=now - timedelta(hours=24),
        window_end=now,
    )
    start, acknowledged = await resolve_briefing_window_start(
        db, device_id=device.id, now=now + timedelta(minutes=1), fallback_hours=24
    )
    assert start == now + timedelta(minutes=1) - timedelta(hours=24)
    assert acknowledged is None


@pytest.mark.asyncio
async def test_old_acknowledgement_is_capped_by_maximum_lookback(db):
    device = await _device(db, "phone-a", "a" * 64)
    now = datetime(2026, 8, 30, 18, 0, 0)
    old = BriefingDelivery(
        device_id=device.id,
        delivery_key="2026-08-20:evening",
        period="evening",
        window_start=now - timedelta(days=11),
        window_end=now - timedelta(days=10),
        delivered_at=now - timedelta(days=10),
    )
    db.add(old)
    await db.commit()

    start, acknowledged = await resolve_briefing_window_start(
        db, device_id=device.id, now=now, fallback_hours=24, max_lookback_hours=72
    )
    assert acknowledged is not None
    assert start == now - timedelta(hours=72)
