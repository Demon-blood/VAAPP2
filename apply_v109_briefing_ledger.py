from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

EXPECTED_BASELINE = "2bfed2996167dbc440bb4f2a7b95f13c987f8a86"

BRIEFING_DELIVERY_SERVICE = r'''from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import BriefingDelivery, Device

_TOKEN_VERSION = 1
_DEFAULT_MAX_LOOKBACK_HOURS = 72


def _delivery_dict(row: BriefingDelivery, *, idempotent: bool) -> dict[str, Any]:
    return {
        "acknowledged": True,
        "idempotent": idempotent,
        "delivery_key": row.delivery_key,
        "period": row.period,
        "window_start": row.window_start.isoformat() + "Z",
        "window_end": row.window_end.isoformat() + "Z",
        "delivered_at": row.delivered_at.isoformat() + "Z",
    }


def issue_briefing_delivery_token(
    device: Device,
    *,
    delivery_key: str,
    window_start: datetime,
    window_end: datetime,
) -> str:
    """Sign a server-generated briefing window so the client cannot advance it arbitrarily."""
    payload = json.dumps(
        {
            "v": _TOKEN_VERSION,
            "device_id": int(device.id),
            "delivery_key": delivery_key,
            "window_start": window_start.isoformat() + "Z",
            "window_end": window_end.isoformat() + "Z",
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    encoded = base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")
    signature = hmac.new(
        device.token_hash.encode("utf-8"),
        encoded.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    return f"{encoded}.{signature}"


def _decode_delivery_token(device: Device, delivery_key: str, token: str) -> tuple[datetime, datetime]:
    try:
        encoded, signature = token.split(".", 1)
    except ValueError as exc:
        raise ValueError("invalid briefing delivery token") from exc

    expected = hmac.new(
        device.token_hash.encode("utf-8"),
        encoded.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise ValueError("invalid briefing delivery token")

    try:
        padding = "=" * (-len(encoded) % 4)
        raw = base64.urlsafe_b64decode((encoded + padding).encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError, binascii.Error) as exc:
        raise ValueError("invalid briefing delivery token") from exc

    if not isinstance(payload, dict) or int(payload.get("v") or 0) != _TOKEN_VERSION:
        raise ValueError("invalid briefing delivery token")
    if int(payload.get("device_id") or 0) != int(device.id):
        raise ValueError("briefing delivery token belongs to another device")
    if str(payload.get("delivery_key") or "") != delivery_key:
        raise ValueError("briefing delivery token key mismatch")

    def parse_boundary(name: str) -> datetime:
        value = str(payload.get(name) or "")
        if not value:
            raise ValueError("invalid briefing delivery token")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("invalid briefing delivery token") from exc
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed

    window_start = parse_boundary("window_start")
    window_end = parse_boundary("window_end")
    if window_end < window_start:
        raise ValueError("invalid briefing delivery window")
    return window_start, window_end


async def resolve_briefing_window_start(
    db: AsyncSession,
    *,
    device_id: int | None,
    now: datetime,
    fallback_hours: int,
    max_lookback_hours: int = _DEFAULT_MAX_LOOKBACK_HOURS,
) -> tuple[datetime, BriefingDelivery | None]:
    """Resolve a conservative lower bound from the last proven delivery for this device."""
    fallback_hours = max(1, int(fallback_hours))
    max_lookback_hours = max(fallback_hours, int(max_lookback_hours))
    fallback = now - timedelta(hours=fallback_hours)
    if device_id is None:
        return fallback, None

    last = (
        await db.execute(
            select(BriefingDelivery)
            .where(BriefingDelivery.device_id == device_id)
            .order_by(BriefingDelivery.window_end.desc(), BriefingDelivery.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if last is None:
        return fallback, None

    floor = now - timedelta(hours=max_lookback_hours)
    acknowledged_boundary = min(last.window_end, now)
    return max(acknowledged_boundary, floor), last


async def acknowledge_briefing_delivery(
    db: AsyncSession,
    *,
    device: Device,
    delivery_key: str,
    delivery_token: str,
) -> dict[str, Any]:
    """Persist proof that the OS notification call succeeded for one scheduled briefing."""
    delivery_key = delivery_key.strip()
    if not delivery_key or len(delivery_key) > 120:
        raise ValueError("delivery_key is required")
    if not delivery_token:
        raise ValueError("delivery_token is required")

    # Validate the server-issued proof even for a duplicate acknowledgement.
    # Idempotency must not turn a guessed delivery key into accepted delivery evidence.
    window_start, window_end = _decode_delivery_token(device, delivery_key, delivery_token)

    existing = (
        await db.execute(
            select(BriefingDelivery).where(
                BriefingDelivery.device_id == device.id,
                BriefingDelivery.delivery_key == delivery_key,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return _delivery_dict(existing, idempotent=True)

    delivered_at = datetime.utcnow()
    # A server-issued generation boundary should never be in the future at receipt time.
    # Clamp sub-second/clock anomalies conservatively rather than allowing the ledger to skip work.
    window_end = min(window_end, delivered_at)
    window_start = min(window_start, window_end)

    row = BriefingDelivery(
        device_id=device.id,
        delivery_key=delivery_key,
        period=delivery_key.rsplit(":", 1)[-1][:24],
        window_start=window_start,
        window_end=window_end,
        delivered_at=delivered_at,
    )
    db.add(row)
    try:
        await db.commit()
        await db.refresh(row)
        return _delivery_dict(row, idempotent=False)
    except IntegrityError:
        # Concurrent duplicate ACKs are still idempotent because of the unique constraint.
        await db.rollback()
        existing = (
            await db.execute(
                select(BriefingDelivery).where(
                    BriefingDelivery.device_id == device.id,
                    BriefingDelivery.delivery_key == delivery_key,
                )
            )
        ).scalar_one()
        return _delivery_dict(existing, idempotent=True)
'''

V109_TESTS = r'''from __future__ import annotations

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
'''

V109_CONTRACT_TEST = r'''from pathlib import Path

ROOT = Path(__file__).parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_v109_release_identity_is_consistent():
    assert 'APP_VERSION = "1.0.9"' in read("backend/app/core/version.py")
    assert 'REQUIRED_ANDROID_VERSION = "1.0.9"' in read("backend/app/core/version.py")
    assert 'version = "1.0.9"' in read("backend/pyproject.toml")
    assert "version: 1.0.9+52" in read("android/pubspec.yaml")
    release = read("android/lib/release_contract.dart")
    assert "appRelease = '1.0.9'" in release
    assert "minimumBackendVersion = '1.0.9'" in release


def test_briefing_delivery_ledger_is_additive_device_scoped_and_idempotent():
    entities = read("backend/app/models/entities.py")
    service = read("backend/app/services/briefing_delivery.py")
    assert "class BriefingDelivery" in entities
    assert '__tablename__ = "briefing_deliveries"' in entities
    assert 'UniqueConstraint("device_id", "delivery_key"' in entities
    assert "BriefingDelivery.device_id == device_id" in service
    assert "device.token_hash" in service
    assert "hmac.compare_digest" in service
    assert "delivered_at = datetime.utcnow()" in service
    assert "IntegrityError" in service


def test_old_briefing_get_contract_remains_and_ack_is_additive():
    api = read("backend/app/api_autopilot.py")
    assert '@router.get("/briefing")' in api
    assert "return await daily_briefing(db, device=device)" in api
    assert '@router.post("/briefing/deliveries")' in api
    assert "delivery_token" in api
    assert "window_end" not in api.split('@router.post("/briefing/deliveries")', 1)[1].split('@router.', 1)[0]


def test_notification_failure_cannot_ack_and_successful_show_is_durably_retried():
    source = read("android/lib/services/background_service.dart")
    scheduled = source.index("id: 1002")
    local_key = source.index("storage.write(key: _briefingPeriodKey", scheduled)
    pending = source.index("storage.write(key: _briefingPendingAckKey", local_key)
    ack = source.index("await _ackBriefingDelivery(", pending)
    assert scheduled < local_key < pending < ack
    # notifications.show is awaited before any local delivered/pending state is written, so
    # a thrown OS-notification failure exits through the outer catch without an ACK.
    show = source.rfind("await notifications.show(", max(0, scheduled - 200), local_key)
    assert show != -1 and show < local_key
    assert "final pendingAckKey = await storage.read(key: _briefingPendingAckKey)" in source
    assert "if (acknowledged)" in source
    assert "storage.delete(key: _briefingPendingAckKey)" in source
    assert "delivery_token" in source
    assert "/api/autopilot/briefing/deliveries" in source


def test_old_android_clients_still_use_the_original_get_briefing_contract():
    api = read("backend/app/api_autopilot.py")
    assert '@router.get("/briefing")' in api
    # Acknowledgement is a separate additive POST; old clients never need to call it.
    assert '@router.post("/briefing/deliveries")' in api


def test_urgent_interrupts_are_independent_from_scheduled_delivery_ack():
    source = read("android/lib/services/background_service.dart")
    urgent_start = source.index("id: 1001")
    scheduled_start = source.index("id: 1002")
    urgent_block = source[urgent_start:scheduled_start]
    assert "_ackBriefingDelivery" not in urgent_block
    assert "last_va_priority_signature" in source


def test_briefing_window_comes_from_proven_ack_or_fallback_not_generation_alone():
    service = read("backend/app/services/briefing_service.py")
    ledger = read("backend/app/services/briefing_delivery.py")
    assert "resolve_briefing_window_start" in service
    assert '"window_source"' in service
    assert "issue_briefing_delivery_token" in service
    assert "select(BriefingDelivery)" in ledger
    assert "max(acknowledged_boundary, floor)" in ledger
'''

V109_DOC = r'''# v1.0.9 — Briefing Ledger & Quiet Operations

v1.0.9 makes scheduled VA briefings delivery-aware without turning routine activity into notification noise.

## Delivery semantics

- Briefing activity uses a bounded server-side window with an explicit `window_start` and `window_end`.
- With no proven delivery, the existing configured fallback lookback is used.
- After a scheduled Android notification call succeeds, Android acknowledges that specific delivery key using a server-signed delivery token.
- The backend records the authenticated device, delivery key/period, server-issued briefing window, and server receipt time.
- The next briefing for that device starts at the last successfully acknowledged `window_end`.
- Generating or manually viewing a briefing does not advance the ledger.
- Failed notification delivery does not create acknowledgement state. A transient acknowledgement failure remains locally pending and is retried silently before the next briefing fetch; the server ledger advances only after a successful ACK.
- Ledger state is isolated per device and acknowledgement is idempotent by `(device_id, delivery_key)`.
- A 72-hour maximum lookback caps stale acknowledged boundaries after long offline periods.

## Trust boundary

Android does not send an authoritative delivery timestamp or arbitrary window boundary. The backend signs the generated window into an opaque token. On acknowledgement it verifies the token against the authenticated device and stamps `delivered_at` using server time.

## Compatibility

`GET /api/autopilot/briefing` remains in place for older Android clients. The new `POST /api/autopilot/briefing/deliveries` endpoint is additive. Older clients simply do not acknowledge deliveries and therefore retain conservative fallback-window behavior.

Urgent `interrupt=true` notifications remain completely independent from scheduled briefing acknowledgement.
'''


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def replace_once(path: Path, old: str, new: str) -> None:
    text = read_text(path)
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected exactly one match in {path}: found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def run_git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def verify_repo(root: Path) -> None:
    if not (root / ".git").exists():
        raise RuntimeError(f"{root} is not a git working tree")
    head = run_git(root, "rev-parse", "HEAD")
    if head != EXPECTED_BASELINE:
        raise RuntimeError(
            f"refusing to patch unexpected HEAD {head}; expected v1.0.8 baseline {EXPECTED_BASELINE}"
        )
    dirty = run_git(root, "status", "--porcelain")
    if dirty:
        raise RuntimeError("refusing to patch a dirty working tree")


def patch_entities(root: Path) -> None:
    path = root / "backend/app/models/entities.py"
    marker = '''class OAuthConnection(Base):\n'''
    insertion = '''class BriefingDelivery(Base):\n    __tablename__ = "briefing_deliveries"\n\n    id: Mapped[int] = mapped_column(primary_key=True)\n    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)\n    delivery_key: Mapped[str] = mapped_column(String(120))\n    period: Mapped[str] = mapped_column(String(24), default="")\n    window_start: Mapped[datetime] = mapped_column(DateTime)\n    window_end: Mapped[datetime] = mapped_column(DateTime)\n    delivered_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)\n    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)\n\n    __table_args__ = (\n        UniqueConstraint("device_id", "delivery_key", name="uq_briefing_delivery_device_key"),\n        Index("ix_briefing_delivery_device_window", "device_id", "window_end"),\n    )\n\n\nclass OAuthConnection(Base):\n'''
    replace_once(path, marker, insertion)


def patch_briefing_service(root: Path) -> None:
    path = root / "backend/app/services/briefing_service.py"
    replace_once(path, "from sqlalchemy import func, or_, select\n", "from sqlalchemy import and_, func, or_, select\n")
    replace_once(path, "    DocumentRecord,\n", "    Device,\n    DocumentRecord,\n")
    replace_once(
        path,
        "from app.services.runtime_config import get_runtime_value\n",
        "from app.services.briefing_delivery import issue_briefing_delivery_token, resolve_briefing_window_start\n"
        "from app.services.runtime_config import get_runtime_value\n",
    )
    replace_once(
        path,
        "async def daily_briefing(db: AsyncSession) -> dict[str, Any]:\n",
        "async def daily_briefing(db: AsyncSession, *, device: Device | None = None) -> dict[str, Any]:\n",
    )
    replace_once(
        path,
        '''    enabled = (await get_runtime_value(db, "daily_briefing_enabled", "true")).lower() == "true"\n\n    since = now - timedelta(hours=window_hours)\n    upcoming = now + timedelta(days=7)\n''',
        '''    enabled = (await get_runtime_value(db, "daily_briefing_enabled", "true")).lower() == "true"\n\n    periods = await briefing_period_schedule(db, local_now)\n    since, acknowledged_delivery = await resolve_briefing_window_start(\n        db,\n        device_id=device.id if device is not None else None,\n        now=now,\n        fallback_hours=window_hours,\n    )\n    if device is not None:\n        for period in periods:\n            if period["enabled"] and period["ready"]:\n                period["delivery_token"] = issue_briefing_delivery_token(\n                    device,\n                    delivery_key=str(period["delivery_key"]),\n                    window_start=since,\n                    window_end=now,\n                )\n    upcoming = now + timedelta(days=7)\n''',
    )

    # Bound all rolling activity to the same server generation boundary.
    replace_once(
        path,
        ".where(EmailMessage.received_at.is_not(None), EmailMessage.received_at >= since)\n",
        ".where(\n                    EmailMessage.received_at.is_not(None),\n                    EmailMessage.received_at >= since,\n                    EmailMessage.received_at <= now,\n                )\n",
    )
    replace_once(
        path,
        '.where(Task.status == "completed", Task.updated_at >= since)\n',
        '.where(Task.status == "completed", Task.updated_at >= since, Task.updated_at <= now)\n',
    )
    replace_once(
        path,
        ".where(or_(Bill.created_at >= since, Bill.updated_at >= since))\n",
        ".where(\n                    or_(\n                        and_(Bill.created_at >= since, Bill.created_at <= now),\n                        and_(Bill.updated_at >= since, Bill.updated_at <= now),\n                    )\n                )\n",
    )
    replace_once(
        path,
        ".where(or_(Payment.created_at >= since, Payment.updated_at >= since, Payment.requires_user_action.is_(True)))\n",
        ".where(\n                    or_(\n                        and_(Payment.created_at >= since, Payment.created_at <= now),\n                        and_(Payment.updated_at >= since, Payment.updated_at <= now),\n                        Payment.requires_user_action.is_(True),\n                    )\n                )\n",
    )
    replace_once(
        path,
        ".where(FinancialRecord.created_at >= since)\n",
        ".where(FinancialRecord.created_at >= since, FinancialRecord.created_at <= now)\n",
    )
    replace_once(
        path,
        ".where(DocumentRecord.created_at >= since)\n",
        ".where(DocumentRecord.created_at >= since, DocumentRecord.created_at <= now)\n",
    )
    replace_once(
        path,
        ".where(AuditLog.created_at >= since)\n",
        ".where(AuditLog.created_at >= since, AuditLog.created_at <= now)\n",
    )
    replace_once(
        path,
        ".where(CommunicationEvent.occurred_at.is_not(None), CommunicationEvent.occurred_at >= since)\n",
        ".where(\n                    CommunicationEvent.occurred_at.is_not(None),\n                    CommunicationEvent.occurred_at >= since,\n                    CommunicationEvent.occurred_at <= now,\n                )\n",
    )
    replace_once(
        path,
        '''                    CommunicationAction.updated_at >= since,\n''',
        '''                    CommunicationAction.updated_at >= since,\n                    CommunicationAction.updated_at <= now,\n''',
    )
    replace_once(
        path,
        ".where(or_(OwnAccountTransfer.created_at >= since, OwnAccountTransfer.updated_at >= since, OwnAccountTransfer.requires_user_action.is_(True)))\n",
        ".where(\n                    or_(\n                        and_(OwnAccountTransfer.created_at >= since, OwnAccountTransfer.created_at <= now),\n                        and_(OwnAccountTransfer.updated_at >= since, OwnAccountTransfer.updated_at <= now),\n                        OwnAccountTransfer.requires_user_action.is_(True),\n                    )\n                )\n",
    )

    replace_once(
        path,
        '''    commitments = await executive_commitment_overview(db)\n    periods = await briefing_period_schedule(db, local_now)\n    ready_periods = [item for item in periods if item["enabled"] and item["ready"]]\n''',
        '''    commitments = await executive_commitment_overview(db)\n    ready_periods = [item for item in periods if item["enabled"] and item["ready"]]\n''',
    )
    replace_once(
        path,
        '''        "window_start": since.isoformat() + "Z",\n        "window_end": now.isoformat() + "Z",\n        "timezone": getattr(tz, "key", str(tz)),\n''',
        '''        "window_start": since.isoformat() + "Z",\n        "window_end": now.isoformat() + "Z",\n        "window_source": "acknowledged_delivery" if acknowledged_delivery is not None else "fallback",\n        "last_acknowledged_delivery_key": (\n            acknowledged_delivery.delivery_key if acknowledged_delivery is not None else None\n        ),\n        "timezone": getattr(tz, "key", str(tz)),\n''',
    )


def patch_api(root: Path) -> None:
    path = root / "backend/app/api_autopilot.py"
    replace_once(
        path,
        "from app.services.briefing_service import daily_briefing\n",
        "from app.services.briefing_delivery import acknowledge_briefing_delivery\n"
        "from app.services.briefing_service import daily_briefing\n",
    )
    replace_once(
        path,
        '''@router.get("/briefing")\nasync def get_daily_briefing(\n    db: AsyncSession = Depends(get_db),\n    _: Device = Depends(require_device),\n) -> dict:\n    return await daily_briefing(db)\n\n\n''',
        '''@router.get("/briefing")\nasync def get_daily_briefing(\n    db: AsyncSession = Depends(get_db),\n    device: Device = Depends(require_device),\n) -> dict:\n    return await daily_briefing(db, device=device)\n\n\n@router.post("/briefing/deliveries")\nasync def post_briefing_delivery(\n    payload: dict = Body(...),\n    db: AsyncSession = Depends(get_db),\n    device: Device = Depends(require_device),\n) -> dict:\n    delivery_key = str(payload.get("delivery_key") or "").strip()\n    delivery_token = str(payload.get("delivery_token") or "").strip()\n    if not delivery_key or not delivery_token:\n        raise HTTPException(status_code=400, detail="delivery_key and delivery_token are required")\n    try:\n        return await acknowledge_briefing_delivery(\n            db,\n            device=device,\n            delivery_key=delivery_key,\n            delivery_token=delivery_token,\n        )\n    except ValueError as exc:\n        raise HTTPException(status_code=400, detail=str(exc)) from exc\n\n\n''',
    )


def patch_android(root: Path) -> None:
    path = root / "android/lib/services/background_service.dart"
    replace_once(
        path,
        "const _briefingPeriodKey = 'last_va_briefing_period';\nconst _prioritySignatureKey = 'last_va_priority_signature';\n",
        "const _briefingPeriodKey = 'last_va_briefing_period';\n"
        "const _briefingPendingAckKey = 'pending_va_briefing_ack_key';\n"
        "const _briefingPendingAckTokenKey = 'pending_va_briefing_ack_token';\n"
        "const _prioritySignatureKey = 'last_va_priority_signature';\n",
    )
    replace_once(
        path,
        '''String _prioritySignature(List<dynamic> items) {\n  final normalized = items\n      .whereType<Map>()\n      .map((item) => [\n            '${item['type'] ?? ''}',\n            '${item['id'] ?? ''}',\n            '${item['title'] ?? ''}',\n            '${item['detail'] ?? ''}',\n          ].join('|'))\n      .toList()\n    ..sort();\n  return normalized.join('||');\n}\n\n''',
        '''String _prioritySignature(List<dynamic> items) {\n  final normalized = items\n      .whereType<Map>()\n      .map((item) => [\n            '${item['type'] ?? ''}',\n            '${item['id'] ?? ''}',\n            '${item['title'] ?? ''}',\n            '${item['detail'] ?? ''}',\n          ].join('|'))\n      .toList()\n    ..sort();\n  return normalized.join('||');\n}\n\nFuture<bool> _ackBriefingDelivery({\n  required String base,\n  required String token,\n  required String deliveryKey,\n  required String deliveryToken,\n}) async {\n  if (deliveryKey.isEmpty || deliveryToken.isEmpty) return false;\n  try {\n    final response = await http.post(\n      Uri.parse('$base/api/autopilot/briefing/deliveries'),\n      headers: {\n        'Authorization': 'Bearer $token',\n        'Accept': 'application/json',\n        'Content-Type': 'application/json',\n      },\n      body: jsonEncode({\n        'delivery_key': deliveryKey,\n        'delivery_token': deliveryToken,\n      }),\n    ).timeout(const Duration(seconds: 15));\n    return response.statusCode >= 200 && response.statusCode < 300;\n  } catch (_) {\n    // The notification was shown, but delivery could not be proven to the backend.\n    // Keep the durable local proof pending so a later poll can retry silently.\n    return false;\n  }\n}\n\n''',
    )
    replace_once(
        path,
        "    if (base == null || token == null) return true;\n\n    try {\n      final response = await http.get(\n",
        "    if (base == null || token == null) return true;\n\n"
        "    try {\n"
        "      // A successful OS notification can outlive a transient backend/network failure.\n"
        "      // Retry that proof before fetching the next briefing so its window can advance.\n"
        "      final pendingAckKey = await storage.read(key: _briefingPendingAckKey) ?? '';\n"
        "      final pendingAckToken = await storage.read(key: _briefingPendingAckTokenKey) ?? '';\n"
        "      if (pendingAckKey.isNotEmpty && pendingAckToken.isNotEmpty) {\n"
        "        final acknowledged = await _ackBriefingDelivery(\n"
        "          base: base,\n"
        "          token: token,\n"
        "          deliveryKey: pendingAckKey,\n"
        "          deliveryToken: pendingAckToken,\n"
        "        );\n"
        "        if (acknowledged) {\n"
        "          await storage.delete(key: _briefingPendingAckKey);\n"
        "          await storage.delete(key: _briefingPendingAckTokenKey);\n"
        "        }\n"
        "      }\n\n"
        "      final response = await http.get(\n",
    )
    replace_once(
        path,
        '''            await storage.write(key: _briefingPeriodKey, value: key);\n            // Keep the legacy key updated so downgrades do not duplicate the evening briefing.\n            await storage.write(key: _dailyBriefingDayKey, value: briefingDate);\n''',
        '''            await storage.write(key: _briefingPeriodKey, value: key);\n            // Keep the legacy key updated so downgrades do not duplicate the evening briefing.\n            await storage.write(key: _dailyBriefingDayKey, value: briefingDate);\n            final deliveryToken = '${period['delivery_token'] ?? ''}';\n            if (deliveryToken.isNotEmpty) {\n              // Persist proof-of-show intent before the network ACK. If the ACK fails, the\n              // next poll retries silently without showing the same notification again.\n              await storage.write(key: _briefingPendingAckKey, value: key);\n              await storage.write(key: _briefingPendingAckTokenKey, value: deliveryToken);\n              final acknowledged = await _ackBriefingDelivery(\n                base: base,\n                token: token,\n                deliveryKey: key,\n                deliveryToken: deliveryToken,\n              );\n              if (acknowledged) {\n                await storage.delete(key: _briefingPendingAckKey);\n                await storage.delete(key: _briefingPendingAckTokenKey);\n              }\n            }\n''',
    )


def bump_versions(root: Path) -> None:
    replace_once(
        root / "backend/app/core/version.py",
        'APP_VERSION = "1.0.8"\nREQUIRED_ANDROID_VERSION = "1.0.8"\n',
        'APP_VERSION = "1.0.9"\nREQUIRED_ANDROID_VERSION = "1.0.9"\n',
    )
    replace_once(root / "backend/pyproject.toml", 'version = "1.0.8"', 'version = "1.0.9"')
    replace_once(root / "android/pubspec.yaml", "version: 1.0.8+51", "version: 1.0.9+52")
    replace_once(
        root / "android/lib/release_contract.dart",
        "const String appRelease = '1.0.8';\nconst String minimumBackendVersion = '1.0.8';",
        "const String appRelease = '1.0.9';\nconst String minimumBackendVersion = '1.0.9';",
    )

    # Historical feature suites keep their historical behavior assertions, but their
    # live release-identity assertions move forward with the runtime. Match the guarded
    # v1.0.8 installer: replace only explicit release-contract literals, never arbitrary
    # historical prose, release filenames, or workflow expectations.
    release_literal_replacements = (
        ('APP_VERSION = "1.0.8"', 'APP_VERSION = "1.0.9"'),
        ('REQUIRED_ANDROID_VERSION = "1.0.8"', 'REQUIRED_ANDROID_VERSION = "1.0.9"'),
        ('version = "1.0.8"', 'version = "1.0.9"'),
        ('version: 1.0.8+51', 'version: 1.0.9+52'),
        ("appRelease = '1.0.8'", "appRelease = '1.0.9'"),
        ("minimumBackendVersion = '1.0.8'", "minimumBackendVersion = '1.0.9'"),
        ('APP_VERSION == "1.0.8"', 'APP_VERSION == "1.0.9"'),
    )
    updated_contracts = 0
    for path in sorted((root / "backend/tests").glob("test_*.py")):
        if path.name.startswith("test_v109_"):
            continue
        text = read_text(path)
        updated = text
        for old, new in release_literal_replacements:
            updated = updated.replace(old, new)
        if updated != text:
            path.write_text(updated, encoding="utf-8")
            updated_contracts += 1
    if updated_contracts < 1:
        raise RuntimeError("expected at least one living release contract to move from 1.0.8 to 1.0.9")


def write_new_files(root: Path) -> None:
    new_files = {
        "backend/app/services/briefing_delivery.py": BRIEFING_DELIVERY_SERVICE,
        "backend/tests/test_v109_briefing_delivery.py": V109_TESTS,
        "backend/tests/test_v109_briefing_ledger_contract.py": V109_CONTRACT_TEST,
        "docs/V1.0.9_BRIEFING_LEDGER_AND_QUIET_OPERATIONS.md": V109_DOC,
    }
    for relative, content in new_files.items():
        path = root / relative
        if path.exists():
            raise RuntimeError(f"refusing to overwrite existing {relative}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def verify_diff(root: Path) -> None:
    run_git(root, "diff", "--check")
    changed = [line for line in run_git(root, "diff", "--name-only").splitlines() if line]
    if not changed:
        raise RuntimeError("patch produced no changes")
    forbidden = [path for path in changed if path.startswith(".github/workflows/")]
    if forbidden:
        raise RuntimeError(f"workflow files changed unexpectedly: {forbidden}")
    if 'APP_VERSION = "1.0.9"' not in read_text(root / "backend/app/core/version.py"):
        raise RuntimeError("v1.0.9 backend version guard failed")
    if "version: 1.0.9+52" not in read_text(root / "android/pubspec.yaml"):
        raise RuntimeError("v1.0.9 Android build/version guard failed")
    print("v1.0.9 source patch prepared. Changed files:")
    for path in changed:
        print(f"  {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply VAAPP v1.0.9 Briefing Ledger & Quiet Operations")
    parser.add_argument("repo", nargs="?", default=".", help="path to a clean VAAPP2 checkout")
    args = parser.parse_args()
    root = Path(args.repo).resolve()

    verify_repo(root)
    patch_entities(root)
    write_new_files(root)
    patch_briefing_service(root)
    patch_api(root)
    patch_android(root)
    bump_versions(root)
    verify_diff(root)

    print("\nNext validation gates (do not publish unless all pass):")
    print("  cd backend && python -m pytest")
    print("  cd backend && python -m ruff check .")
    print("  cd android && flutter test")
    print("  cd android && flutter analyze")
    print("  cd android && flutter build apk --release")


if __name__ == "__main__":
    main()
