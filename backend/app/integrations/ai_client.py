from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any

import httpx
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import AIUsageDaily
from app.schemas.api import AutomationDecision
from app.services.runtime_config import get_runtime_value

SYSTEM_PROMPT = """You are the decision engine for a private full-time virtual assistant.
Return only the structured decision requested by the response schema. Never invent facts.
Classify Dutch and English email. Protect legal, government, bailiff, financial, receipt,
contract, account-security, family, and medical messages. Never trash an unread message.
Only trash a genuine low-value promotion/newsletter/routine notification when is_read=true.
Use local_extraction as hints, not as unquestionable truth. Distinguish payable invoices from
paid receipts and informational statements/notices. A bill is allowed only when the message
contains evidence that money is still owed (for example amount due, due date, outstanding
balance, payment request/instructions, or a verified payable invoice). Purchase receipts,
payment confirmations, card charges, order confirmations and completed subscription renewals
must set financial_document_type=paid_receipt and bill=null. Informational statements/notices
must set financial_document_type=statement_or_notice and bill=null. A Google Play GPA order
identifier is normally a completed purchase/receipt, not an unpaid invoice, unless the message
explicitly says payment remains due. Detect tasks, sufficiently certain calendar events, support
cases, orders, subscriptions and reply drafts. A changed or unverified IBAN must be
action_required and preserved. Never claim a payment was made unless the source explicitly
confirms it. Keep reasoning_summary to one short sentence. Sending replies and executing
payments are controlled by separate safety rules outside this model."""


def _nullable_object(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "anyOf": [
            {
                "type": "object",
                "properties": properties,
                "required": list(properties),
                "additionalProperties": False,
            },
            {"type": "null"},
        ]
    }


_nullable_string = {"type": ["string", "null"]}
AUTOMATION_DECISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "category": {"type": "string"},
        "financial_document_type": {
            "type": "string",
            "enum": ["none", "payable_invoice", "paid_receipt", "statement_or_notice"],
        },
        "priority": {"type": "string", "enum": ["low", "normal", "high", "urgent"]},
        "action_required": {"type": "boolean"},
        "preserve": {"type": "boolean"},
        "archive": {"type": "boolean"},
        "trash": {"type": "boolean"},
        "labels": {"type": "array", "items": {"type": "string"}},
        "task": _nullable_object({
            "title": {"type": "string"},
            "description": {"type": "string"},
            "due_at": _nullable_string,
            "requires_approval": {"type": "boolean"},
        }),
        "bill": _nullable_object({
            "creditor_name": {"type": "string"},
            "amount": _nullable_string,
            "currency": {"type": "string"},
            "due_at": _nullable_string,
            "iban": _nullable_string,
            "reference": {"type": "string"},
            "invoice_number": {"type": "string"},
            "account_scope": {"type": "string", "enum": ["personal", "pro"]},
        }),
        "calendar_event": _nullable_object({
            "summary": {"type": "string"},
            "description": {"type": "string"},
            "start": {"type": "string"},
            "end": {"type": "string"},
            "location": _nullable_string,
        }),
        "reply": _nullable_object({
            "to": _nullable_string,
            "subject": {"type": "string"},
            "body": {"type": "string"},
        }),
        "support_case": _nullable_object({
            "category": {"type": "string"},
            "status": {"type": "string"},
            "priority": {"type": "string", "enum": ["low", "normal", "high", "urgent"]},
            "next_follow_up_at": _nullable_string,
        }),
        "order": _nullable_object({
            "merchant": {"type": "string"},
            "order_number": {"type": "string"},
            "status": {"type": "string"},
            "total_amount": _nullable_string,
            "currency": {"type": "string"},
            "expected_delivery_at": _nullable_string,
            "tracking_url": _nullable_string,
            "account_scope": {"type": "string", "enum": ["personal", "pro"]},
        }),
        "subscription": _nullable_object({
            "provider_name": {"type": "string"},
            "description": {"type": "string"},
            "amount": _nullable_string,
            "currency": {"type": "string"},
            "billing_cycle": {"type": "string"},
            "next_charge_at": _nullable_string,
            "status": {"type": "string"},
            "account_scope": {"type": "string", "enum": ["personal", "pro"]},
        }),
        "archive_attachments": {"type": "boolean"},
        "reasoning_summary": {"type": "string"},
    },
    "required": [
        "category", "financial_document_type", "priority", "action_required", "preserve", "archive", "trash", "labels",
        "task", "bill", "calendar_event", "reply", "support_case", "order", "subscription",
        "archive_attachments", "reasoning_summary",
    ],
    "additionalProperties": False,
}


class AIConfigurationError(RuntimeError):
    pass


class AIQuotaDeferred(RuntimeError):
    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


async def ensure_ai_configured(db: AsyncSession) -> None:
    api_key = await get_runtime_value(db, "ai_api_key")
    model = await get_runtime_value(db, "ai_model")
    if not api_key or not model:
        raise AIConfigurationError("AI provider is not configured")


def _day_key() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d")


async def _usage_row(db: AsyncSession) -> AIUsageDaily:
    key = _day_key()
    row = await db.get(AIUsageDaily, key)
    if row is None:
        row = AIUsageDaily(day_key=key)
        db.add(row)
        await db.flush()
    return row


async def mark_rule_shortcut(db: AsyncSession) -> None:
    row = await _usage_row(db)
    row.rule_shortcuts += 1


async def mark_fingerprint_hit(db: AsyncSession) -> None:
    row = await _usage_row(db)
    row.fingerprint_hits += 1


async def mark_ai_deferred(db: AsyncSession) -> None:
    row = await _usage_row(db)
    row.deferred_count += 1


async def ai_usage_status(db: AsyncSession) -> dict[str, Any]:
    row = await _usage_row(db)
    request_budget = max(1, int(await get_runtime_value(db, "ai_daily_request_budget", "1000")))
    token_budget = max(1, int(await get_runtime_value(db, "ai_daily_token_budget", "200000")))
    request_reserve = max(0, int(await get_runtime_value(db, "ai_daily_request_reserve", "100")))
    token_reserve = max(0, int(await get_runtime_value(db, "ai_daily_token_reserve", "25000")))
    return {
        "day": row.day_key,
        "provider_base_url": await get_runtime_value(db, "ai_base_url", ""),
        "model": await get_runtime_value(db, "ai_model", ""),
        "fallback_configured": bool(
            await get_runtime_value(db, "ai_fallback_api_key") and await get_runtime_value(db, "ai_fallback_model")
        ),
        "requests": row.request_count,
        "request_budget": request_budget,
        "requests_remaining_local": max(0, request_budget - row.request_count),
        "total_tokens": row.total_tokens,
        "token_budget": token_budget,
        "tokens_remaining_local": max(0, token_budget - row.total_tokens),
        "reserved_requests": request_reserve,
        "reserved_tokens": token_reserve,
        "prompt_tokens": row.prompt_tokens,
        "completion_tokens": row.completion_tokens,
        "rate_limit_count": row.rate_limit_count,
        "deferred_count": row.deferred_count,
        "rule_shortcuts": row.rule_shortcuts,
        "fingerprint_hits": row.fingerprint_hits,
        "provider_remaining_requests": row.provider_remaining_requests,
        "provider_remaining_tokens_minute": row.provider_remaining_tokens_minute,
    }


async def _budget_allows(
    db: AsyncSession,
    *,
    urgent: bool,
    estimated_tokens: int,
    is_backfill: bool,
) -> tuple[bool, str]:
    row = await _usage_row(db)
    request_budget = max(1, int(await get_runtime_value(db, "ai_daily_request_budget", "1000")))
    token_budget = max(1, int(await get_runtime_value(db, "ai_daily_token_budget", "200000")))
    request_reserve = max(0, int(await get_runtime_value(db, "ai_daily_request_reserve", "100")))
    token_reserve = max(0, int(await get_runtime_value(db, "ai_daily_token_reserve", "25000")))
    if is_backfill:
        backfill_limit = max(0, int(await get_runtime_value(db, "ai_backfill_daily_limit", "50")))
        if row.backfill_requests >= backfill_limit:
            return False, "daily historical-mail AI allowance reached"
    request_ceiling = request_budget if urgent else max(0, request_budget - request_reserve)
    token_ceiling = token_budget if urgent else max(0, token_budget - token_reserve)
    if row.request_count + 1 > request_ceiling:
        return False, "daily AI request allowance reserved for urgent mail"
    if row.total_tokens + estimated_tokens > token_ceiling:
        return False, "daily AI token allowance reserved for urgent mail"
    return True, ""


def _is_groq_strict(base_url: str, model: str) -> bool:
    return "api.groq.com" in base_url.lower() and model in {"openai/gpt-oss-20b", "openai/gpt-oss-120b"}


def _estimate_tokens(payload: dict[str, Any]) -> int:
    # Deliberately conservative; actual usage is stored from the provider response.
    chars = len(SYSTEM_PROMPT) + len(json.dumps(payload, ensure_ascii=False, default=str))
    return max(300, chars // 3 + 900)


async def _record_response_usage(
    db: AsyncSession,
    response: httpx.Response,
    data: dict[str, Any],
    *,
    is_backfill: bool,
) -> None:
    row = await _usage_row(db)
    usage = data.get("usage") or {}
    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    total = int(usage.get("total_tokens") or prompt + completion)
    row.request_count += 1
    row.prompt_tokens += prompt
    row.completion_tokens += completion
    row.total_tokens += total
    if is_backfill:
        row.backfill_requests += 1
    try:
        row.provider_remaining_requests = int(response.headers.get("x-ratelimit-remaining-requests", ""))
    except ValueError:
        pass
    try:
        row.provider_remaining_tokens_minute = int(response.headers.get("x-ratelimit-remaining-tokens", ""))
    except ValueError:
        pass
    # Persist quota accounting before any later Gmail/Calendar/Drive side effect can fail.
    await db.commit()


async def _record_rate_limit(db: AsyncSession) -> None:
    row = await _usage_row(db)
    row.rate_limit_count += 1
    await db.commit()


async def _call_provider(
    db: AsyncSession,
    *,
    base_url: str,
    api_key: str,
    model: str,
    payload: dict[str, Any],
    urgent: bool,
    is_backfill: bool,
    apply_budget: bool,
) -> AutomationDecision:
    if apply_budget:
        allowed, reason = await _budget_allows(
            db,
            urgent=urgent,
            estimated_tokens=_estimate_tokens(payload),
            is_backfill=is_backfill,
        )
        if not allowed:
            raise AIQuotaDeferred(reason)

    strict = _is_groq_strict(base_url, model)
    body: dict[str, Any] = {
        "model": model,
        "temperature": 0,
        "max_completion_tokens": 1200,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)},
        ],
        "response_format": (
            {
                "type": "json_schema",
                "json_schema": {
                    "name": "automation_decision",
                    "strict": True,
                    "schema": AUTOMATION_DECISION_SCHEMA,
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
    timeout = float(await get_runtime_value(db, "ai_timeout_seconds", "90") or 90)
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
                if attempt == 0 and retry_after is not None and retry_after <= 15:
                    await asyncio.sleep(max(0.2, retry_after))
                    continue
                raise AIQuotaDeferred("AI provider rate limit reached", retry_after=retry_after)
            response.raise_for_status()
            data = response.json()
            await _record_response_usage(db, response, data, is_backfill=is_backfill)
            content = data["choices"][0]["message"]["content"]
            try:
                decoded = json.loads(content)
                return AutomationDecision.model_validate(decoded)
            except (json.JSONDecodeError, ValidationError) as exc:
                raise RuntimeError("AI provider returned an invalid automation decision") from exc
    raise RuntimeError("AI request failed")


async def analyze_email(
    db: AsyncSession,
    payload: dict[str, Any],
    *,
    urgent: bool = False,
    sensitive: bool = False,
    is_backfill: bool = False,
) -> AutomationDecision:
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
            urgent=urgent,
            is_backfill=is_backfill,
            apply_budget=True,
        )
    except AIQuotaDeferred as primary_quota:
        fallback_key = await get_runtime_value(db, "ai_fallback_api_key")
        fallback_model = await get_runtime_value(db, "ai_fallback_model")
        fallback_base = await get_runtime_value(db, "ai_fallback_base_url")
        allow_sensitive = (await get_runtime_value(db, "ai_fallback_allow_sensitive", "false")).lower() == "true"
        if fallback_key and fallback_model and fallback_base and (allow_sensitive or not sensitive):
            return await _call_provider(
                db,
                base_url=fallback_base,
                api_key=fallback_key,
                model=fallback_model,
                payload=payload,
                urgent=True,
                is_backfill=False,
                apply_budget=False,
            )
        raise primary_quota
    except (httpx.HTTPError, RuntimeError) as primary_error:
        fallback_key = await get_runtime_value(db, "ai_fallback_api_key")
        fallback_model = await get_runtime_value(db, "ai_fallback_model")
        fallback_base = await get_runtime_value(db, "ai_fallback_base_url")
        allow_sensitive = (await get_runtime_value(db, "ai_fallback_allow_sensitive", "false")).lower() == "true"
        if fallback_key and fallback_model and fallback_base and (allow_sensitive or not sensitive):
            try:
                return await _call_provider(
                    db,
                    base_url=fallback_base,
                    api_key=fallback_key,
                    model=fallback_model,
                    payload=payload,
                    urgent=True,
                    is_backfill=False,
                    apply_budget=False,
                )
            except Exception:
                pass
        raise primary_error
