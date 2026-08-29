from __future__ import annotations

from typing import Any

from app.services.runtime_config import get_runtime_value


_ALLOWED_NEEDS_YOU = {
    "payment_authorization",
    "payment_reconciliation",
    "transfer_authorization",
    "transfer_reconciliation",
    "task_approval",
    "provider_authorization",
    "funding_required",
    "urgent_communication",
}


def filter_needs_you(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep Needs You for actual human authority/auth/material requirements only."""
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for raw in items:
        item = dict(raw)
        kind = str(item.get("type") or "")
        if kind == "autopilot_exception":
            if str(item.get("classification") or "") != "user_required":
                continue
        elif kind not in _ALLOWED_NEEDS_YOU:
            continue
        # AI availability/model/configuration is system-owned; deterministic fallback
        # must keep mail moving rather than making the user operate the AI stack.
        if kind == "provider_authorization" and str(item.get("id") or "") == "ai_primary":
            continue
        item.setdefault("interrupt", kind == "urgent_communication")
        signature = (kind, str(item.get("id") or ""), str(item.get("title") or ""))
        if signature in seen:
            continue
        seen.add(signature)
        result.append(item)
    return result


def human_briefing_summary(
    stats: dict[str, Any],
    payments: list[dict[str, Any]],
    upcoming_bills: list[Any],
    needs_you: list[dict[str, Any]],
    *,
    period: str = "daily",
    commitments: dict[str, Any] | None = None,
) -> str:
    greeting = {
        "morning": "Good morning.",
        "afternoon": "Good afternoon.",
        "evening": "Good evening.",
    }.get(period, "Here is the latest.")
    parts = [greeting]

    if needs_you:
        first = needs_you[0]
        title = str(first.get("title") or "one item").strip()
        detail = " ".join(str(first.get("detail") or "").split())
        parts.append(f"Most things are under control, but there is one thing I need from you: {title}.")
        if detail:
            parts.append(detail[:360] + ("…" if len(detail) > 360 else ""))
        if len(needs_you) > 1:
            parts.append(f"There are {len(needs_you) - 1} other genuine user items behind it; I have kept everything else with the VA.")
    else:
        parts.append("Everything is under control and nothing needs your attention right now.")

    if int(stats.get("emails_received") or 0) or int(stats.get("messages_received") or 0):
        parts.append("I cleared the routine mail and messages that came in and kept the useful information with the relevant work.")
    if int(stats.get("replies_sent") or 0) or int(stats.get("communication_replies_sent") or 0):
        parts.append("I also handled the replies that were safe to send on your behalf.")
    if int(stats.get("receipts_and_notices") or 0) or payments:
        parts.append("Your financial notifications and records have been filed and reconciled where the provider evidence allowed it.")
    if upcoming_bills:
        parts.append("I am keeping an eye on the bills coming due and will only involve you if a real authorization or exception appears.")
    if int(stats.get("calendar_changes") or 0):
        parts.append("I updated your calendar where incoming information required it.")
    commitments = commitments or {}
    working = commitments.get("working_now") if isinstance(commitments.get("working_now"), list) else []
    waiting = commitments.get("waiting_external") if isinstance(commitments.get("waiting_external"), list) else []
    resolving = commitments.get("resolving_internal") if isinstance(commitments.get("resolving_internal"), list) else []
    if working:
        lead = working[0] if isinstance(working[0], dict) else {}
        title = str(lead.get("title") or "the main open commitment").strip()
        commitment = lead.get("commitment") if isinstance(lead.get("commitment"), dict) else {}
        next_action = str(commitment.get("next_action") or "").strip()
        parts.append(f"The main thing I am actively working on is {title}.")
        if next_action:
            parts.append(f"My next step there is to {next_action[:1].lower() + next_action[1:]}.")
    if waiting:
        lead = waiting[0] if isinstance(waiting[0], dict) else {}
        title = str(lead.get("title") or "one open item").strip()
        commitment = lead.get("commitment") if isinstance(lead.get("commitment"), dict) else {}
        waiting_on = str(commitment.get("waiting_on") or "the other side").replace("_", " ")
        parts.append(f"I am also still holding {title}; it is waiting on {waiting_on}, and I will keep the follow-through with me.")
    if resolving or int(stats.get("provider_problems") or 0):
        parts.append("There is also some internal/provider cleanup in progress. I am keeping that system-owned rather than turning it into your task.")
    return " ".join(parts)


async def briefing_period_schedule(db, local_now) -> list[dict[str, Any]]:
    defaults = {
        "morning": ("false", "8"),
        "afternoon": ("false", "14"),
        "evening": ("true", "19"),
    }
    periods: list[dict[str, Any]] = []
    for name, (enabled_default, hour_default) in defaults.items():
        enabled = (await get_runtime_value(db, f"briefing_{name}_enabled", enabled_default)).casefold() == "true"
        try:
            hour = max(0, min(int(await get_runtime_value(db, f"briefing_{name}_hour_local", hour_default)), 23))
        except ValueError:
            hour = int(hour_default)
        periods.append({
            "name": name,
            "enabled": enabled,
            "hour_local": hour,
            "ready": enabled and local_now.hour >= hour,
            "delivery_key": f"{local_now.date().isoformat()}:{name}",
        })
    return periods
