from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
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
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("invalid briefing delivery token") from exc
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(UTC).replace(tzinfo=None)
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

    delivered_at = datetime.now(UTC).replace(tzinfo=None)
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
