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
    if int(stats.get("provider_problems") or 0):
        parts.append("A provider or internal service had an issue; I am keeping that system-owned and retrying or containing it rather than turning it into your task.")
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
