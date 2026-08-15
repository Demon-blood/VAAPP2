from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timedelta
from statistics import median
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import CommunicationAction, CommunicationEvent, RelationshipFact, RelationshipIdentity, RelationshipProfile
from app.services.audit import write_audit
from app.services.relationship_preferences import (
    communication_preferences_for_relationship,
    preferences_for_ai,
    resolve_relationship_for_party,
)

FACT_KEY = "learned_communication_style"
SOURCE_TYPE = "verified_user_outbound_aggregate"
STYLE_VERSION = 1
MIN_SAMPLES = 3
MAX_SAMPLES = 80
MAX_SCAN = 500
_REFRESH_AFTER = timedelta(minutes=15)

# v1.0.5 only learns from device-observed outgoing history after excluding known VA-generated sends. The
# Android SMS history provider reads the OS sent/outbox store. Notification-only
# apps do not expose historical sent messages through NotificationListenerService,
# so Messenger/WhatsApp/Signal/Telegram style can inherit this relationship-level
# profile or explicit examples but is never fabricated from incoming messages.
_VERIFIED_USER_OUTBOUND_PROVIDERS = {"android_sms_history"}

_SENSITIVE_RE = re.compile(
    r"(?i)(?:https?://|www\.|\b(?:password|passcode|one[- ]time|otp|pin|cvv|iban|"
    r"verification code|security code|account number|card number|passport|national id)\b)"
)
_LONG_NUMBER_RE = re.compile(r"\b\d{6,}\b")
_IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b", re.IGNORECASE)
_EMOJI_RE = re.compile("[\U0001F1E6-\U0001F1FF\U0001F300-\U0001FAFF\u2600-\u27BF]")


def _now() -> datetime:
    return datetime.utcnow()


def _loads(value: str | None) -> dict[str, Any]:
    try:
        decoded = json.loads(value or "{}")
        return decoded if isinstance(decoded, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def _normalize_phone(value: str) -> str:
    raw = (value or "").strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) < 7:
        return ""
    return ("+" if raw.startswith("+") else "") + digits


def _normalize_message(value: str) -> str:
    # Preserve line-break behavior while normalizing repeated horizontal whitespace.
    lines = [" ".join(line.split()) for line in str(value or "").replace("\r", "").split("\n")]
    return "\n".join(line for line in lines if line).strip()[:500]


def _safe_sample(value: str) -> str:
    text = _normalize_message(value)
    if not text or len(text) > 500:
        return ""
    if _SENSITIVE_RE.search(text) or _LONG_NUMBER_RE.search(text) or _IBAN_RE.search(text):
        return ""
    return text


def _style_fact_ref(relationship_id: int) -> str:
    return f"relationship:{relationship_id}:learned_communication_style:v{STYLE_VERSION}"


async def _style_fact(db: AsyncSession, relationship_id: int) -> RelationshipFact | None:
    return (
        await db.execute(
            select(RelationshipFact).where(
                RelationshipFact.relationship_id == relationship_id,
                RelationshipFact.fact_key == FACT_KEY,
                RelationshipFact.source_type == SOURCE_TYPE,
                RelationshipFact.source_ref == _style_fact_ref(relationship_id),
            )
        )
    ).scalar_one_or_none()


async def get_relationship_learned_style(db: AsyncSession, relationship_id: int) -> dict[str, Any]:
    if await db.get(RelationshipProfile, relationship_id) is None:
        raise LookupError("relationship not found")
    preferences = await communication_preferences_for_relationship(db, relationship_id)
    enabled = preferences.get("learn_from_history") is True
    fact = await _style_fact(db, relationship_id)
    if fact is None:
        return {
            "enabled": enabled,
            "ready": False,
            "sample_count": 0,
            "minimum_samples": MIN_SAMPLES,
            "style": {},
            "provenance": None,
        }
    value = _loads(fact.value_json)
    count = int(value.get("sample_count") or 0)
    return {
        "enabled": enabled,
        "ready": enabled and count >= MIN_SAMPLES,
        "sample_count": count,
        "minimum_samples": MIN_SAMPLES,
        "style": value,
        "provenance": {
            "source_type": fact.source_type,
            "source_ref": fact.source_ref,
            "confidence": str(fact.confidence),
            "first_seen_at": fact.first_seen_at,
            "last_seen_at": fact.last_seen_at,
        },
    }


async def _relationship_phone_identities(db: AsyncSession, relationship_id: int) -> set[str]:
    rows = list(
        (
            await db.execute(
                select(RelationshipIdentity).where(
                    RelationshipIdentity.relationship_id == relationship_id,
                    RelationshipIdentity.identity_type == "phone",
                )
            )
        ).scalars()
    )
    return {value for value in (_normalize_phone(row.normalized_value or row.display_value) for row in rows) if value}


async def _successful_va_sms_signatures(db: AsyncSession) -> list[tuple[str, str, datetime]]:
    rows = list(
        (
            await db.execute(
                select(CommunicationAction)
                .where(CommunicationAction.status.in_(["dispatched", "sent", "delivered", "completed"]))
                .order_by(CommunicationAction.created_at.desc())
                .limit(MAX_SCAN)
            )
        ).scalars()
    )
    result: list[tuple[str, str, datetime]] = []
    for row in rows:
        payload = _loads(row.payload_json)
        if str(payload.get("channel") or "").lower() != "sms" or row.action_type != "reply":
            continue
        target = _normalize_phone(row.target)
        text = _normalize_message(str(payload.get("text") or ""))
        if target and text:
            result.append((target, text, row.created_at))
    return result


def _looks_va_generated_history(
    *,
    target: str,
    text: str,
    occurred_at: datetime | None,
    va_signatures: list[tuple[str, str, datetime]],
) -> bool:
    if occurred_at is None:
        return False
    for action_target, action_text, action_at in va_signatures:
        if action_target != target or action_text != text:
            continue
        if abs((occurred_at - action_at).total_seconds()) <= 2 * 24 * 60 * 60:
            return True
    return False


async def _verified_user_samples(db: AsyncSession, relationship_id: int) -> list[dict[str, Any]]:
    phones = await _relationship_phone_identities(db, relationship_id)
    if not phones:
        return []
    va_signatures = await _successful_va_sms_signatures(db)
    rows = list(
        (
            await db.execute(
                select(CommunicationEvent)
                .where(
                    CommunicationEvent.direction == "outgoing",
                    CommunicationEvent.channel == "sms",
                    CommunicationEvent.provider.in_(_VERIFIED_USER_OUTBOUND_PROVIDERS),
                )
                .order_by(CommunicationEvent.occurred_at.desc().nullslast(), CommunicationEvent.id.desc())
                .limit(MAX_SCAN)
            )
        ).scalars()
    )
    samples: list[dict[str, Any]] = []
    seen: set[str] = set()
    for event in rows:
        target = _normalize_phone(event.recipient)
        if not target or target not in phones or event.protected:
            continue
        text = _safe_sample(event.body)
        if not text:
            continue
        if _looks_va_generated_history(
            target=target,
            text=text,
            occurred_at=event.occurred_at,
            va_signatures=va_signatures,
        ):
            continue
        sample_key = hashlib.sha256(f"{event.external_id}:{text}".encode("utf-8")).hexdigest()
        if sample_key in seen:
            continue
        seen.add(sample_key)
        samples.append(
            {
                "text": text,
                "channel": event.channel,
                "source_ref": event.external_id,
                "occurred_at": event.occurred_at or event.created_at,
            }
        )
        if len(samples) >= MAX_SAMPLES:
            break
    return samples


def _ratio(count: int, total: int) -> float:
    return round(count / total, 2) if total else 0.0


def _first_alpha(text: str) -> str:
    return next((ch for ch in text if ch.isalpha()), "")


def _build_style(samples: list[dict[str, Any]]) -> dict[str, Any]:
    texts = [str(item["text"]) for item in samples]
    lengths = [len(text) for text in texts]
    word_counts = [len(text.split()) for text in texts]
    emoji_counter: Counter[str] = Counter()
    for text in texts:
        emoji_counter.update(_EMOJI_RE.findall(text))

    total = len(texts)
    lower_starts = sum(1 for text in texts if (_first_alpha(text).islower() if _first_alpha(text) else False))
    punctuated = sum(1 for text in texts if text.rstrip().endswith((".", "!", "?")))
    exclamations = sum(1 for text in texts if "!" in text)
    questions = sum(1 for text in texts if "?" in text)
    multiline = sum(1 for text in texts if "\n" in text)
    emoji_messages = sum(1 for text in texts if _EMOJI_RE.search(text))
    channels = Counter(str(item.get("channel") or "unknown") for item in samples)

    target_length = median(lengths) if lengths else 0
    ranked = sorted(
        texts,
        key=lambda text: (abs(len(text) - target_length), len(text)),
    )
    examples: list[str] = []
    for text in ranked:
        compact = text[:240]
        if compact and compact.casefold() not in {item.casefold() for item in examples}:
            examples.append(compact)
        if len(examples) >= 5:
            break

    avg_words = round(sum(word_counts) / total, 1) if total else 0.0
    avg_chars = round(sum(lengths) / total, 1) if total else 0.0
    top_emojis = [item for item, _ in emoji_counter.most_common(8)]
    descriptors: list[str] = []
    descriptors.append("very short" if avg_words <= 5 else "short" if avg_words <= 12 else "medium-length" if avg_words <= 30 else "longer")
    if _ratio(lower_starts, total) >= 0.65:
        descriptors.append("often starts lowercase")
    if _ratio(punctuated, total) <= 0.35:
        descriptors.append("usually omits terminal punctuation")
    elif _ratio(punctuated, total) >= 0.8:
        descriptors.append("usually uses terminal punctuation")
    if _ratio(emoji_messages, total) >= 0.35:
        descriptors.append("uses emojis regularly")
    if _ratio(multiline, total) >= 0.3:
        descriptors.append("often uses line breaks")

    source_times = [item.get("occurred_at") for item in samples if item.get("occurred_at") is not None]
    source_material = "\n".join(sorted(str(item.get("source_ref") or "") for item in samples))
    return {
        "version": STYLE_VERSION,
        "sample_count": total,
        "channels": dict(channels),
        "average_words": avg_words,
        "average_characters": avg_chars,
        "lowercase_start_ratio": _ratio(lower_starts, total),
        "terminal_punctuation_ratio": _ratio(punctuated, total),
        "exclamation_ratio": _ratio(exclamations, total),
        "question_ratio": _ratio(questions, total),
        "multiline_ratio": _ratio(multiline, total),
        "emoji_message_ratio": _ratio(emoji_messages, total),
        "common_emojis": top_emojis,
        "representative_examples": examples,
        "summary": ", ".join(descriptors),
        "source_first_at": min(source_times).isoformat() if source_times else None,
        "source_last_at": max(source_times).isoformat() if source_times else None,
        "source_digest": hashlib.sha256(source_material.encode("utf-8")).hexdigest(),
        "generated_at": _now().isoformat(),
    }


async def refresh_relationship_style(db: AsyncSession, relationship_id: int) -> dict[str, Any]:
    if await db.get(RelationshipProfile, relationship_id) is None:
        raise LookupError("relationship not found")
    preferences = await communication_preferences_for_relationship(db, relationship_id)
    fact = await _style_fact(db, relationship_id)
    if preferences.get("learn_from_history") is not True:
        if fact is not None:
            await db.delete(fact)
            await write_audit(
                db,
                "relationship_learned_style_cleared",
                entity_type="relationship",
                entity_id=str(relationship_id),
                details={"reason": "learning_disabled"},
            )
            await db.commit()
        return await get_relationship_learned_style(db, relationship_id)

    samples = await _verified_user_samples(db, relationship_id)
    style = _build_style(samples)
    encoded = json.dumps(style, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    now = _now()
    if fact is None:
        fact = RelationshipFact(
            relationship_id=relationship_id,
            fact_key=FACT_KEY,
            value_json=encoded,
            source_type=SOURCE_TYPE,
            source_ref=_style_fact_ref(relationship_id),
            confidence=1 if len(samples) >= MIN_SAMPLES else 0.5,
            first_seen_at=now,
            last_seen_at=now,
        )
        db.add(fact)
    else:
        fact.value_json = encoded
        fact.confidence = 1 if len(samples) >= MIN_SAMPLES else 0.5
        fact.last_seen_at = now

    await write_audit(
        db,
        "relationship_learned_style_refreshed",
        entity_type="relationship",
        entity_id=str(relationship_id),
        details={
            "sample_count": len(samples),
            "ready": len(samples) >= MIN_SAMPLES,
            "channels": style.get("channels") or {},
            "source_type": SOURCE_TYPE,
        },
    )
    await db.commit()
    return await get_relationship_learned_style(db, relationship_id)


async def refresh_enabled_relationship_styles(db: AsyncSession) -> dict[str, int]:
    profiles = list((await db.execute(select(RelationshipProfile.id).order_by(RelationshipProfile.id))).scalars())
    refreshed = 0
    ready = 0
    for relationship_id in profiles:
        preferences = await communication_preferences_for_relationship(db, int(relationship_id))
        if preferences.get("learn_from_history") is not True:
            continue
        result = await refresh_relationship_style(db, int(relationship_id))
        refreshed += 1
        ready += int(result.get("ready") is True)
    return {"refreshed": refreshed, "ready": ready}


def _style_for_ai(style: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "sample_count",
        "channels",
        "average_words",
        "average_characters",
        "lowercase_start_ratio",
        "terminal_punctuation_ratio",
        "exclamation_ratio",
        "question_ratio",
        "multiline_ratio",
        "emoji_message_ratio",
        "common_emojis",
        "representative_examples",
        "summary",
    }
    return {key: style.get(key) for key in allowed if style.get(key) not in (None, "", [], {})}


async def relationship_reply_context_for_party(
    db: AsyncSession,
    party: str,
    *,
    channel: str = "",
    provider: str = "",
) -> tuple[dict[str, Any], dict[str, Any]]:
    relationship_id, preferences = await resolve_relationship_for_party(
        db,
        party,
        channel=channel,
        provider=provider,
    )
    ai_context = preferences_for_ai(preferences)
    if relationship_id is None or preferences.get("learn_from_history") is not True:
        return preferences, ai_context

    fact = await _style_fact(db, relationship_id)
    if fact is None or (fact.last_seen_at and _now() - fact.last_seen_at >= _REFRESH_AFTER):
        await refresh_relationship_style(db, relationship_id)
        fact = await _style_fact(db, relationship_id)
    if fact is None:
        return preferences, ai_context
    style = _loads(fact.value_json)
    if int(style.get("sample_count") or 0) >= MIN_SAMPLES:
        ai_context["learned_writing_style"] = _style_for_ai(style)
    return preferences, ai_context
