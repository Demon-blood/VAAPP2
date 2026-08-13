from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.ai_client import (
    AIQuotaDeferred,
    _budget_allows,
    _is_groq_strict,
    _record_rate_limit,
    _record_response_usage,
    ensure_ai_configured,
)
from app.services.runtime_config import get_runtime_value


TELEPHONY_SYSTEM_PROMPT = """You are the real-time voice decision engine for a private autonomous virtual assistant.
You are speaking on a telephone call. Return only the requested JSON object.

Rules:
- The caller must always know they are speaking with an automated virtual assistant. Never pretend to be human.
- Keep speech concise, natural, and suitable for a phone call. Ask one question at a time.
- Pursue the supplied purpose and expected outcome, but never invent a confirmation, booking, payment, cancellation,
  identity check, agreement, or external result.
- objective_satisfied=true is allowed only when the counterparty's actual words provide clear source-backed confirmation
  of the expected outcome. Set counterparty_confirmed=true only for an explicit confirmation.
- Never accept or create a contract, purchase, payment, bank transfer, debt admission, legal settlement, medical decision,
  employment commitment, credential/security change, identity verification, or disclosure of passwords, PINs, OTP/2FA
  codes, payment-card details, bank credentials, or similarly sensitive authentication material.
- If a material decision, authentication step, payment, binding commitment, or sensitive personal disclosure is required,
  set needs_user=true, end_call=true, and explain briefly that the person represented must handle that part directly.
- You may autonomously gather routine information, take a message, ask about availability, chase a routine status, obtain a
  reference number, or coordinate low-risk logistics when no material commitment is created.
- Do not reveal personal information that is not explicitly present in the supplied context.
- retry_call may be true only when a later retry is genuinely useful. Use a bounded delay of at least one hour.
- outcome_summary must state only facts actually supported by this conversation.
"""

TELEPHONY_DECISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "speech": {"type": "string"},
        "end_call": {"type": "boolean"},
        "needs_user": {"type": "boolean"},
        "needs_user_reason": {"type": "string"},
        "objective_satisfied": {"type": "boolean"},
        "counterparty_confirmed": {"type": "boolean"},
        "outcome_summary": {"type": "string"},
        "retry_call": {"type": "boolean"},
        "retry_after_hours": {"type": "integer", "minimum": 1, "maximum": 168},
    },
    "required": [
        "speech",
        "end_call",
        "needs_user",
        "needs_user_reason",
        "objective_satisfied",
        "counterparty_confirmed",
        "outcome_summary",
        "retry_call",
        "retry_after_hours",
    ],
    "additionalProperties": False,
}


def _estimated_tokens(payload: dict[str, Any]) -> int:
    chars = len(TELEPHONY_SYSTEM_PROMPT) + len(json.dumps(payload, ensure_ascii=False, default=str))
    return max(220, chars // 3 + 500)


async def _call_provider(
    db: AsyncSession,
    *,
    base_url: str,
    api_key: str,
    model: str,
    payload: dict[str, Any],
    apply_budget: bool,
) -> dict[str, Any]:
    if apply_budget:
        allowed, reason = await _budget_allows(
            db,
            urgent=True,
            estimated_tokens=_estimated_tokens(payload),
            is_backfill=False,
        )
        if not allowed:
            raise AIQuotaDeferred(reason)

    strict = _is_groq_strict(base_url, model)
    body: dict[str, Any] = {
        "model": model,
        "temperature": 0,
        "max_completion_tokens": 450,
        "messages": [
            {"role": "system", "content": TELEPHONY_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)},
        ],
        "response_format": (
            {
                "type": "json_schema",
                "json_schema": {
                    "name": "telephony_turn_decision",
                    "strict": True,
                    "schema": TELEPHONY_DECISION_SCHEMA,
                },
            }
            if strict
            else {"type": "json_object"}
        ),
    }
    if strict:
        body["reasoning_effort"] = "low"
        body["include_reasoning"] = False

    endpoint = base_url.rstrip("/") + "/chat/completions"
    timeout = min(25.0, float(await get_runtime_value(db, "ai_timeout_seconds", "90") or 90))
    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(2):
            response = await client.post(
                endpoint,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=body,
            )
            if response.status_code == 429:
                await _record_rate_limit(db)
                retry_after = None
                try:
                    retry_after = float(response.headers.get("retry-after", ""))
                except ValueError:
                    pass
                if attempt == 0 and retry_after is not None and retry_after <= 4:
                    await asyncio.sleep(max(0.2, retry_after))
                    continue
                raise AIQuotaDeferred("AI provider rate limit reached", retry_after=retry_after)
            response.raise_for_status()
            data = response.json()
            await _record_response_usage(db, response, data, is_backfill=False)
            try:
                decoded = json.loads(data["choices"][0]["message"]["content"])
            except (KeyError, TypeError, json.JSONDecodeError) as exc:
                raise RuntimeError("AI provider returned an invalid telephony decision") from exc
            if not isinstance(decoded, dict) or not set(TELEPHONY_DECISION_SCHEMA["required"]).issubset(decoded):
                raise RuntimeError("AI provider returned an incomplete telephony decision")
            decoded["speech"] = str(decoded.get("speech") or "")[:700]
            decoded["needs_user_reason"] = str(decoded.get("needs_user_reason") or "")[:1000]
            decoded["outcome_summary"] = str(decoded.get("outcome_summary") or "")[:1600]
            for key in ("end_call", "needs_user", "objective_satisfied", "counterparty_confirmed", "retry_call"):
                decoded[key] = bool(decoded.get(key))
            try:
                decoded["retry_after_hours"] = max(1, min(168, int(decoded.get("retry_after_hours") or 24)))
            except (TypeError, ValueError):
                decoded["retry_after_hours"] = 24
            if decoded["objective_satisfied"] and not decoded["counterparty_confirmed"]:
                decoded["objective_satisfied"] = False
            if decoded["needs_user"]:
                decoded["end_call"] = True
                decoded["objective_satisfied"] = False
                decoded["retry_call"] = False
            return decoded
    raise RuntimeError("AI telephony request failed")


async def analyze_telephony_turn(db: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
    await ensure_ai_configured(db)
    base_url = await get_runtime_value(db, "ai_base_url", "https://api.openai.com/v1")
    api_key = await get_runtime_value(db, "ai_api_key")
    model = await get_runtime_value(db, "ai_model")
    try:
        return await _call_provider(
            db,
            base_url=base_url,
            api_key=api_key,
            model=model,
            payload=payload,
            apply_budget=True,
        )
    except (AIQuotaDeferred, httpx.HTTPError, RuntimeError) as primary_error:
        fallback_key = await get_runtime_value(db, "ai_fallback_api_key")
        fallback_model = await get_runtime_value(db, "ai_fallback_model")
        fallback_base = await get_runtime_value(db, "ai_fallback_base_url")
        # Telephone transcripts can contain sensitive information unexpectedly.
        # Only use the fallback when the user explicitly permits sensitive fallback.
        allow_sensitive = (await get_runtime_value(db, "ai_fallback_allow_sensitive", "false")).lower() == "true"
        if allow_sensitive and fallback_key and fallback_model and fallback_base:
            try:
                return await _call_provider(
                    db,
                    base_url=fallback_base,
                    api_key=fallback_key,
                    model=fallback_model,
                    payload=payload,
                    apply_budget=False,
                )
            except Exception:
                pass
        raise primary_error
