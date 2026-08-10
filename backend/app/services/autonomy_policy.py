from __future__ import annotations

import json
import re
from decimal import Decimal
from email.utils import parseaddr
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import EmailMessage, OperationPreference
from app.schemas.api import AutomationDecision

PROTECTED_REPLY_CATEGORIES = {
    "banking", "finance", "financial", "geldzaken", "legal", "juridisch", "security", "beveiliging",
    "government", "overheid", "tax", "belasting", "insurance", "verzekering", "medical", "gezondheid",
    "employment", "hr", "identity", "fraud", "family", "familie",
}

RISKY_REPLY_TERMS = {
    "authorize", "authorise", "authorization", "approval", "approve", "contract", "agreement", "agree to",
    "accept terms", "signature", "sign this", "payment", "pay", "transfer", "bank", "iban", "card number",
    "password", "passcode", "verification code", "2fa", "otp", "itsme", "identity", "passport", "id card",
    "social security", "rijksregisternummer", "national number", "lawsuit", "court", "lawyer", "attorney",
    "terminate", "cancel contract", "resign", "medical", "diagnosis", "prescription", "insurance claim",
}

NO_REPLY_MARKERS = {"no-reply", "noreply", "do-not-reply", "donotreply"}


def _decode(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def normalized_sender(sender: str) -> str:
    _, address = parseaddr(sender or "")
    return (address or sender or "").strip().lower()


def _contains_any(text: str, terms: set[str]) -> bool:
    value = re.sub(r"\s+", " ", (text or "").lower())
    return any(term in value for term in terms)


AMBIGUITY_TASK_TERMS = {
    "choose", "decide", "decision", "confirm which", "which option", "preference", "clarify", "clarification",
    "unknown", "ambiguous", "missing information", "need your input", "requires your input", "approval", "approve",
    "budget", "quote selection", "select one", "pick one", "consent", "authorize", "authorise",
}


def task_requires_human(decision: AutomationDecision) -> tuple[bool, str]:
    task = decision.task or {}
    if not task:
        return False, "no_task"
    category_text = f"{decision.category} {' '.join(decision.labels)}".lower()
    text = f"{task.get('title', '')}\n{task.get('description', '')}\n{decision.reasoning_summary}"
    if any(term in category_text for term in PROTECTED_REPLY_CATEGORIES):
        return True, "protected_task_context"
    if _contains_any(text, RISKY_REPLY_TERMS):
        return True, "sensitive_or_commitment_task"
    if _contains_any(text, AMBIGUITY_TASK_TERMS):
        return True, "ambiguous_task_requires_preference"
    return False, "routine_task_owned_by_autopilot"


async def get_preference(db: AsyncSession, domain: str, key: str) -> OperationPreference | None:
    return (
        await db.execute(
            select(OperationPreference).where(
                OperationPreference.domain == domain,
                OperationPreference.preference_key == key,
                OperationPreference.enabled.is_(True),
            )
        )
    ).scalar_one_or_none()


async def record_learned_preference(
    db: AsyncSession,
    *,
    domain: str,
    key: str,
    value: dict[str, Any],
    minimum_samples: int = 3,
) -> OperationPreference | None:
    """Learn only deterministic, non-security preferences after repeated matching successes."""

    normalized_domain = domain.strip().lower()[:80]
    normalized_key = key.strip()[:255]
    if not normalized_domain or not normalized_key:
        return None
    if normalized_domain in {"security", "credentials", "payment_authorization", "bank_authorization", "legal"}:
        return None

    row = (
        await db.execute(
            select(OperationPreference).where(
                OperationPreference.domain == normalized_domain,
                OperationPreference.preference_key == normalized_key,
            )
        )
    ).scalar_one_or_none()
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    if row is None:
        row = OperationPreference(
            domain=normalized_domain,
            preference_key=normalized_key,
            value_json=encoded,
            confidence=Decimal("0.5000"),
            sample_count=1,
            source="learned",
            enabled=minimum_samples <= 1,
        )
        db.add(row)
    elif row.source == "explicit":
        return row
    elif row.value_json == encoded:
        row.sample_count = int(row.sample_count or 0) + 1
    else:
        # A changed outcome resets learning instead of silently generalizing conflicting behavior.
        row.value_json = encoded
        row.sample_count = 1
        row.enabled = False

    row.source = "learned"
    samples = max(1, int(row.sample_count or 1))
    row.confidence = Decimal(str(min(0.99, 0.50 + min(samples, 5) * 0.10))).quantize(Decimal("0.0001"))
    row.enabled = samples >= max(1, minimum_samples)
    await db.flush()
    return row


async def learn_successful_reply(
    db: AsyncSession,
    *,
    message: EmailMessage,
    mode: str,
) -> None:
    sender = normalized_sender(message.sender)
    if not sender:
        return
    await record_learned_preference(
        db,
        domain="email_reply",
        key=f"sender:{sender}",
        value={"auto_send": True, "category": message.category, "mode": mode},
        minimum_samples=3,
    )


async def reply_autonomy_decision(
    db: AsyncSession,
    *,
    message: EmailMessage,
    decision: AutomationDecision,
) -> tuple[bool, str]:
    """Return whether a saved reply can be sent unattended.

    Explicit preferences can opt a sender into autonomy. Without one, only low-risk acknowledgement-style
    replies are sent automatically. Financial/legal/security/identity decisions always stay human-gated.
    """

    reply = decision.reply or {}
    body = str(reply.get("body") or "").strip()
    sender = normalized_sender(message.sender)
    if not body:
        return False, "empty_reply"
    if not sender or any(marker in sender for marker in NO_REPLY_MARKERS):
        return False, "non_replyable_sender"

    explicit = await get_preference(db, "email_reply", f"sender:{sender}")
    if explicit is not None and _decode(explicit.value_json).get("auto_send") is False:
        return False, "explicit_block"

    category_text = f"{decision.category} {' '.join(decision.labels)}".lower()
    combined = f"{message.subject}\n{message.snippet}\n{body}\n{decision.reasoning_summary}"
    if any(term in category_text for term in PROTECTED_REPLY_CATEGORIES):
        return False, "protected_category"
    if decision.financial_document_type != "none" or decision.bill is not None:
        return False, "financial_context"
    if _contains_any(combined, RISKY_REPLY_TERMS):
        return False, "risky_commitment_or_sensitive_content"
    if str(decision.priority or "normal").lower() in {"urgent", "critical"}:
        return False, "urgent_message_requires_judgment"
    if len(body) > 1800:
        return False, "long_reply_requires_judgment"

    if explicit is not None and _decode(explicit.value_json).get("auto_send") is True:
        return True, (
            "explicit_sender_preference" if explicit.source == "explicit" else "learned_sender_preference"
        )

    from app.services.runtime_config import get_runtime_value

    enabled = (await get_runtime_value(db, "autonomous_low_risk_replies", "true")).lower() == "true"
    if not enabled:
        return False, "autonomous_low_risk_replies_disabled"

    # Default unattended path: short, non-sensitive, non-contractual replies are routine VA work.
    return True, "deterministic_low_risk"
