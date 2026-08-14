from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_device
from app.core.database import get_db
from app.models.entities import Device
from app.models.fulfillment_entities import FulfillmentRequest
from app.services import fulfillment_service

router = APIRouter(tags=["fulfillment"])


class FulfillmentProviderRequest(BaseModel):
    slug: str = Field(min_length=2, max_length=120, pattern=r"^[A-Za-z0-9][A-Za-z0-9 _-]*$")
    name: str = Field(min_length=1, max_length=255)
    provider_type: Literal["merchant", "airline", "hotel", "travel", "carrier", "service", "general"] = "merchant"
    browser_portal_id: int | None = Field(default=None, ge=1)
    account_scope: Literal["personal", "pro"] = "personal"
    support_phone: str = Field(default="", max_length=32)
    recipe: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class FulfillmentCreateRequest(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=255)
    request_type: Literal["purchase", "travel", "logistics", "return", "refund", "cancel", "customer_service"]
    title: str = Field(min_length=1, max_length=2000)
    goal: str = Field(min_length=1, max_length=8000)
    provider_id: int | None = Field(default=None, ge=1)
    account_scope: Literal["personal", "pro"] = "personal"
    amount: Decimal | None = Field(default=None, ge=0)
    currency: str = Field(default="EUR", min_length=3, max_length=3)
    details: dict[str, Any] = Field(default_factory=dict)
    priority: Literal["low", "normal", "high", "urgent"] = "normal"


@router.get("/api/fulfillment/status")
async def fulfillment_status(
    _: Annotated[Device, Depends(require_device)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    return await fulfillment_service.fulfillment_status(db)


@router.get("/api/fulfillment/provider-templates")
async def fulfillment_provider_templates(
    _: Annotated[Device, Depends(require_device)],
):
    return fulfillment_service.provider_templates()


@router.get("/api/fulfillment/providers")
async def fulfillment_providers(
    _: Annotated[Device, Depends(require_device)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    return await fulfillment_service.list_providers(db)


@router.post("/api/fulfillment/providers")
async def configure_fulfillment_provider(
    payload: FulfillmentProviderRequest,
    _: Annotated[Device, Depends(require_device)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    try:
        row = await fulfillment_service.upsert_provider(
            db,
            slug=payload.slug,
            name=payload.name,
            provider_type=payload.provider_type,
            browser_portal_id=payload.browser_portal_id,
            account_scope=payload.account_scope,
            support_phone=payload.support_phone,
            recipe=payload.recipe,
            enabled=payload.enabled,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "id": row.id,
        "slug": row.slug,
        "name": row.name,
        "provider_type": row.provider_type,
        "browser_portal_id": row.browser_portal_id,
        "account_scope": row.account_scope,
        "enabled": row.enabled,
    }


@router.get("/api/fulfillment/requests")
async def fulfillment_requests(
    _: Annotated[Device, Depends(require_device)],
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(default=200, ge=1, le=500),
    status: str | None = Query(default=None, max_length=40),
):
    return await fulfillment_service.list_requests(db, limit=limit, status=status)


@router.get("/api/fulfillment/requests/{request_id}")
async def fulfillment_request_detail(
    request_id: int,
    _: Annotated[Device, Depends(require_device)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    row = await db.get(FulfillmentRequest, request_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Fulfillment request not found")
    return await fulfillment_service.serialize_request(db, row, include_actions=True)


@router.post("/api/fulfillment/requests")
async def create_fulfillment_request(
    payload: FulfillmentCreateRequest,
    _: Annotated[Device, Depends(require_device)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    try:
        row = await fulfillment_service.create_request(
            db,
            idempotency_key=payload.idempotency_key,
            request_type=payload.request_type,
            title=payload.title,
            goal=payload.goal,
            provider_id=payload.provider_id,
            account_scope=payload.account_scope,
            amount=payload.amount,
            currency=payload.currency,
            details=payload.details,
            priority=payload.priority,
        )
        row = await fulfillment_service.run_request(db, row)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return await fulfillment_service.serialize_request(db, row, include_actions=True)


@router.post("/api/fulfillment/requests/{request_id}/run")
async def run_fulfillment_request(
    request_id: int,
    _: Annotated[Device, Depends(require_device)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    row = await db.get(FulfillmentRequest, request_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Fulfillment request not found")
    row.next_action_at = fulfillment_service.utcnow()
    row = await fulfillment_service.run_request(db, row)
    return await fulfillment_service.serialize_request(db, row, include_actions=True)


@router.post("/api/fulfillment/requests/{request_id}/authorize-payment")
async def authorize_fulfillment_payment(
    request_id: int,
    _: Annotated[Device, Depends(require_device)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    row = await db.get(FulfillmentRequest, request_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Fulfillment request not found")
    try:
        row = await fulfillment_service.authorize_request(db, row)
        row = await fulfillment_service.run_request(db, row)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return await fulfillment_service.serialize_request(db, row, include_actions=True)


@router.post("/api/fulfillment/requests/{request_id}/cancel")
async def cancel_fulfillment_request(
    request_id: int,
    _: Annotated[Device, Depends(require_device)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    row = await db.get(FulfillmentRequest, request_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Fulfillment request not found")
    try:
        row = await fulfillment_service.cancel_request(db, row)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return await fulfillment_service.serialize_request(db, row, include_actions=True)


@router.post("/api/fulfillment/reconcile")
async def reconcile_fulfillment(
    _: Annotated[Device, Depends(require_device)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    return await fulfillment_service.reconcile_fulfillment(db, limit=200)
