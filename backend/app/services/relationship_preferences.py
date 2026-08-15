from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from email.utils import parseaddr
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import RelationshipFact, RelationshipIdentity, RelationshipProfile
from app.services.audit import write_audit

FACT_KEY = "communication_preferences"
SOURCE_TYPE = "user_explicit"

_ALLOWED_LANGUAGE = {"auto", "nl", "fr", "en", "de"}
_ALLOWED_TONE = {"neutral", "friendly", "warm", "direct", "professional"}
_ALLOWED_FORMALITY = {"auto", "informal", "formal"}
_ALLOWED_GREETING = {"auto", "first_name", "hello", "none"}
_ALLOWED_SIGNOFF = {"auto", "name", "warm", "professional", "none"}
_ALLOWED_VERBOSITY = {"short", "normal", "detailed"}
_ALLOWED_CHANNEL = {"auto", "email", "sms", "whatsapp", "signal", "telegram", "messenger"}
_ALLOWED_CATEGORY = {"partner", "family", "friend", "client", "provider", "colleague", "other"}
_ALLOWED_ALIAS_CHANNEL = {"whatsapp", "signal", "telegram", "messenger", "notification"}


def _now() -> datetime:
    return datetime.utcnow()


def _loads(value: str | None) -> dict[str, Any]:
    try:
        decoded = json.loads(value or "{}")
        return decoded if isinstance(decoded, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def _bounded_text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _enum(value: Any, allowed: set[str], *, default: str) -> str:
    normalized = str(value or default).strip().lower()
    return normalized if normalized in allowed else default


def _normalize_email(value: str) -> str:
    _, address = parseaddr(value or "")
    candidate = (address or value or "").strip().lower()
    if re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", candidate):
        return candidate
    return ""


def _normalize_phone(value: str) -> str:
    raw = (value or "").strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) < 7:
        return ""
    return ("+" if raw.startswith("+") else "") + digits


def sanitize_preferences(values: dict[str, Any]) -> dict[str, Any]:
    """Validate user-explicit communication preferences.

    These fields influence presentation and conservative communication policy only.
    They never encode payment, browser, banking, legal or credential authority.
    """

    result: dict[str, Any] = {
        "language": _enum(values.get("language"), _ALLOWED_LANGUAGE, default="auto"),
        "tone": _enum(values.get("tone"), _ALLOWED_TONE, default="neutral"),
        "formality": _enum(values.get("formality"), _ALLOWED_FORMALITY, default="auto"),
        "greeting_style": _enum(values.get("greeting_style"), _ALLOWED_GREETING, default="auto"),
        "signoff_style": _enum(values.get("signoff_style"), _ALLOWED_SIGNOFF, default="auto"),
        "verbosity": _enum(values.get("verbosity"), _ALLOWED_VERBOSITY, default="normal"),
        "preferred_channel": _enum(values.get("preferred_channel"), _ALLOWED_CHANNEL, default="auto"),
        "relationship_category": _enum(values.get("relationship_category"), _ALLOWED_CATEGORY, default="other"),
        "instructions": _bounded_text(values.get("instructions"), 2000),
        "learn_from_history": values.get("learn_from_history") is True,
    }

    auto_send = values.get("routine_auto_send")
    result["routine_auto_send"] = auto_send if isinstance(auto_send, bool) else None

    approval_topics: list[str] = []
    raw_topics = values.get("approval_topics")
    if isinstance(raw_topics, list):
        for item in raw_topics[:20]:
            topic = _bounded_text(item, 80)
            if topic and topic.casefold() not in {entry.casefold() for entry in approval_topics}:
                approval_topics.append(topic)
    result["approval_topics"] = approval_topics

    examples: list[str] = []
    raw_examples = values.get("examples")
    if isinstance(raw_examples, list):
        for item in raw_examples[:5]:
            example = _bounded_text(item, 500)
            if example:
                examples.append(example)
    result["examples"] = examples

    aliases: dict[str, list[str]] = {}
    raw_aliases = values.get("channel_aliases")
    if isinstance(raw_aliases, dict):
        for raw_channel, raw_values in raw_aliases.items():
            channel = str(raw_channel or "").strip().lower()
            if channel not in _ALLOWED_ALIAS_CHANNEL or not isinstance(raw_values, list):
                continue
            channel_values: list[str] = []
            for item in raw_values[:8]:
                alias = _bounded_text(item, 120)
                if alias and alias.casefold() not in {entry.casefold() for entry in channel_values}:
                    channel_values.append(alias)
            if channel_values:
                aliases[channel] = channel_values
    result["channel_aliases"] = aliases
    return result


def preferences_for_ai(preferences: dict[str, Any]) -> dict[str, Any]:
    """Return only style/context preferences safe to hand to the language model.

    Auto-send and approval-topic controls stay deterministic and are deliberately
    excluded so model output cannot grant itself execution authority.
    """

    allowed = {
        "language",
        "tone",
        "formality",
        "greeting_style",
        "signoff_style",
        "verbosity",
        "preferred_channel",
        "relationship_category",
        "instructions",
        "examples",
    }
    return {key: value for key, value in preferences.items() if key in allowed and value not in (None, "", [], "auto")}


def preference_digest(preferences: dict[str, Any]) -> str:
    canonical = json.dumps(preferences, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def _explicit_fact(db: AsyncSession, relationship_id: int) -> RelationshipFact | None:
    source_ref = f"relationship:{relationship_id}:communication_preferences"
    return (
        await db.execute(
            select(RelationshipFact).where(
                RelationshipFact.relationship_id == relationship_id,
                RelationshipFact.fact_key == FACT_KEY,
                RelationshipFact.source_type == SOURCE_TYPE,
                RelationshipFact.source_ref == source_ref,
            )
        )
    ).scalar_one_or_none()


async def get_relationship_communication_preferences(
    db: AsyncSession,
    relationship_id: int,
) -> dict[str, Any]:
    profile = await db.get(RelationshipProfile, relationship_id)
    if profile is None:
        raise LookupError("relationship not found")
    fact = await _explicit_fact(db, relationship_id)
    preferences = sanitize_preferences(_loads(fact.value_json) if fact is not None else {})
    return {
        "relationship_id": relationship_id,
        "display_name": profile.display_name,
        "preferences": preferences,
        "provenance": None
        if fact is None
        else {
            "source_type": fact.source_type,
            "source_ref": fact.source_ref,
            "confidence": str(fact.confidence),
            "first_seen_at": fact.first_seen_at,
            "last_seen_at": fact.last_seen_at,
        },
    }


async def set_relationship_communication_preferences(
    db: AsyncSession,
    relationship_id: int,
    values: dict[str, Any],
) -> dict[str, Any]:
    profile = await db.get(RelationshipProfile, relationship_id)
    if profile is None:
        raise LookupError("relationship not found")

    preferences = sanitize_preferences(values)
    encoded = json.dumps(preferences, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    source_ref = f"relationship:{relationship_id}:communication_preferences"
    row = await _explicit_fact(db, relationship_id)
    now = _now()
    if row is None:
        row = RelationshipFact(
            relationship_id=relationship_id,
            fact_key=FACT_KEY,
            value_json=encoded,
            source_type=SOURCE_TYPE,
            source_ref=source_ref,
            confidence=1,
            first_seen_at=now,
            last_seen_at=now,
        )
        db.add(row)
    else:
        row.value_json = encoded
        row.last_seen_at = now
        row.confidence = 1

    await write_audit(
        db,
        "relationship_communication_preferences_set",
        entity_type="relationship",
        entity_id=str(relationship_id),
        details={
            "fact_key": FACT_KEY,
            "source_type": SOURCE_TYPE,
            "fields": sorted(preferences),
            "routine_auto_send": preferences.get("routine_auto_send"),
            "approval_topic_count": len(preferences.get("approval_topics") or []),
            "example_count": len(preferences.get("examples") or []),
            "channel_alias_count": sum(len(items) for items in (preferences.get("channel_aliases") or {}).values()),
            "learn_from_history": preferences.get("learn_from_history") is True,
        },
    )
    await db.commit()
    return await get_relationship_communication_preferences(db, relationship_id)


async def communication_preferences_for_relationship(
    db: AsyncSession,
    relationship_id: int,
) -> dict[str, Any]:
    fact = await _explicit_fact(db, relationship_id)
    return sanitize_preferences(_loads(fact.value_json) if fact is not None else {})


async def resolve_relationship_for_party(
    db: AsyncSession,
    party: str,
    *,
    channel: str = "",
    provider: str = "",
) -> tuple[int | None, dict[str, Any]]:
    candidates: list[tuple[str, str]] = []
    email = _normalize_email(party)
    if email:
        candidates.append(("email", email))
    phone = _normalize_phone(party)
    if phone:
        candidates.append(("phone", phone))
    for identity_type, normalized in candidates:
        identity = (
            await db.execute(
                select(RelationshipIdentity).where(
                    RelationshipIdentity.identity_type == identity_type,
                    RelationshipIdentity.normalized_value == normalized,
                )
            )
        ).scalar_one_or_none()
        if identity is None:
            continue
        fact = await _explicit_fact(db, identity.relationship_id)
        if fact is not None:
            return identity.relationship_id, sanitize_preferences(_loads(fact.value_json))

    # Notification-driven messaging apps often expose only a display name rather
    # than a stable phone/email identity. Never merge by that name automatically.
    # A user may explicitly bind the exact displayed alias to a relationship, scoped
    # to the channel. Ambiguous duplicate aliases deliberately resolve to no profile.
    normalized_channel = str(channel or provider or "").strip().lower()
    alias = _bounded_text(party, 120).casefold()
    if normalized_channel in _ALLOWED_ALIAS_CHANNEL and alias:
        facts = list(
            (
                await db.execute(
                    select(RelationshipFact).where(
                        RelationshipFact.fact_key == FACT_KEY,
                        RelationshipFact.source_type == SOURCE_TYPE,
                    )
                )
            ).scalars()
        )
        matches: list[tuple[int, dict[str, Any]]] = []
        for fact in facts:
            prefs = sanitize_preferences(_loads(fact.value_json))
            aliases = (prefs.get("channel_aliases") or {}).get(normalized_channel) or []
            if any(_bounded_text(item, 120).casefold() == alias for item in aliases):
                matches.append((fact.relationship_id, prefs))
        if len(matches) == 1:
            return matches[0]
    return None, {}


async def communication_preferences_for_party(
    db: AsyncSession,
    party: str,
    *,
    channel: str = "",
    provider: str = "",
) -> dict[str, Any]:
    _, preferences = await resolve_relationship_for_party(
        db, party, channel=channel, provider=provider
    )
    return preferences


def relationship_reply_review_reason(
    preferences: dict[str, Any],
    *,
    incoming_text: str,
    proposed_reply: str,
) -> str:
    if not proposed_reply.strip():
        return ""
    if preferences.get("routine_auto_send") is False:
        return "relationship_pref_requires_review"
    haystack = f"{incoming_text}\n{proposed_reply}".casefold()
    for topic in preferences.get("approval_topics") or []:
        normalized = _bounded_text(topic, 80).casefold()
        if normalized and normalized in haystack:
            return f"relationship_approval_topic:{normalized[:60]}"
    return ""
