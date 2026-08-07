from __future__ import annotations

import json
from typing import Any

import httpx
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.api import AutomationDecision
from app.services.runtime_config import get_runtime_value

SYSTEM_PROMPT = """You are the decision engine for a private full-time virtual assistant.
Return one JSON object only. Never invent facts. Classify Dutch and English email.
Protect legal, government, bailiff, financial, receipt, contract, account-security,
family, and medical messages. Never trash an unread message. Only set trash=true for
genuine low-value promotion/newsletter/routine notification when is_read=true.
Detect bills and extract creditor_name, amount, currency, due_at ISO-8601 if present,
IBAN, reference, invoice_number, and account_scope personal or pro. A changed or
unverified IBAN must be marked action_required and preserve. Detect customer/support
requests and return support_case with category, status, priority and next_follow_up_at.
Detect purchase/order messages and return order with merchant, order_number, status,
total_amount, currency, expected_delivery_at, tracking_url and account_scope. Detect
recurring subscriptions or renewals and return subscription with provider_name,
description, amount, currency, billing_cycle, next_charge_at, status and account_scope.
Set archive_attachments=true for invoices, receipts, contracts, legal/government,
medical, family, security, support evidence and other durable records. Create a task
when a reply, document, appointment, deadline, payment, cancellation, or follow-up is
needed. Create calendar_event only when date and time are sufficiently certain. Never
claim that a payment was made. Reply content may only be proposed; sending is
controlled by separate explicit rules. The JSON must match:
{
  "category": string,
  "priority": "low"|"normal"|"high"|"urgent",
  "action_required": boolean,
  "preserve": boolean,
  "archive": boolean,
  "trash": boolean,
  "labels": [string],
  "task": object|null,
  "bill": object|null,
  "calendar_event": object|null,
  "reply": object|null,
  "support_case": object|null,
  "order": object|null,
  "subscription": object|null,
  "archive_attachments": boolean,
  "reasoning_summary": string
}
"""


class AIConfigurationError(RuntimeError):
    pass


async def ensure_ai_configured(db: AsyncSession) -> None:
    api_key = await get_runtime_value(db, "ai_api_key")
    model = await get_runtime_value(db, "ai_model")
    if not api_key or not model:
        raise AIConfigurationError("AI provider is not configured")


async def analyze_email(db: AsyncSession, payload: dict[str, Any]) -> AutomationDecision:
    await ensure_ai_configured(db)
    base_url = await get_runtime_value(db, "ai_base_url", "https://api.openai.com/v1")
    api_key = await get_runtime_value(db, "ai_api_key")
    model = await get_runtime_value(db, "ai_model")
    endpoint = base_url.rstrip("/") + "/chat/completions"
    body = {
        "model": model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)},
        ],
    }
    async with httpx.AsyncClient(timeout=90) as client:
        response = await client.post(
            endpoint,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=body,
        )
        response.raise_for_status()
        data = response.json()
    content = data["choices"][0]["message"]["content"]
    try:
        decoded = json.loads(content)
        return AutomationDecision.model_validate(decoded)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise RuntimeError("AI provider returned an invalid automation decision") from exc
