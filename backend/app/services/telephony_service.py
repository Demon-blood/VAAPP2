from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta
from typing import Any
from xml.etree import ElementTree as ET

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from twilio.request_validator import RequestValidator

from app.core.crypto import decrypt_text, encrypt_text, hash_token, new_token
from app.core.settings import get_settings
from app.models.entities import Task, VAObjective, VAObjectiveStep, VAOutcomeEvidence
from app.models.telephony_entities import TelephonyCall, TelephonyEvidence, TelephonyTurn
from app.services.audit import write_audit
from app.services.runtime_config import get_runtime_value
from app.services.telephony_ai import analyze_telephony_turn

settings = get_settings()

E164_RE = re.compile(r"^\+[1-9]\d{7,14}$")
PROVIDER_TERMINAL = {"completed", "busy", "failed", "no-answer", "canceled"}
CALL_TERMINAL = {
    "completed_verified",
    "provider_completed_unverified",
    "busy",
    "failed",
    "no-answer",
    "canceled",
    "needs_user",
}
MATERIAL_PATTERNS = (
    "one-time code",
    "one time code",
    "otp",
    "2fa",
    "security code",
    "verification code",
    "password",
    "passcode",
    "pin code",
    "card number",
    "credit card",
    "debit card",
    "bank login",
    "bank password",
    "authorize payment",
    "make a payment",
    "pay now",
    "wire transfer",
    "bank transfer now",
    "sign the contract",
    "accept the contract",
    "agree to the terms",
    "legal settlement",
    "admit liability",
    "medical diagnosis",
    "treatment decision",
    "prescription change",
    "social security number",
    "national register number",
    "national registry number",
    "rijksregisternummer",
    "passport number",
    "identity card number",
    "date of birth",
    "job offer",
    "employment contract",
    "terminate employment",
    "fire the employee",
)


def utcnow() -> datetime:
    return datetime.utcnow()


def normalize_e164(value: str) -> str:
    compact = re.sub(r"[\s().-]+", "", (value or "").strip())
    if not E164_RE.fullmatch(compact):
        raise ValueError("Phone number must use E.164 format, for example +32470123456")
    return compact


def mask_phone(value: str) -> str:
    try:
        number = normalize_e164(value)
    except ValueError:
        return "hidden"
    if len(number) <= 6:
        return "+••••"
    return f"{number[:3]}••••{number[-3:]}"


def _number_hash(value: str) -> str:
    return hashlib.sha256(normalize_e164(value).encode("utf-8")).hexdigest()


def _public_url(path: str) -> str:
    return str(settings.public_base_url).rstrip("/") + path


def canonical_webhook_url(path: str) -> str:
    """Return the exact public URL supplied to Twilio for signature validation."""
    return _public_url(path)


async def _twilio_config(db: AsyncSession) -> dict[str, str]:
    return {
        "enabled": await get_runtime_value(db, "telephony_enabled", "true"),
        "account_sid": await get_runtime_value(db, "twilio_account_sid", ""),
        "auth_token": await get_runtime_value(db, "twilio_auth_token", ""),
        "from_number": await get_runtime_value(db, "twilio_from_number", ""),
        "language": await get_runtime_value(db, "telephony_language", "en-GB"),
        "owner_name": await get_runtime_value(db, "telephony_owner_display_name", ""),
        "max_turns": await get_runtime_value(db, "telephony_max_turns", "10"),
        "max_duration": await get_runtime_value(db, "telephony_max_duration_seconds", "600"),
        "max_attempts": await get_runtime_value(db, "telephony_max_attempts", "3"),
    }


async def telephony_status(db: AsyncSession) -> dict[str, Any]:
    config = await _twilio_config(db)
    configured = bool(config["account_sid"] and config["auth_token"] and config["from_number"])
    ai_configured = bool(
        await get_runtime_value(db, "ai_api_key", "")
        and await get_runtime_value(db, "ai_model", "")
    )
    enabled = config["enabled"].lower() == "true"
    active = int(
        (
            await db.execute(
                select(func.count(TelephonyCall.id)).where(TelephonyCall.status.not_in(CALL_TERMINAL))
            )
        ).scalar_one()
    )
    try:
        from_number = mask_phone(config["from_number"]) if config["from_number"] else ""
    except ValueError:
        from_number = "invalid"
    return {
        "provider": "twilio",
        "enabled": enabled,
        "configured": configured,
        "ai_configured": ai_configured,
        "available": enabled and configured and ai_configured,
        "account_sid_hint": f"…{config['account_sid'][-4:]}" if config["account_sid"] else "",
        "from_number": from_number,
        "language": config["language"] or "en-GB",
        "recording_enabled": False,
        "active_calls": active,
        "incoming_webhook_url": _public_url("/api/telephony/twilio/incoming"),
        "completion_rule": "A provider-completed call is not objective completion without counterparty evidence.",
    }


async def validate_twilio_signature(
    db: AsyncSession,
    *,
    public_url: str,
    params: dict[str, Any],
    signature: str,
) -> bool:
    auth_token = await get_runtime_value(db, "twilio_auth_token", "")
    if not auth_token or not signature:
        return False
    validator = RequestValidator(auth_token)
    return bool(validator.validate(public_url, params, signature))


def _safe_provider_details(params: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "CallStatus",
        "CallDuration",
        "SequenceNumber",
        "Timestamp",
        "Direction",
        "AnsweredBy",
        "SipResponseCode",
        "StirVerstat",
    }
    return {key: str(value)[:500] for key, value in params.items() if key in allowed and value not in (None, "")}


async def _record_evidence(
    db: AsyncSession,
    call: TelephonyCall,
    *,
    event_key: str,
    event_type: str,
    provider_status: str = "",
    external_ref: str = "",
    sequence_number: int | None = None,
    signature_verified: bool = True,
    details: dict[str, Any] | None = None,
) -> TelephonyEvidence:
    existing = (
        await db.execute(select(TelephonyEvidence).where(TelephonyEvidence.event_key == event_key).limit(1))
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    row = TelephonyEvidence(
        call_id=call.id,
        event_key=event_key[:255],
        event_type=event_type[:80],
        provider_status=provider_status[:40],
        external_ref=external_ref[:255],
        sequence_number=sequence_number,
        signature_verified=signature_verified,
        details_json=json.dumps(details or {}, ensure_ascii=False, separators=(",", ":"), default=str),
    )
    db.add(row)
    await db.flush()
    return row


async def _append_turn(
    db: AsyncSession,
    call: TelephonyCall,
    *,
    speaker: str,
    transcript: str,
    provider_ref: str,
    confidence: str = "",
) -> tuple[TelephonyTurn, bool]:
    if provider_ref:
        existing = (
            await db.execute(
                select(TelephonyTurn).where(
                    TelephonyTurn.call_id == call.id,
                    TelephonyTurn.provider_ref == provider_ref,
                ).limit(1)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing, False
    last = (
        await db.execute(select(func.max(TelephonyTurn.turn_index)).where(TelephonyTurn.call_id == call.id))
    ).scalar_one_or_none()
    normalized = transcript.strip()
    row = TelephonyTurn(
        call_id=call.id,
        turn_index=int(last or 0) + 1,
        speaker=speaker[:30],
        transcript_encrypted=encrypt_text(normalized),
        transcript_sha256=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        provider_ref=provider_ref[:255],
        confidence=confidence[:40],
    )
    db.add(row)
    await db.flush()
    return row, True


async def _objective_for_new_call(
    db: AsyncSession,
    *,
    idempotency_key: str,
    direction: str,
    purpose: str,
    expected_outcome: str,
    objective_id: int | None,
) -> tuple[VAObjective, VAObjectiveStep | None]:
    if objective_id:
        objective = await db.get(VAObjective, objective_id)
        if objective is None:
            raise ValueError("The requested VA objective does not exist")
        step = (
            await db.execute(
                select(VAObjectiveStep).where(
                    VAObjectiveStep.objective_id == objective.id,
                    VAObjectiveStep.action_type == "telephony_call",
                    VAObjectiveStep.idempotency_key == f"telephony:{idempotency_key}:step",
                ).limit(1)
            )
        ).scalar_one_or_none()
        if step is None:
            max_position = (
                await db.execute(
                    select(func.max(VAObjectiveStep.position)).where(VAObjectiveStep.objective_id == objective.id)
                )
            ).scalar_one_or_none()
            step = VAObjectiveStep(
                objective_id=objective.id,
                position=int(max_position or 0) + 1,
                action_type="telephony_call",
                idempotency_key=f"telephony:{idempotency_key}:step"[:255],
                status="executing",
                parameters_json=json.dumps(
                    {"direction": direction, "telephony_intent_key": idempotency_key[:255]},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                verification_type="telephony_counterparty_confirmation",
                max_attempts=3,
            )
            db.add(step)
            await db.flush()
        return objective, step

    correlation = f"telephony:{idempotency_key}"[:255]
    existing = (
        await db.execute(select(VAObjective).where(VAObjective.correlation_key == correlation).limit(1))
    ).scalar_one_or_none()
    if existing is not None:
        step = (
            await db.execute(
                select(VAObjectiveStep).where(
                    VAObjectiveStep.objective_id == existing.id,
                    VAObjectiveStep.action_type == "telephony_call",
                ).order_by(VAObjectiveStep.position.asc()).limit(1)
            )
        ).scalar_one_or_none()
        return existing, step

    objective = VAObjective(
        correlation_key=correlation,
        source_type="telephony",
        source_id=idempotency_key[:255],
        title="Handle incoming telephone call" if direction == "inbound" else "Autonomous telephone call",
        goal="Complete the encrypted telephony objective using source-backed counterparty evidence.",
        category="telephony",
        priority="normal",
        risk_level="low",
        status="executing",
        context_json=json.dumps({"direction": direction}, separators=(",", ":")),
        plan_json=json.dumps({"executor": "twilio_programmable_voice", "verification": "counterparty_evidence"}, separators=(",", ":")),
    )
    db.add(objective)
    await db.flush()
    step = VAObjectiveStep(
        objective_id=objective.id,
        position=1,
        action_type="telephony_call",
        idempotency_key=f"{correlation}:step:1"[:255],
        status="executing",
        parameters_json=json.dumps(
            {"direction": direction, "telephony_intent_key": idempotency_key[:255]},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        verification_type="telephony_counterparty_confirmation",
        max_attempts=3,
    )
    db.add(step)
    await db.flush()
    return objective, step


async def _set_objective_state(
    db: AsyncSession,
    call: TelephonyCall,
    status: str,
    *,
    reason: str = "",
    error: str = "",
) -> None:
    if call.objective_id is None:
        return
    objective = await db.get(VAObjective, call.objective_id)
    if objective is None:
        return
    # Telephony may be one step inside a larger objective. Completing the call step
    # must not complete unrelated unfinished work on that objective.
    requested_status = status
    objective.status = status
    if status == "needs_user":
        objective.needs_user_reason = reason[:4000]
        objective.blocked_reason = ""
        objective.user_intervention_count = int(objective.user_intervention_count or 0) + 1
    elif status.startswith("blocked"):
        objective.blocked_reason = reason[:4000]
        objective.needs_user_reason = ""
    else:
        objective.needs_user_reason = ""
        if status not in {"blocked_capability", "blocked_system"}:
            objective.blocked_reason = ""
    if error:
        objective.last_error = error[:8000]
    if call.objective_step_id:
        step = await db.get(VAObjectiveStep, call.objective_step_id)
        if step is not None:
            if status == "completed":
                step.status = "completed"
                step.finished_at = step.finished_at or utcnow()
                step.external_ref = call.external_call_sid or step.external_ref
                remaining = int(
                    (
                        await db.execute(
                            select(func.count(VAObjectiveStep.id)).where(
                                VAObjectiveStep.objective_id == objective.id,
                                VAObjectiveStep.id != step.id,
                                VAObjectiveStep.status != "completed",
                            )
                        )
                    ).scalar_one()
                )
                if remaining:
                    objective.status = "verifying"
                    objective.finished_at = None
                else:
                    objective.status = "completed"
                    objective.finished_at = objective.finished_at or utcnow()
            elif status in {"needs_user", "blocked_capability", "blocked_system", "waiting", "verifying"}:
                step.status = status
                step.last_error = (error or reason)[:4000]
    elif requested_status in {"completed", "cancelled", "failed"}:
        objective.finished_at = objective.finished_at or utcnow()


async def _mark_verified(db: AsyncSession, call: TelephonyCall, summary: str) -> None:
    call.verification_status = "verified"
    call.result_summary_encrypted = encrypt_text(summary[:1600])
    call.status = "completed_verified" if call.provider_status in PROVIDER_TERMINAL else "objective_verified"
    call.next_retry_at = None
    call.needs_user = False
    call.needs_user_reason = ""
    await _set_objective_state(db, call, "completed", reason="Counterparty confirmation satisfied the telephone objective")
    if call.objective_id is not None:
        existing = (
            await db.execute(
                select(VAOutcomeEvidence.id).where(
                    VAOutcomeEvidence.objective_id == call.objective_id,
                    VAOutcomeEvidence.evidence_type == "telephony_counterparty_confirmation",
                    VAOutcomeEvidence.external_ref == (call.external_call_sid or f"telephony:{call.id}"),
                ).limit(1)
            )
        ).scalar_one_or_none()
        if existing is None:
            db.add(
                VAOutcomeEvidence(
                    objective_id=call.objective_id,
                    step_id=call.objective_step_id,
                    evidence_type="telephony_counterparty_confirmation",
                    provider="twilio",
                    external_ref=call.external_call_sid or f"telephony:{call.id}",
                    # Keep transcript-derived content encrypted on TelephonyCall/TelephonyTurn.
                    # The shared objective evidence only records that explicit confirmation exists.
                    details_json=json.dumps(
                        {"call_id": call.id, "counterparty_confirmation": True},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                )
            )


async def _mark_needs_user(db: AsyncSession, call: TelephonyCall, reason: str) -> None:
    call.needs_user = True
    call.needs_user_reason = reason[:4000]
    call.verification_status = "needs_user"
    call.status = "needs_user"
    call.next_retry_at = None
    await _set_objective_state(db, call, "needs_user", reason=reason)
    existing = (
        await db.execute(
            select(Task).where(
                Task.source_type == "telephony_needs_user",
                Task.source_id == str(call.id),
                Task.status.in_(["open", "waiting"]),
            ).limit(1)
        )
    ).scalar_one_or_none()
    if existing is None:
        db.add(
            Task(
                title="Phone call needs your decision",
                description=reason[:1200],
                source_type="telephony_needs_user",
                source_id=str(call.id),
                priority="high",
                requires_approval=True,
            )
        )


async def _call_by_token(db: AsyncSession, token: str, *, lock: bool = False) -> TelephonyCall | None:
    stmt = select(TelephonyCall).where(TelephonyCall.webhook_token_hash == hash_token(token)).limit(1)
    if lock:
        stmt = stmt.with_for_update()
    return (await db.execute(stmt)).scalar_one_or_none()


def _twiml_say_and_hangup(text: str, language: str) -> str:
    root = ET.Element("Response")
    say = ET.SubElement(root, "Say", {"language": language or "en-GB"})
    say.text = text[:900]
    ET.SubElement(root, "Hangup")
    return ET.tostring(root, encoding="unicode", xml_declaration=True)


def _twiml_gather(text: str, language: str, action_url: str) -> str:
    root = ET.Element("Response")
    gather = ET.SubElement(
        root,
        "Gather",
        {
            "input": "speech",
            "action": action_url,
            "method": "POST",
            "actionOnEmptyResult": "true",
            "language": language or "en-GB",
            "speechTimeout": "auto",
            "timeout": "5",
        },
    )
    say = ET.SubElement(gather, "Say", {"language": language or "en-GB"})
    say.text = text[:900]
    return ET.tostring(root, encoding="unicode", xml_declaration=True)


async def _language(db: AsyncSession) -> str:
    return (await get_runtime_value(db, "telephony_language", "en-GB")).strip() or "en-GB"


async def _max_turns(db: AsyncSession) -> int:
    try:
        return max(2, min(20, int(await get_runtime_value(db, "telephony_max_turns", "10"))))
    except ValueError:
        return 10


async def _max_duration_seconds(db: AsyncSession) -> int:
    try:
        return max(60, min(1800, int(await get_runtime_value(db, "telephony_max_duration_seconds", "600"))))
    except ValueError:
        return 600


def _turn_url(call: TelephonyCall, token: str, logical_turn: int) -> str:
    return _public_url(f"/api/telephony/twilio/turn/{token}/{logical_turn}")


async def initial_twiml(db: AsyncSession, call: TelephonyCall) -> str:
    token = decrypt_text(call.webhook_token_encrypted)
    language = await _language(db)
    purpose = decrypt_text(call.purpose_encrypted)
    owner_name = (await get_runtime_value(db, "telephony_owner_display_name", "")).strip()[:120]
    represented = owner_name or "the person I represent"
    if call.direction == "inbound":
        prompt = (
            "Hello. I am an automated virtual assistant. Please tell me what you are calling about. "
            f"I can help with routine coordination or take a message for {represented}."
        )
    else:
        prompt = (
            f"Hello. I am an automated virtual assistant calling on behalf of {represented}. "
            f"I am calling about {purpose[:360]}. If you are the right person to help, please tell me."
        )
    sid = call.external_call_sid or f"local-{call.id}"
    await _append_turn(db, call, speaker="va", transcript=prompt, provider_ref=f"va:{sid}:initial")
    await db.commit()
    return _twiml_gather(prompt, language, _turn_url(call, token, 1))


async def _bind_sid(db: AsyncSession, call: TelephonyCall, sid: str) -> None:
    sid = (sid or "").strip()
    if not sid:
        return
    if call.external_call_sid and call.external_call_sid != sid:
        raise ValueError("Twilio CallSid does not match this durable call intent")
    duplicate = (
        await db.execute(
            select(TelephonyCall.id).where(
                TelephonyCall.external_call_sid == sid,
                TelephonyCall.id != call.id,
            ).limit(1)
        )
    ).scalar_one_or_none()
    if duplicate is not None:
        raise ValueError("Twilio CallSid is already attached to a different call intent")
    call.external_call_sid = sid
    if call.status == "creation_uncertain":
        call.status = "provider_recovered"
        call.failure_reason = ""


async def create_outbound_call(
    db: AsyncSession,
    *,
    target: str,
    purpose: str,
    expected_outcome: str,
    idempotency_key: str,
    objective_id: int | None = None,
    max_attempts: int | None = None,
) -> TelephonyCall:
    target = normalize_e164(target)
    purpose = purpose.strip()
    expected_outcome = (expected_outcome or purpose).strip()
    idempotency_key = idempotency_key.strip()[:255]
    if not idempotency_key:
        raise ValueError("A stable idempotency key is required for an outbound call")
    if not purpose:
        raise ValueError("A call purpose is required")
    if _contains_material_request(f"{purpose}\n{expected_outcome}"):
        raise ValueError(
            "This telephone objective contains a material payment, binding commitment, "
            "medical/legal decision, or authentication/security step. Telephony may gather "
            "information about it, but it cannot perform that material action."
        )

    existing = (
        await db.execute(select(TelephonyCall).where(TelephonyCall.idempotency_key == idempotency_key).limit(1))
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    config = await _twilio_config(db)
    if config["enabled"].lower() != "true":
        raise ValueError("Telephony automation is disabled")
    if not config["account_sid"] or not config["auth_token"] or not config["from_number"]:
        raise ValueError("Twilio is not configured; account SID, auth token, and caller number are required")
    if not await get_runtime_value(db, "ai_api_key", "") or not await get_runtime_value(db, "ai_model", ""):
        raise ValueError("The AI decision engine is not configured; outbound telephony would not have a safe conversation executor")
    from_number = normalize_e164(config["from_number"])
    try:
        configured_max = max(1, min(5, int(config["max_attempts"] or "3")))
    except ValueError:
        configured_max = 3
    attempts = max(1, min(5, int(max_attempts or configured_max)))

    objective, step = await _objective_for_new_call(
        db,
        idempotency_key=idempotency_key,
        direction="outbound",
        purpose=purpose,
        expected_outcome=expected_outcome,
        objective_id=objective_id,
    )
    token = new_token(24)
    call = TelephonyCall(
        idempotency_key=idempotency_key,
        series_key=idempotency_key,
        attempt=1,
        max_attempts=attempts,
        direction="outbound",
        objective_id=objective.id,
        objective_step_id=step.id if step is not None else None,
        webhook_token_hash=hash_token(token),
        webhook_token_encrypted=encrypt_text(token),
        target_hash=_number_hash(target),
        target_encrypted=encrypt_text(target),
        from_number_encrypted=encrypt_text(from_number),
        purpose_encrypted=encrypt_text(purpose[:4000]),
        expected_outcome_encrypted=encrypt_text(expected_outcome[:4000]),
        status="creating",
        verification_status="unverified",
    )
    db.add(call)
    await db.flush()
    if step is not None:
        step.external_ref = f"telephony:{call.id}"
    await write_audit(
        db,
        "telephony_call_intent_created",
        entity_type="telephony_call",
        entity_id=str(call.id),
        details={"direction": "outbound", "provider": "twilio", "attempt": 1},
    )
    # Persist before the irreversible provider POST. A crash after this commit is
    # never repaired by blindly dialing again.
    await db.commit()
    return await _dispatch_outbound_call(db, call)


async def _dispatch_outbound_call(db: AsyncSession, call: TelephonyCall) -> TelephonyCall:
    config = await _twilio_config(db)
    if not config["account_sid"] or not config["auth_token"] or not config["from_number"]:
        call.status = "blocked_capability"
        call.failure_reason = "Twilio provider credentials/caller number are not configured"
        await _set_objective_state(db, call, "blocked_capability", reason=call.failure_reason)
        await db.commit()
        return call

    target = decrypt_text(call.target_encrypted)
    from_number = decrypt_text(call.from_number_encrypted)
    token = decrypt_text(call.webhook_token_encrypted)
    voice_url = _public_url(f"/api/telephony/twilio/voice/{token}")
    status_url = _public_url(f"/api/telephony/twilio/status/{token}")
    endpoint = f"https://api.twilio.com/2010-04-01/Accounts/{config['account_sid']}/Calls.json"
    max_duration = await _max_duration_seconds(db)
    call.status = "dispatching"
    call.started_at = call.started_at or utcnow()
    await db.commit()

    form: dict[str, Any] = {
        "To": target,
        "From": from_number,
        "Url": voice_url,
        "Method": "POST",
        "StatusCallback": status_url,
        "StatusCallbackMethod": "POST",
        # httpx expands list-valued form fields into repeated parameters, which is
        # the representation Twilio documents for multiple progress events.
        "StatusCallbackEvent": ["initiated", "ringing", "answered", "completed"],
        "Timeout": "30",
        "TimeLimit": str(max_duration),
    }
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                endpoint,
                auth=httpx.BasicAuth(config["account_sid"], config["auth_token"]),
                data=form,
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.TimeoutException, httpx.RequestError) as exc:
        call.status = "creation_uncertain"
        call.failure_reason = (
            "Twilio call creation outcome is uncertain; automatic creation retry is blocked. "
            "A signed voice/status webhook can still recover the CallSid if Twilio accepted the request. "
            f"{exc}"
        )[:4000]
        await _set_objective_state(db, call, "verifying", reason="Provider creation outcome is ambiguous", error=call.failure_reason)
        await write_audit(
            db,
            "telephony_call_creation_uncertain",
            entity_type="telephony_call",
            entity_id=str(call.id),
            result="blocked",
            details={"retry_suppressed": True, "provider": "twilio"},
        )
        await db.commit()
        return call
    except httpx.HTTPStatusError as exc:
        call.status = "failed"
        call.provider_status = "failed"
        call.failure_reason = f"Twilio rejected call creation with HTTP {exc.response.status_code}: {exc.response.text[:1000]}"[:4000]
        state = "blocked_capability" if exc.response.status_code in {401, 403} else "blocked_system"
        await _set_objective_state(db, call, state, reason=call.failure_reason, error=call.failure_reason)
        await db.commit()
        return call

    sid = str(payload.get("sid") or "").strip()
    if not sid:
        call.status = "creation_uncertain"
        call.failure_reason = "Twilio returned success without a CallSid; automatic creation retry is blocked."
        await _set_objective_state(db, call, "verifying", reason=call.failure_reason)
    else:
        await _bind_sid(db, call, sid)
        call.provider_status = str(payload.get("status") or "queued").lower()
        call.status = call.provider_status or "queued"
        await _record_evidence(
            db,
            call,
            event_key=f"twilio-create:{sid}",
            event_type="provider_create_ack",
            provider_status=call.provider_status,
            external_ref=sid,
            details={"provider": "twilio", "status": call.provider_status},
        )
    await write_audit(
        db,
        "telephony_call_dispatched",
        entity_type="telephony_call",
        entity_id=str(call.id),
        result="success" if sid else "blocked",
        details={"provider": "twilio", "external_ref": sid, "attempt": call.attempt},
    )
    await db.commit()
    return call


async def create_inbound_call(db: AsyncSession, params: dict[str, Any]) -> TelephonyCall:
    sid = str(params.get("CallSid") or "").strip()
    caller_raw = str(params.get("From") or "").strip()
    called_raw = str(params.get("To") or "").strip()
    try:
        caller = normalize_e164(caller_raw)
    except ValueError:
        caller = caller_raw[:255] or "hidden"
    try:
        called = normalize_e164(called_raw)
    except ValueError:
        called = called_raw[:255] or "hidden"
    if not sid:
        raise ValueError("Twilio inbound call is missing CallSid")
    existing = (
        await db.execute(select(TelephonyCall).where(TelephonyCall.external_call_sid == sid).limit(1))
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    idempotency = f"twilio:inbound:{sid}"
    purpose = "Understand why the caller is calling and handle routine coordination or take a clear message."
    outcome = "Resolve routine coordination when safe, or capture the caller's request without making a material commitment."
    objective, step = await _objective_for_new_call(
        db,
        idempotency_key=idempotency,
        direction="inbound",
        purpose=purpose,
        expected_outcome=outcome,
        objective_id=None,
    )
    token = new_token(24)
    call = TelephonyCall(
        idempotency_key=idempotency,
        series_key=idempotency,
        attempt=1,
        max_attempts=1,
        direction="inbound",
        objective_id=objective.id,
        objective_step_id=step.id if step is not None else None,
        external_call_sid=sid,
        webhook_token_hash=hash_token(token),
        webhook_token_encrypted=encrypt_text(token),
        target_hash=hashlib.sha256(caller.casefold().encode("utf-8")).hexdigest(),
        target_encrypted=encrypt_text(caller),
        from_number_encrypted=encrypt_text(called),
        purpose_encrypted=encrypt_text(purpose),
        expected_outcome_encrypted=encrypt_text(outcome),
        status="in_progress",
        provider_status=str(params.get("CallStatus") or "in-progress").lower(),
        verification_status="unverified",
        started_at=utcnow(),
        answered_at=utcnow(),
    )
    db.add(call)
    await db.flush()
    if step is not None:
        step.external_ref = sid
    await _record_evidence(
        db,
        call,
        event_key=f"twilio-inbound:{sid}",
        event_type="incoming_call",
        provider_status=call.provider_status,
        external_ref=sid,
        details={"provider": "twilio", "direction": "inbound"},
    )
    await db.commit()
    return call


def _contains_material_request(text: str) -> bool:
    lower = text.casefold()
    return any(pattern in lower for pattern in MATERIAL_PATTERNS)


async def _turn_history(db: AsyncSession, call: TelephonyCall) -> list[dict[str, str]]:
    rows = list(
        (
            await db.execute(
                select(TelephonyTurn)
                .where(TelephonyTurn.call_id == call.id)
                .order_by(TelephonyTurn.turn_index.desc())
                .limit(16)
            )
        ).scalars()
    )
    rows.reverse()
    return [
        {"speaker": row.speaker, "text": decrypt_text(row.transcript_encrypted)[:1500]}
        for row in rows
    ]


async def process_voice_webhook(db: AsyncSession, token: str, params: dict[str, Any]) -> str:
    call = await _call_by_token(db, token, lock=True)
    if call is None:
        raise ValueError("Unknown or expired telephony call token")
    await _bind_sid(db, call, str(params.get("CallSid") or ""))
    if call.provider_status not in PROVIDER_TERMINAL:
        call.provider_status = str(params.get("CallStatus") or call.provider_status or "in-progress").lower()
        call.status = "in_progress" if call.verification_status != "verified" else "objective_verified"
    call.answered_at = call.answered_at or utcnow()
    await _record_evidence(
        db,
        call,
        event_key=f"twilio-voice:{call.external_call_sid or call.id}",
        event_type="voice_webhook",
        provider_status=call.provider_status,
        external_ref=call.external_call_sid or "",
        details={"provider": "twilio", "voice_control_started": True},
    )
    await db.commit()
    return await initial_twiml(db, call)


async def process_turn_webhook(
    db: AsyncSession,
    token: str,
    logical_turn: int,
    params: dict[str, Any],
) -> str:
    call = await _call_by_token(db, token, lock=True)
    if call is None:
        raise ValueError("Unknown or expired telephony call token")
    await _bind_sid(db, call, str(params.get("CallSid") or ""))
    sid = call.external_call_sid or f"local-{call.id}"
    language = await _language(db)
    logical_turn = max(1, min(99, int(logical_turn)))
    provider_ref = f"speech:{sid}:{logical_turn}"
    existing = (
        await db.execute(
            select(TelephonyTurn).where(
                TelephonyTurn.call_id == call.id,
                TelephonyTurn.provider_ref == provider_ref,
            ).limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        response_turn = (
            await db.execute(
                select(TelephonyTurn).where(
                    TelephonyTurn.call_id == call.id,
                    TelephonyTurn.provider_ref == f"va:{sid}:{logical_turn}",
                ).limit(1)
            )
        ).scalar_one_or_none()
        if response_turn is not None:
            speech = decrypt_text(response_turn.transcript_encrypted)
            if call.needs_user or call.verification_status == "verified" or call.status in {"ending_unverified", "blocked_system"}:
                return _twiml_say_and_hangup(speech, language)
            return _twiml_gather(speech, language, _turn_url(call, token, logical_turn + 1))
        # The counterparty turn was durably committed (for example by AI usage
        # accounting) but the VA response was not. Re-run only the decision step;
        # never count or duplicate the caller turn.
        speech_result = decrypt_text(existing.transcript_encrypted)
        confidence = existing.confidence
        created = False
    else:
        speech_result = str(params.get("SpeechResult") or "").strip()
        confidence = str(params.get("Confidence") or "")[:40]
        _, created = await _append_turn(
            db,
            call,
            speaker="counterparty",
            transcript=speech_result,
            provider_ref=provider_ref,
            confidence=confidence,
        )
    if created:
        call.turn_count += 1
        if not speech_result:
            call.empty_turn_count += 1
    max_turns = await _max_turns(db)
    max_duration = await _max_duration_seconds(db)
    if call.started_at is not None and utcnow() - call.started_at >= timedelta(seconds=max_duration):
        response = "I have reached my call time limit. I will end the call now and follow up later if needed."
        await _append_turn(db, call, speaker="va", transcript=response, provider_ref=f"va:{sid}:{logical_turn}")
        call.status = "ending_unverified"
        if call.direction == "outbound" and call.attempt < call.max_attempts:
            call.next_retry_at = utcnow() + timedelta(hours=24)
            await _set_objective_state(db, call, "waiting", reason="Maximum call duration reached; a bounded later retry is scheduled")
        else:
            await _set_objective_state(db, call, "verifying", reason="Maximum call duration reached without verified objective completion")
        await db.commit()
        return _twiml_say_and_hangup(response, language)

    if _contains_material_request(speech_result):
        response = (
            "That part requires the person I represent to handle it directly. "
            "I will pass this on and end the call now."
        )
        await _append_turn(db, call, speaker="va", transcript=response, provider_ref=f"va:{sid}:{logical_turn}")
        await _mark_needs_user(db, call, "The counterparty requested a material payment, commitment, medical/legal decision, or authentication/security step.")
        await db.commit()
        return _twiml_say_and_hangup(response, language)

    if not speech_result:
        if call.empty_turn_count >= 2 or call.turn_count >= max_turns:
            response = "I still cannot hear a response. I will end the call now and follow up later if appropriate."
            await _append_turn(db, call, speaker="va", transcript=response, provider_ref=f"va:{sid}:{logical_turn}")
            call.status = "ending_unverified"
            if call.direction == "outbound" and call.attempt < call.max_attempts:
                call.next_retry_at = utcnow() + timedelta(hours=24)
                await _set_objective_state(db, call, "waiting", reason="No speech was received; a bounded later retry is scheduled")
            else:
                await _set_objective_state(db, call, "verifying", reason="Call ended without counterparty speech")
            await db.commit()
            return _twiml_say_and_hangup(response, language)
        response = "I did not catch that. Could you please repeat it?"
        await _append_turn(db, call, speaker="va", transcript=response, provider_ref=f"va:{sid}:{logical_turn}")
        await db.commit()
        return _twiml_gather(response, language, _turn_url(call, token, logical_turn + 1))

    history = await _turn_history(db, call)
    payload = {
        "direction": call.direction,
        "purpose": decrypt_text(call.purpose_encrypted),
        "expected_outcome": decrypt_text(call.expected_outcome_encrypted),
        "conversation": history,
        "attempt": call.attempt,
        "max_attempts": call.max_attempts,
        "remaining_turns": max(0, max_turns - call.turn_count),
    }
    try:
        decision = await analyze_telephony_turn(db, payload)
    except Exception as exc:
        response = (
            "I am unable to continue this call safely right now. "
            "I will have the person I represent follow up another way."
        )
        await _append_turn(db, call, speaker="va", transcript=response, provider_ref=f"va:{sid}:{logical_turn}")
        call.status = "blocked_system"
        call.failure_reason = f"Voice decision engine unavailable: {exc}"[:4000]
        if call.direction == "outbound" and call.attempt < call.max_attempts:
            call.next_retry_at = utcnow() + timedelta(hours=2)
        await _set_objective_state(db, call, "blocked_system", reason="Voice decision engine was unavailable", error=call.failure_reason)
        await db.commit()
        return _twiml_say_and_hangup(response, language)

    response = str(decision.get("speech") or "Thank you.").strip() or "Thank you."
    await _append_turn(db, call, speaker="va", transcript=response, provider_ref=f"va:{sid}:{logical_turn}")
    summary = str(decision.get("outcome_summary") or "")[:1600]
    if summary:
        call.result_summary_encrypted = encrypt_text(summary)

    if decision.get("needs_user"):
        reason = str(decision.get("needs_user_reason") or "The call reached a material decision or authentication step.")
        await _mark_needs_user(db, call, reason)
        await db.commit()
        return _twiml_say_and_hangup(response, language)

    if decision.get("objective_satisfied") and decision.get("counterparty_confirmed"):
        await _mark_verified(db, call, summary or "The counterparty explicitly confirmed the requested outcome during the call.")
        await db.commit()
        return _twiml_say_and_hangup(response, language)

    if decision.get("retry_call") and call.direction == "outbound" and call.attempt < call.max_attempts:
        hours = max(1, min(168, int(decision.get("retry_after_hours") or 24)))
        call.next_retry_at = utcnow() + timedelta(hours=hours)

    should_end = bool(decision.get("end_call")) or call.turn_count >= max_turns
    if should_end:
        call.status = "ending_unverified"
        if call.next_retry_at is not None:
            await _set_objective_state(db, call, "waiting", reason="Telephone objective is not yet verified; bounded follow-up is scheduled")
        else:
            await _set_objective_state(db, call, "verifying", reason="Call ended; objective remains unverified until source-backed confirmation exists")
        await db.commit()
        return _twiml_say_and_hangup(response, language)

    call.status = "in_progress"
    await db.commit()
    return _twiml_gather(response, language, _turn_url(call, token, logical_turn + 1))


async def process_status_webhook(db: AsyncSession, token: str, params: dict[str, Any]) -> TelephonyCall:
    call = await _call_by_token(db, token, lock=True)
    if call is None:
        raise ValueError("Unknown or expired telephony call token")
    sid = str(params.get("CallSid") or "").strip()
    await _bind_sid(db, call, sid)
    provider_status = str(params.get("CallStatus") or "").lower().strip()
    try:
        sequence = int(params.get("SequenceNumber"))
    except (TypeError, ValueError):
        sequence = None
    event_key = f"twilio-status:{sid or call.id}:{sequence if sequence is not None else 'na'}:{provider_status}"
    await _record_evidence(
        db,
        call,
        event_key=event_key,
        event_type="status_callback",
        provider_status=provider_status,
        external_ref=sid,
        sequence_number=sequence,
        signature_verified=True,
        details=_safe_provider_details(params),
    )
    if sequence is not None and sequence < call.last_sequence_number:
        await db.commit()
        return call
    if sequence is not None:
        call.last_sequence_number = sequence
    await _apply_provider_status(db, call, provider_status, params)
    await db.commit()
    return call


async def _apply_provider_status(
    db: AsyncSession,
    call: TelephonyCall,
    provider_status: str,
    details: dict[str, Any] | None = None,
) -> None:
    provider_status = provider_status.lower().strip()
    if not provider_status:
        return
    call.provider_status = provider_status
    now = utcnow()
    # A later provider callback must never erase a material decision/authentication
    # blocker that already handed control back to the user. Provider completion and
    # objective ownership are separate facts.
    if call.needs_user or call.verification_status == "needs_user":
        call.status = "needs_user"
        if provider_status in PROVIDER_TERMINAL:
            call.ended_at = call.ended_at or now
        return
    if provider_status in {"initiated", "queued"}:
        if call.verification_status != "verified":
            call.status = provider_status
        call.started_at = call.started_at or now
    elif provider_status == "ringing":
        if call.verification_status != "verified":
            call.status = "ringing"
        call.started_at = call.started_at or now
    elif provider_status == "in-progress":
        call.answered_at = call.answered_at or now
        if call.verification_status != "verified":
            call.status = "in_progress"
    elif provider_status == "completed":
        call.ended_at = call.ended_at or now
        if call.verification_status == "verified":
            call.status = "completed_verified"
            await _set_objective_state(db, call, "completed", reason="Provider call ended after counterparty-verified objective completion")
        else:
            call.status = "provider_completed_unverified"
            if call.next_retry_at is not None:
                await _set_objective_state(db, call, "waiting", reason="Provider completed the call, but the objective is unverified and a bounded retry is scheduled")
            else:
                await _set_objective_state(
                    db,
                    call,
                    "verifying",
                    reason="Twilio completed only proves a connected call; it does not prove the counterparty satisfied the objective",
                )
    elif provider_status in {"busy", "no-answer"}:
        call.status = provider_status
        call.ended_at = call.ended_at or now
        if call.direction == "outbound" and call.attempt < call.max_attempts:
            call.next_retry_at = now + timedelta(hours=2)
            await _set_objective_state(db, call, "waiting", reason=f"Twilio reported {provider_status}; bounded retry scheduled")
        else:
            await _set_objective_state(
                db,
                call,
                "blocked_capability",
                reason=f"Bounded telephone attempts were exhausted without reaching the counterparty ({provider_status})",
            )
    elif provider_status == "canceled":
        call.status = "canceled"
        call.ended_at = call.ended_at or now
        call.next_retry_at = None
        await _set_objective_state(db, call, "cancelled", reason="Twilio call was cancelled")
    elif provider_status == "failed":
        call.status = "failed"
        call.ended_at = call.ended_at or now
        call.next_retry_at = None
        reason = "Twilio reported that the call could not be completed"
        if details and details.get("SipResponseCode"):
            reason += f" (SIP {details.get('SipResponseCode')})"
        call.failure_reason = reason
        await _set_objective_state(db, call, "blocked_system", reason=reason, error=reason)


async def reconcile_call(db: AsyncSession, call: TelephonyCall) -> TelephonyCall:
    if not call.external_call_sid:
        if call.status == "creating" and call.updated_at < utcnow() - timedelta(minutes=15):
            call.status = "creation_uncertain"
            call.failure_reason = "Call creation was interrupted before a Twilio CallSid was recorded; blind retry is blocked."
            await _set_objective_state(db, call, "verifying", reason=call.failure_reason)
            await db.commit()
        return call
    config = await _twilio_config(db)
    if not config["account_sid"] or not config["auth_token"]:
        return call
    endpoint = f"https://api.twilio.com/2010-04-01/Accounts/{config['account_sid']}/Calls/{call.external_call_sid}.json"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(endpoint, auth=httpx.BasicAuth(config["account_sid"], config["auth_token"]))
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        call.failure_reason = f"Twilio reconciliation failed: {exc}"[:4000]
        await db.commit()
        return call
    status = str(payload.get("status") or call.provider_status).lower()
    await _record_evidence(
        db,
        call,
        event_key=f"twilio-reconcile:{call.external_call_sid}:{status}:{payload.get('date_updated') or utcnow().isoformat()}",
        event_type="provider_reconcile",
        provider_status=status,
        external_ref=call.external_call_sid,
        details={
            "status": status,
            "duration": payload.get("duration"),
            "start_time": payload.get("start_time"),
            "end_time": payload.get("end_time"),
        },
    )
    await _apply_provider_status(db, call, status, payload)
    await db.commit()
    return call


async def _create_retry_call(db: AsyncSession, parent: TelephonyCall) -> TelephonyCall | None:
    next_attempt = parent.attempt + 1
    if next_attempt > parent.max_attempts or parent.needs_user or parent.verification_status == "verified":
        parent.next_retry_at = None
        await db.commit()
        return None
    existing = (
        await db.execute(
            select(TelephonyCall).where(
                TelephonyCall.series_key == parent.series_key,
                TelephonyCall.attempt == next_attempt,
            ).limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        parent.next_retry_at = None
        await db.commit()
        return existing
    token = new_token(24)
    retry_key = "telephony-retry:" + hashlib.sha256(f"{parent.series_key}:{next_attempt}".encode("utf-8")).hexdigest()
    child = TelephonyCall(
        idempotency_key=retry_key,
        series_key=parent.series_key,
        attempt=next_attempt,
        max_attempts=parent.max_attempts,
        provider=parent.provider,
        direction=parent.direction,
        objective_id=parent.objective_id,
        objective_step_id=parent.objective_step_id,
        parent_call_id=parent.id,
        webhook_token_hash=hash_token(token),
        webhook_token_encrypted=encrypt_text(token),
        target_hash=parent.target_hash,
        target_encrypted=parent.target_encrypted,
        from_number_encrypted=parent.from_number_encrypted,
        purpose_encrypted=parent.purpose_encrypted,
        expected_outcome_encrypted=parent.expected_outcome_encrypted,
        status="creating",
        verification_status="unverified",
    )
    db.add(child)
    parent.next_retry_at = None
    await db.flush()
    await write_audit(
        db,
        "telephony_retry_intent_created",
        entity_type="telephony_call",
        entity_id=str(child.id),
        details={"parent_call_id": parent.id, "attempt": next_attempt, "max_attempts": parent.max_attempts},
    )
    await db.commit()
    return await _dispatch_outbound_call(db, child)


async def reconcile_telephony(db: AsyncSession) -> dict[str, int]:
    result = {"reconciled": 0, "creation_uncertain": 0, "retries_started": 0}
    stale_cutoff = utcnow() - timedelta(minutes=15)
    stale = list(
        (
            await db.execute(
                select(TelephonyCall).where(
                    TelephonyCall.status.in_(["creating", "dispatching"]),
                    TelephonyCall.external_call_sid.is_(None),
                    TelephonyCall.updated_at < stale_cutoff,
                ).limit(100)
            )
        ).scalars()
    )
    for call in stale:
        call.status = "creation_uncertain"
        call.failure_reason = "Call creation was interrupted before provider identity was recorded; automatic retry is blocked."
        await _set_objective_state(db, call, "verifying", reason=call.failure_reason)
        result["creation_uncertain"] += 1
    if stale:
        await db.commit()

    active = list(
        (
            await db.execute(
                select(TelephonyCall).where(
                    TelephonyCall.external_call_sid.is_not(None),
                    TelephonyCall.provider_status.not_in(PROVIDER_TERMINAL),
                ).order_by(TelephonyCall.id.asc()).limit(50)
            )
        ).scalars()
    )
    for call in active:
        await reconcile_call(db, call)
        result["reconciled"] += 1

    due = list(
        (
            await db.execute(
                select(TelephonyCall).where(
                    TelephonyCall.direction == "outbound",
                    TelephonyCall.next_retry_at.is_not(None),
                    TelephonyCall.next_retry_at <= utcnow(),
                    TelephonyCall.attempt < TelephonyCall.max_attempts,
                    TelephonyCall.needs_user.is_(False),
                    TelephonyCall.verification_status != "verified",
                ).order_by(TelephonyCall.next_retry_at.asc()).limit(20)
            )
        ).scalars()
    )
    for parent in due:
        child = await _create_retry_call(db, parent)
        if child is not None:
            result["retries_started"] += 1
    return result


async def list_calls(db: AsyncSession, *, limit: int = 100) -> list[dict[str, Any]]:
    rows = list(
        (
            await db.execute(select(TelephonyCall).order_by(TelephonyCall.id.desc()).limit(max(1, min(limit, 250))))
        ).scalars()
    )
    return [await serialize_call(db, row, include_turns=False) for row in rows]


async def serialize_call(db: AsyncSession, call: TelephonyCall, *, include_turns: bool) -> dict[str, Any]:
    target = decrypt_text(call.target_encrypted)
    purpose = decrypt_text(call.purpose_encrypted)
    expected = decrypt_text(call.expected_outcome_encrypted)
    summary = decrypt_text(call.result_summary_encrypted) if call.result_summary_encrypted else ""
    result: dict[str, Any] = {
        "id": call.id,
        "direction": call.direction,
        "provider": call.provider,
        "target": target if include_turns else mask_phone(target),
        "target_masked": mask_phone(target),
        "purpose": purpose,
        "expected_outcome": expected,
        "attempt": call.attempt,
        "max_attempts": call.max_attempts,
        "status": call.status,
        "provider_status": call.provider_status,
        "verification_status": call.verification_status,
        "provider_completed": call.provider_status == "completed",
        "objective_verified": call.verification_status == "verified",
        "objective_id": call.objective_id,
        "external_call_sid": call.external_call_sid,
        "needs_user": call.needs_user,
        "needs_user_reason": call.needs_user_reason,
        "result_summary": summary,
        "failure_reason": call.failure_reason,
        "next_retry_at": call.next_retry_at.isoformat() if call.next_retry_at else None,
        "started_at": call.started_at.isoformat() if call.started_at else None,
        "answered_at": call.answered_at.isoformat() if call.answered_at else None,
        "ended_at": call.ended_at.isoformat() if call.ended_at else None,
        "created_at": call.created_at.isoformat(),
    }
    if include_turns:
        turns = list(
            (
                await db.execute(
                    select(TelephonyTurn).where(TelephonyTurn.call_id == call.id).order_by(TelephonyTurn.turn_index.asc())
                )
            ).scalars()
        )
        result["turns"] = [
            {
                "index": row.turn_index,
                "speaker": row.speaker,
                "text": decrypt_text(row.transcript_encrypted),
                "confidence": row.confidence,
                "occurred_at": row.occurred_at.isoformat(),
            }
            for row in turns
        ]
        evidence = list(
            (
                await db.execute(
                    select(TelephonyEvidence)
                    .where(TelephonyEvidence.call_id == call.id)
                    .order_by(TelephonyEvidence.id.asc())
                )
            ).scalars()
        )
        result["evidence"] = [
            {
                "event_type": row.event_type,
                "provider_status": row.provider_status,
                "external_ref": row.external_ref,
                "sequence_number": row.sequence_number,
                "signature_verified": row.signature_verified,
                "details": json.loads(row.details_json or "{}"),
                "created_at": row.created_at.isoformat(),
            }
            for row in evidence
        ]
    return result
