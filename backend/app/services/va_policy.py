from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import VAObjective
from app.services.capability_registry import capability_for_key
from app.services.runtime_config import get_runtime_value


_INTENT_CAPABILITY = {
    "sync_gmail": "email",
    "sync_banking": "banking_read",
    "sync_contacts": "contacts",
    "run_connectors": "service_connectors",
    "housekeeping": "documents",
    "provider_health": "workflow_engine",
    "plan": "ai_decisioning",
}


async def authorize_step(
    db: AsyncSession,
    *,
    objective: VAObjective,
    action_type: str,
    parameters: dict[str, Any],
) -> dict[str, Any]:
    """Central, fail-closed authority decision for Phase-1 objective steps."""

    if (await get_runtime_value(db, "va_autonomous_core_enabled", "true")).lower() != "true":
        return {
            "allowed": False,
            "needs_user": False,
            "resolution": "system_disabled",
            "reason": "Autonomous VA core is disabled",
        }

    if action_type in {"record_only", "wait", "complete"}:
        return {
            "allowed": True,
            "needs_user": False,
            "resolution": "automatic",
            "reason": "Internal reversible operation",
        }


    if action_type in {"gmail_send_reply", "gmail_send_followup"}:
        capability = await capability_for_key(db, "email")
        if capability is None or not capability.get("available"):
            return {
                "allowed": False,
                "needs_user": bool(capability and capability.get("resolution") == "user_connect"),
                "resolution": (capability or {}).get("resolution") or "capability_unavailable",
                "reason": "Gmail send requires a live Google OAuth connection",
                "capability": capability,
            }
        if objective.risk_level in {"critical"}:
            return {
                "allowed": False,
                "needs_user": True,
                "resolution": "material_decision",
                "reason": "Critical-risk communication requires the account holder's material decision",
                "capability": capability,
            }
        if not str(parameters.get("recipient") or "").strip() or not str(parameters.get("body") or "").strip():
            return {
                "allowed": False,
                "needs_user": False,
                "resolution": "invalid_parameters",
                "reason": "Gmail recipient and body are required",
                "capability": capability,
            }
        return {
            "allowed": True,
            "needs_user": False,
            "resolution": "automatic",
            "reason": "Gmail reply was already classified safe and has a real durable send/verification executor",
            "capability": capability,
        }

    if action_type in {"device_communication_action", "device_followup_action"}:
        capability = await capability_for_key(db, "android_device")
        if capability is None or not capability.get("available"):
            return {
                "allowed": False,
                "needs_user": bool(capability and capability.get("resolution") == "user_connect"),
                "resolution": (capability or {}).get("resolution") or "capability_unavailable",
                "reason": "A live paired Android device is required for this communication action",
                "capability": capability,
            }
        channel = str(parameters.get("channel") or "").lower()
        if channel not in {"sms", "whatsapp", "signal", "telegram", "messenger"}:
            return {
                "allowed": False,
                "needs_user": False,
                "resolution": "unsupported_action",
                "reason": f"No Android communication executor exists for channel {channel or '<empty>'}",
                "capability": capability,
            }
        if objective.risk_level == "critical":
            return {
                "allowed": False,
                "needs_user": True,
                "resolution": "material_decision",
                "reason": "Critical-risk communication requires the account holder's material decision",
                "capability": capability,
            }
        return {
            "allowed": True,
            "needs_user": False,
            "resolution": "automatic",
            "reason": "A real paired-device action exists and outcome verification is required",
            "capability": capability,
        }

    if action_type == "needs_user":
        return {
            "allowed": False,
            "needs_user": True,
            "resolution": "user_action",
            "reason": str(parameters.get("reason") or "External authorization or material user decision is required"),
        }

    if action_type == "workflow_intent":
        intent_type = str(parameters.get("intent_type") or "").strip()
        if intent_type == "run_va":
            # run_va is intentionally allowed even when some providers are absent;
            # its existing workflow already skips unavailable providers and executes
            # only the real configured stages.
            capability = await capability_for_key(db, "workflow_engine")
        else:
            capability_key = _INTENT_CAPABILITY.get(intent_type)
            if capability_key is None:
                return {
                    "allowed": False,
                    "needs_user": False,
                    "resolution": "unsupported_action",
                    "reason": f"Unsupported durable workflow intent: {intent_type or '<empty>'}",
                }
            capability = await capability_for_key(db, capability_key)
        if capability is None:
            return {
                "allowed": False,
                "needs_user": False,
                "resolution": "unsupported_action",
                "reason": "Required capability is not registered",
            }
        if not capability["available"]:
            user_resolvable = capability.get("resolution") == "user_connect"
            return {
                "allowed": False,
                "needs_user": user_resolvable,
                "resolution": capability.get("resolution") or "capability_unavailable",
                "reason": f"{capability['title']} is unavailable: {capability.get('detail') or capability['executor']}",
                "capability": capability,
            }
        if objective.risk_level == "critical":
            return {
                "allowed": False,
                "needs_user": True,
                "resolution": "material_decision",
                "reason": "Critical-risk objective requires the account holder's material decision",
                "capability": capability,
            }
        return {
            "allowed": True,
            "needs_user": False,
            "resolution": "automatic",
            "reason": "Existing durable workflow is available and within Phase-1 policy",
            "capability": capability,
        }

    return {
        "allowed": False,
        "needs_user": False,
        "resolution": "unsupported_action",
        "reason": f"Action type is not implemented by the autonomous core: {action_type}",
    }
