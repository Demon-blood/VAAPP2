from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_device
from app.core.database import get_db
from app.models.entities import Device
from app.services.autonomous_core import run_core_cycle
from app.services.standing_authority import list_standing_authorities, set_standing_authority

router = APIRouter()


class StandingAuthorityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    max_risk: str | None = Field(default=None, max_length=20)
    max_actions_per_day: int | None = Field(default=None, ge=1, le=200)
    max_amount_eur: str | None = Field(default=None, max_length=32)
    counterparties: list[str] | None = Field(default=None, max_length=100)
    expires_at: str | None = Field(default=None, max_length=80)


@router.get("/api/va/authorities")
async def standing_authorities(
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    return await list_standing_authorities(db)


@router.put("/api/va/authorities/{policy_key}")
async def update_standing_authority(
    policy_key: str,
    payload: StandingAuthorityRequest,
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        result = await set_standing_authority(
            db,
            policy_key,
            payload.model_dump(mode="json", exclude_none=True),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # If authority was broadened, immediately re-run the VA state machine so any
    # matching Needs You item can resume. Normal executor/provider verification
    # still controls completion.
    if result.get("enabled"):
        result["core_cycle"] = await run_core_cycle(db)
    return result
