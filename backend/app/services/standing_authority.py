from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import AuditLog, OperationPreference, VAObjective
from app.services.audit import write_audit

_DOMAIN = "standing_authority"
_RISK_ORDER = {"low": 0, "normal": 1, "medium": 1, "high": 2, "critical": 3}

# Standing authority is intentionally template-based. The user can broaden the VA's
# authority once, but only inside predefined executor/risk envelopes that remain
# auditable and revocable.
AUTHORITY_TEMPLATES: dict[str, dict[str, Any]] = {
    "routine_communications": {
        "title": "Routine communications",
        "description": "Send ordinary email/SMS replies and follow-ups without asking each time.",
        "action_types": {
            "send_email_reply",
            "send_message_reply",
            "gmail_send_reply",
            "gmail_send_followup",
            "device_communication_action",
            "device_followup_action",
        },
        "max_risk": "high",
        "default_max_actions_per_day": 50,
        "default_max_amount_eur": None,
    },
    "browser_transactions": {
        "title": "Bounded portal transactions",
        "description": "Allow low-value purchases, returns, refunds, cancellations, and other ordinary portal commitments when the amount is known and within the configured cap.",
        "action_types": {"browser_operation"},
        "max_risk": "high",
        "default_max_actions_per_day": 5,
        "default_max_amount_eur": "50.00",
    },
}

_ACTION_TO_POLICY = {
    action_type: policy_key
    for policy_key, template in AUTHORITY_TEMPLATES.items()
    for action_type in template["action_types"]
}

# These are human boundaries, not preferences. No standing authority can waive
# them. Specific, fresh provider/account-holder participation is still required.
_HARD_BOUNDARY_ACTIONS = {
    "external_authorization",
    "provider_authentication",
    "bank_authorization",
    "payment_authorization",
    "transfer_authorization",
    "credential_entry",
    "auth_code",
    "identity_verification",
    "physical_action",
}
_HARD_BOUNDARY_TERMS = {
    "one-time code",
    "one time code",
    "verification code",
    "security code",
    "2fa",
    "otp",
    "itsme",
    "password",
    "passcode",
    "passport",
    "id card",
    "identity verification",
    "national number",
    "rijksregisternummer",
    "sign contract",
    "accept contract",
    "signature",
    "agree to terms",
    "medical consent",
    "prescription",
    "diagnosis",
    "lawsuit",
    "court order",
    "resign",
    "terminate employment",
    "delete account",
    "close account",
    "change password",
    "change security",
    "bank transfer",
    "wire transfer",
    "withdraw",
    "authorize payment",
    "authorise payment",
}
_MONETARY_BROWSER_TERMS = {
    "pay",
    "purchase",
    "buy",
    "place order",
    "submit order",
    "checkout",
    "confirm payment",
}
_NON_MONETARY_BROWSER_TERMS = {"return", "refund", "cancel", "cancellation"}


def _now() -> datetime:
    return datetime.utcnow()


def _loads(value: str | None) -> dict[str, Any]:
    try:
        decoded = json.loads(value or "{}")
        return decoded if isinstance(decoded, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _normalize_counterparty(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().casefold())[:320]


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value).replace(",", ".")).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


def amount_from_proposal(proposal: dict[str, Any]) -> Decimal | None:
    for key in ("amount_eur", "amount_mentioned", "amount"):
        amount = _decimal(proposal.get(key))
        if amount is not None:
            currency = str(proposal.get("currency") or "EUR").upper()
            return amount if currency == "EUR" else None
    text = " ".join(
        str(proposal.get(key) or "")
        for key in ("summary", "source_excerpt", "proposed_reply", "plan_text")
    )
    for pattern in (
        r"(?:€|EUR)\s*([0-9]+(?:[.,][0-9]{1,2})?)",
        r"([0-9]+(?:[.,][0-9]{1,2})?)\s*(?:EUR\b|€)",
    ):
        match = re.search(pattern, text, re.I)
        if match:
            return _decimal(match.group(1))
    return None


def _proposal_text(proposal: dict[str, Any]) -> str:
    return " ".join(
        str(proposal.get(key) or "")
        for key in (
            "summary",
            "source_excerpt",
            "proposed_reply",
            "plan_text",
            "category",
            "operation",
        )
    ).casefold()


def _hard_boundary(action_type: str, risk_level: str, proposal: dict[str, Any]) -> str:
    if action_type in _HARD_BOUNDARY_ACTIONS:
        return "This action requires fresh account-holder/provider participation and cannot be delegated as standing authority."
    if str(risk_level or "low").casefold() == "critical":
        return "Critical-risk work remains a specific human decision."
    if bool(proposal.get("protected")):
        return "Protected communication decisions remain specific human decisions."
    category = str(proposal.get("category") or "").casefold()
    if category in {"legal", "juridisch", "medical", "gezondheid", "identity", "security", "beveiliging", "fraud"}:
        return f"Standing authority cannot cover protected decision category: {category}."
    text = _proposal_text(proposal)
    term = next((item for item in _HARD_BOUNDARY_TERMS if item in text), "")
    if term:
        return f"Standing authority cannot cover the human-boundary condition: {term}."
    return ""


def _policy_public(key: str, row: OperationPreference | None, usage_today: int = 0) -> dict[str, Any]:
    template = AUTHORITY_TEMPLATES[key]
    config = _loads(row.value_json) if row is not None else {}
    default_amount = template["default_max_amount_eur"]
    return {
        "key": key,
        "title": template["title"],
        "description": template["description"],
        "enabled": bool(row and row.enabled),
        "max_risk": str(config.get("max_risk") or template["max_risk"]),
        "max_actions_per_day": int(config.get("max_actions_per_day") or template["default_max_actions_per_day"]),
        "max_amount_eur": str(config.get("max_amount_eur") or default_amount or ""),
        "counterparties": list(config.get("counterparties") or []),
        "expires_at": str(config.get("expires_at") or ""),
        "usage_today": int(usage_today),
        "source": row.source if row is not None else "default_disabled",
        "hard_boundaries": "Bank/provider authentication, OTP/credentials, identity proof, signatures/contracts, medical/legal consent, security changes, and physical acts always remain specific human steps.",
    }


async def _row(db: AsyncSession, key: str) -> OperationPreference | None:
    return (
        await db.execute(
            select(OperationPreference).where(
                OperationPreference.domain == _DOMAIN,
                OperationPreference.preference_key == key,
            )
        )
    ).scalar_one_or_none()


async def _usage_today(db: AsyncSession, key: str) -> int:
    start = _now().replace(hour=0, minute=0, second=0, microsecond=0)
    rows = list(
        (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.event_type == "va_standing_authority_used",
                    AuditLog.created_at >= start,
                )
            )
        ).scalars()
    )
    count = 0
    for audit in rows:
        details = _loads(audit.details_json)
        if details.get("policy_key") == key:
            count += 1
    return count


async def list_standing_authorities(db: AsyncSession) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for key in AUTHORITY_TEMPLATES:
        row = await _row(db, key)
        result.append(_policy_public(key, row, await _usage_today(db, key)))
    return result


async def set_standing_authority(
    db: AsyncSession,
    key: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    if key not in AUTHORITY_TEMPLATES:
        raise ValueError("Unknown standing-authority policy")
    template = AUTHORITY_TEMPLATES[key]
    row = await _row(db, key)
    if row is None:
        row = OperationPreference(
            domain=_DOMAIN,
            preference_key=key,
            source="explicit",
            confidence=Decimal("1.0000"),
            sample_count=1,
            enabled=False,
        )
        db.add(row)

    existing_config = _loads(row.value_json)
    enabled = bool(payload.get("enabled", row.enabled))
    max_risk = str(payload.get("max_risk") or existing_config.get("max_risk") or template["max_risk"]).casefold()
    if max_risk not in {"low", "normal", "medium", "high"}:
        raise ValueError("Standing authority may not be configured for critical risk")
    if _RISK_ORDER[max_risk] > _RISK_ORDER[str(template["max_risk"])]:
        raise ValueError("Requested risk ceiling exceeds this policy template")

    try:
        max_actions = int(
            payload.get("max_actions_per_day")
            or existing_config.get("max_actions_per_day")
            or template["default_max_actions_per_day"]
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("max_actions_per_day must be an integer") from exc
    if max_actions < 1 or max_actions > 200:
        raise ValueError("max_actions_per_day must be between 1 and 200")

    default_amount = template["default_max_amount_eur"]
    requested_amount = payload.get("max_amount_eur")
    if requested_amount is None:
        requested_amount = existing_config.get("max_amount_eur", default_amount)
    max_amount = _decimal(requested_amount)
    if key == "browser_transactions":
        if max_amount is None or max_amount <= 0 or max_amount > Decimal("500.00"):
            raise ValueError("Bounded portal transactions require an EUR cap between 0.01 and 500.00")
    elif max_amount is not None:
        raise ValueError("This policy does not support a monetary cap")

    raw_counterparties = payload.get("counterparties")
    if raw_counterparties is None:
        raw_counterparties = existing_config.get("counterparties") or []
    if not isinstance(raw_counterparties, list) or len(raw_counterparties) > 100:
        raise ValueError("counterparties must be a list of at most 100 values")
    counterparties = [
        normalized
        for normalized in (_normalize_counterparty(str(item)) for item in raw_counterparties)
        if normalized
    ]

    expires_at_value = payload.get("expires_at")
    if expires_at_value is None:
        expires_at_value = existing_config.get("expires_at") or ""
    expires_at = str(expires_at_value or "").strip()
    if expires_at:
        try:
            parsed = datetime.fromisoformat(expires_at.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError as exc:
            raise ValueError("expires_at must be an ISO-8601 timestamp") from exc
        if parsed <= _now():
            raise ValueError("expires_at must be in the future")
        if parsed > _now() + timedelta(days=365):
            raise ValueError("Standing authority may be granted for at most one year at a time")

    config = {
        "max_risk": max_risk,
        "max_actions_per_day": max_actions,
        "max_amount_eur": str(max_amount) if max_amount is not None else "",
        "counterparties": counterparties,
        "expires_at": expires_at,
    }
    row.value_json = _dump(config)
    row.source = "explicit"
    row.confidence = Decimal("1.0000")
    row.sample_count = 1
    row.enabled = enabled
    await db.flush()
    await write_audit(
        db,
        "va_standing_authority_updated",
        entity_type="operation_preference",
        entity_id=str(row.id),
        details={
            "policy_key": key,
            "enabled": enabled,
            "max_risk": max_risk,
            "max_actions_per_day": max_actions,
            "max_amount_eur": str(max_amount) if max_amount is not None else "",
            "counterparty_count": len(counterparties),
            "expires_at": expires_at,
        },
    )
    await db.commit()
    return _policy_public(key, row, await _usage_today(db, key))


async def evaluate_standing_authority(
    db: AsyncSession,
    *,
    action_type: str,
    risk_level: str = "low",
    proposal: dict[str, Any] | None = None,
) -> dict[str, Any]:
    proposal = dict(proposal or {})
    action_type = str(action_type or "").strip()
    risk_level = str(risk_level or "low").casefold()
    boundary = _hard_boundary(action_type, risk_level, proposal)
    if boundary:
        return {
            "allowed": False,
            "policy_key": "",
            "hard_boundary": True,
            "reason": boundary,
        }

    key = _ACTION_TO_POLICY.get(action_type)
    if not key:
        return {
            "allowed": False,
            "policy_key": "",
            "hard_boundary": False,
            "reason": "No standing-authority template exists for this action type.",
        }
    row = await _row(db, key)
    if row is None or not row.enabled:
        return {
            "allowed": False,
            "policy_key": key,
            "hard_boundary": False,
            "reason": "Standing authority is not enabled for this scope.",
        }
    template = AUTHORITY_TEMPLATES[key]
    config = _loads(row.value_json)

    expires_at = str(config.get("expires_at") or "").strip()
    if expires_at:
        try:
            expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            expiry = _now() - timedelta(seconds=1)
        if expiry <= _now():
            return {
                "allowed": False,
                "policy_key": key,
                "hard_boundary": False,
                "reason": "Standing authority has expired.",
            }

    max_risk = str(config.get("max_risk") or template["max_risk"]).casefold()
    if _RISK_ORDER.get(risk_level, 3) > _RISK_ORDER.get(max_risk, 0):
        return {
            "allowed": False,
            "policy_key": key,
            "hard_boundary": False,
            "reason": f"Risk level {risk_level} exceeds the configured {max_risk} ceiling.",
        }

    counterparty = _normalize_counterparty(str(proposal.get("counterparty") or proposal.get("provider") or ""))
    allowed_counterparties = [
        _normalize_counterparty(str(item))
        for item in (config.get("counterparties") or [])
        if str(item).strip()
    ]
    if allowed_counterparties and counterparty not in allowed_counterparties:
        return {
            "allowed": False,
            "policy_key": key,
            "hard_boundary": False,
            "reason": "Counterparty is outside the configured standing-authority allowlist.",
        }

    usage = await _usage_today(db, key)
    max_actions = int(config.get("max_actions_per_day") or template["default_max_actions_per_day"])
    if usage >= max_actions:
        return {
            "allowed": False,
            "policy_key": key,
            "hard_boundary": False,
            "reason": "The standing-authority daily action limit has been reached.",
        }

    amount = amount_from_proposal(proposal)
    if key == "browser_transactions":
        text = _proposal_text(proposal)
        explicitly_monetary = any(term in text for term in _MONETARY_BROWSER_TERMS)
        safely_non_monetary = any(term in text for term in _NON_MONETARY_BROWSER_TERMS)
        monetary = explicitly_monetary or (bool(proposal.get("material_commitment")) and not safely_non_monetary)
        max_amount = _decimal(config.get("max_amount_eur") or template["default_max_amount_eur"])
        if monetary and amount is None:
            return {
                "allowed": False,
                "policy_key": key,
                "hard_boundary": False,
                "reason": "A monetary portal commitment cannot use standing authority until its EUR amount is known.",
            }
        if amount is not None and (max_amount is None or amount > max_amount):
            return {
                "allowed": False,
                "policy_key": key,
                "hard_boundary": False,
                "reason": f"EUR {amount} exceeds the configured EUR {max_amount} standing-authority cap.",
            }

    return {
        "allowed": True,
        "policy_key": key,
        "hard_boundary": False,
        "reason": "Explicit standing authority covers this exact action inside its configured risk envelope.",
        "usage_today": usage,
        "max_actions_per_day": max_actions,
        "amount_eur": str(amount) if amount is not None else "",
    }


async def record_standing_authority_use(
    db: AsyncSession,
    *,
    decision: dict[str, Any],
    action_type: str,
    proposal: dict[str, Any],
    objective: VAObjective | None = None,
    entity_type: str = "va_objective",
    entity_id: str = "",
) -> None:
    if not decision.get("allowed") or not decision.get("policy_key"):
        raise ValueError("Cannot record use of a standing authority that was not allowed")
    target_id = entity_id or (str(objective.id) if objective is not None else "")
    await write_audit(
        db,
        "va_standing_authority_used",
        entity_type=entity_type,
        entity_id=target_id,
        details={
            "policy_key": decision["policy_key"],
            "action_type": action_type,
            "counterparty_scoped": bool(proposal.get("counterparty") or proposal.get("provider")),
            "amount_eur": decision.get("amount_eur") or "",
            "objective_id": objective.id if objective is not None else None,
        },
    )
