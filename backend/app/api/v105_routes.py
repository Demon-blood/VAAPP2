from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_device
from app.core.database import get_db
from app.integrations.google_api import GoogleConfigurationError
from app.models.entities import Device
from app.services.autonomous_core import run_core_cycle
from app.services.contact_directory import (
    ingest_device_contact_snapshot,
    list_people_directory,
    sync_google_contact_sources,
)
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


class ContactRelationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = Field(default="", max_length=80)
    person: str = Field(default="", max_length=255)


class DeviceContactRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_id: str = Field(min_length=1, max_length=320)
    display_name: str = Field(default="", max_length=255)
    phones: list[str] = Field(default_factory=list, max_length=50)
    emails: list[str] = Field(default_factory=list, max_length=50)
    organization: str = Field(default="", max_length=255)
    job_title: str = Field(default="", max_length=255)
    department: str = Field(default="", max_length=255)
    nickname: str = Field(default="", max_length=255)
    groups: list[str] = Field(default_factory=list, max_length=50)
    relations: list[ContactRelationRequest] = Field(default_factory=list, max_length=30)
    starred: bool = False


class DeviceContactBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_id: str = Field(min_length=8, max_length=120)
    contacts: list[DeviceContactRequest] = Field(default_factory=list, max_length=150)
    snapshot_complete: bool = False


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


@router.get("/api/relationships/directory")
async def relationship_directory(
    query: str = Query(default="", max_length=160),
    filter_name: str = Query(default="all", alias="filter", max_length=40),
    limit: int = Query(default=1000, ge=1, le=2000),
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return await list_people_directory(
        db,
        query=query,
        filter_name=filter_name,
        limit=limit,
    )


@router.post("/api/relationships/directory/device-contacts")
async def sync_device_contacts_to_directory(
    payload: DeviceContactBatchRequest,
    device: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    contacts = [item.model_dump(mode="json") for item in payload.contacts]
    return await ingest_device_contact_snapshot(
        db,
        device_id=device.id,
        snapshot_id=payload.snapshot_id,
        contacts=contacts,
        snapshot_complete=payload.snapshot_complete,
    )


@router.post("/api/relationships/directory/sync-google")
async def sync_google_contacts_to_directory(
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        return await sync_google_contact_sources(db)
    except GoogleConfigurationError as exc:
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
