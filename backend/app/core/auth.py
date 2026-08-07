from datetime import datetime

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import hash_token
from app.core.database import get_db
from app.models.entities import Device


async def require_device(
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> Device:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing device token")
    token = authorization.removeprefix("Bearer ").strip()
    result = await db.execute(select(Device).where(Device.token_hash == hash_token(token), Device.enabled.is_(True)))
    device = result.scalar_one_or_none()
    if device is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid device token")
    device.last_seen_at = datetime.utcnow()
    await db.commit()
    return device
