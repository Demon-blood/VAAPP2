import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import AuditLog


async def write_audit(
    db: AsyncSession,
    event_type: str,
    *,
    entity_type: str = "",
    entity_id: str = "",
    result: str = "success",
    details: dict[str, Any] | None = None,
) -> None:
    db.add(
        AuditLog(
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            result=result,
            details_json=json.dumps(details or {}, ensure_ascii=False, default=str),
        )
    )
    await db.flush()
