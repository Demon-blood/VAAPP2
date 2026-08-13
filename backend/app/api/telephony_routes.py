from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_device
from app.core.database import get_db
from app.models.entities import Device
from app.models.telephony_entities import TelephonyCall
from app.services import telephony_service

router = APIRouter(tags=["telephony"])


class OutboundCallRequest(BaseModel):
    target: str = Field(min_length=8, max_length=32)
    purpose: str = Field(min_length=3, max_length=2000)
    expected_outcome: str = Field(default="", max_length=2000)
    idempotency_key: str = Field(min_length=8, max_length=255)
    objective_id: int | None = Field(default=None, ge=1)
    max_attempts: int | None = Field(default=None, ge=1, le=5)


async def _verified_twilio_form(
    request: Request,
    db: AsyncSession,
    signature: str | None,
) -> dict[str, str]:
    form = await request.form()
    params = {str(key): str(value) for key, value in form.items()}
    public_url = telephony_service.canonical_webhook_url(request.url.path)
    valid = await telephony_service.validate_twilio_signature(
        db,
        public_url=public_url,
        params=params,
        signature=signature or "",
    )
    if not valid:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid Twilio webhook signature")
    return params


@router.get("/api/telephony/status")
async def telephony_status(
    _: Annotated[Device, Depends(require_device)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    return await telephony_service.telephony_status(db)


@router.get("/api/telephony/calls")
async def telephony_calls(
    _: Annotated[Device, Depends(require_device)],
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(default=100, ge=1, le=250),
):
    return await telephony_service.list_calls(db, limit=limit)


@router.get("/api/telephony/calls/{call_id}")
async def telephony_call_detail(
    call_id: int,
    _: Annotated[Device, Depends(require_device)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    call = await db.get(TelephonyCall, call_id)
    if call is None:
        raise HTTPException(status_code=404, detail="Telephony call not found")
    return await telephony_service.serialize_call(db, call, include_turns=True)


@router.post("/api/telephony/calls")
async def create_telephony_call(
    payload: OutboundCallRequest,
    _: Annotated[Device, Depends(require_device)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    try:
        call = await telephony_service.create_outbound_call(
            db,
            target=payload.target,
            purpose=payload.purpose,
            expected_outcome=payload.expected_outcome,
            idempotency_key=payload.idempotency_key,
            objective_id=payload.objective_id,
            max_attempts=payload.max_attempts,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return await telephony_service.serialize_call(db, call, include_turns=True)


@router.post("/api/telephony/calls/{call_id}/reconcile")
async def reconcile_telephony_call(
    call_id: int,
    _: Annotated[Device, Depends(require_device)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    call = await db.get(TelephonyCall, call_id)
    if call is None:
        raise HTTPException(status_code=404, detail="Telephony call not found")
    call = await telephony_service.reconcile_call(db, call)
    return await telephony_service.serialize_call(db, call, include_turns=True)


@router.post("/api/telephony/reconcile")
async def reconcile_telephony(
    _: Annotated[Device, Depends(require_device)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    return await telephony_service.reconcile_telephony(db)


@router.post("/api/telephony/twilio/incoming", response_class=Response)
async def twilio_incoming(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    x_twilio_signature: str | None = Header(default=None, alias="X-Twilio-Signature"),
):
    params = await _verified_twilio_form(request, db, x_twilio_signature)
    call = await telephony_service.create_inbound_call(db, params)
    xml = await telephony_service.initial_twiml(db, call)
    return Response(content=xml, media_type="application/xml")


@router.post("/api/telephony/twilio/voice/{token}", response_class=Response)
async def twilio_voice(
    token: str,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    x_twilio_signature: str | None = Header(default=None, alias="X-Twilio-Signature"),
):
    params = await _verified_twilio_form(request, db, x_twilio_signature)
    try:
        xml = await telephony_service.process_voice_webhook(db, token, params)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(content=xml, media_type="application/xml")


@router.post("/api/telephony/twilio/turn/{token}/{logical_turn}", response_class=Response)
async def twilio_turn(
    token: str,
    logical_turn: int,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    x_twilio_signature: str | None = Header(default=None, alias="X-Twilio-Signature"),
):
    params = await _verified_twilio_form(request, db, x_twilio_signature)
    try:
        xml = await telephony_service.process_turn_webhook(db, token, logical_turn, params)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(content=xml, media_type="application/xml")


@router.post("/api/telephony/twilio/status/{token}")
async def twilio_status(
    token: str,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    x_twilio_signature: str | None = Header(default=None, alias="X-Twilio-Signature"),
):
    params = await _verified_twilio_form(request, db, x_twilio_signature)
    try:
        call = await telephony_service.process_status_webhook(db, token, params)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True, "call_id": call.id, "status": call.status, "verification_status": call.verification_status}
