from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_device
from app.core.database import get_db
from app.models.entities import Device
from app.services.autonomous_core import run_core_cycle
from app.services.relationship_preferences import (
    get_relationship_communication_preferences,
    set_relationship_communication_preferences,
)
from app.services.relationship_style_learning import (
    get_relationship_learned_style,
    refresh_relationship_style,
)
from app.services.specific_authorization import authorize_specific_objective, decline_specific_objective

router = APIRouter()


class ObjectiveDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_fingerprint: str = Field(min_length=64, max_length=64)
    reason: str = Field(default="", max_length=500)


class RelationshipCommunicationPreferencesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    language: str = "auto"
    tone: str = "neutral"
    formality: str = "auto"
    greeting_style: str = "auto"
    signoff_style: str = "auto"
    verbosity: str = "normal"
    preferred_channel: str = "auto"
    routine_auto_send: bool | None = None
    approval_topics: list[str] = Field(default_factory=list, max_length=20)
    relationship_category: str = "other"
    instructions: str = Field(default="", max_length=2000)
    examples: list[str] = Field(default_factory=list, max_length=5)
    channel_aliases: dict[str, list[str]] = Field(default_factory=dict)
    learn_from_history: bool = False


@router.post("/api/va/objectives/{objective_id}/authorize")
async def authorize_va_objective(
    objective_id: int,
    payload: ObjectiveDecisionRequest,
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        result = await authorize_specific_objective(
            db,
            objective_id,
            action_fingerprint=payload.action_fingerprint,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    # Resume the state machine in the same request. This does not mark anything
    # complete; executor/provider verification still controls terminal outcome.
    result["core_cycle"] = await run_core_cycle(db)
    return result


@router.post("/api/va/objectives/{objective_id}/decline")
async def decline_va_objective(
    objective_id: int,
    payload: ObjectiveDecisionRequest,
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        return await decline_specific_objective(
            db,
            objective_id,
            action_fingerprint=payload.action_fingerprint,
            reason=payload.reason,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/api/relationships/{relationship_id}/communication-preferences")
async def relationship_communication_preferences(
    relationship_id: int,
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        result = await get_relationship_communication_preferences(db, relationship_id)
        result["learned_style"] = await get_relationship_learned_style(db, relationship_id)
        return result
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/api/relationships/{relationship_id}/communication-preferences")
async def update_relationship_communication_preferences(
    relationship_id: int,
    payload: RelationshipCommunicationPreferencesRequest,
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        result = await set_relationship_communication_preferences(
            db,
            relationship_id,
            payload.model_dump(mode="json"),
        )
        result["learned_style"] = await refresh_relationship_style(db, relationship_id)
        return result
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/api/relationships/{relationship_id}/communication-style/relearn")
async def relearn_relationship_communication_style(
    relationship_id: int,
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        return await refresh_relationship_style(db, relationship_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
