from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timedelta
from decimal import Decimal
from email.utils import getaddresses
from typing import Any, Iterable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import (
    CalendarEventMirror,
    CommunicationEvent,
    ContactRecord,
    EmailMessage,
    GmailOutboundMessage,
    OAuthConnection,
    RelationshipFact,
    RelationshipIdentity,
    RelationshipInteraction,
    RelationshipMemoryState,
    RelationshipProfile,
    VACommunicationThread,
    VAFollowUp,
)
from app.services.audit import write_audit

_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_PHONE_RE = re.compile(r"\d")


def _now() -> datetime:
    return datetime.utcnow()


def _loads_list(value: str) -> list[Any]:
    try:
        decoded = json.loads(value or "[]")
        return decoded if isinstance(decoded, list) else []
    except (TypeError, json.JSONDecodeError):
        return []


def _loads_dict(value: str) -> dict[str, Any]:
    try:
        decoded = json.loads(value or "{}")
        return decoded if isinstance(decoded, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def _normalize_email(value: str) -> str:
    normalized = value.strip().lower()
    return normalized if _EMAIL_RE.match(normalized) else ""


def _normalize_phone(value: str) -> str:
    raw = value.strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) < 7:
        return ""
    return ("+" if raw.startswith("+") else "") + digits


def _email_identities(value: str, *, source: str) -> list[tuple[str, str, str, str]]:
    rows: list[tuple[str, str, str, str]] = []
    for name, address in getaddresses([value or ""]):
        normalized = _normalize_email(address)
        if normalized:
            rows.append(("email", normalized, address.strip() or normalized, source))
    if rows:
        return rows
    normalized = _normalize_email(value)
    return [("email", normalized, value.strip() or normalized, source)] if normalized else []


def _identity_candidates(value: str, *, source: str) -> list[tuple[str, str, str, str]]:
    emails = _email_identities(value, source=source)
    if emails:
        return emails
    phone = _normalize_phone(value)
    if phone:
        return [("phone", phone, value.strip() or phone, source)]
    return []


def _dedupe_identities(
    rows: Iterable[tuple[str, str, str, str]],
) -> list[tuple[str, str, str, str]]:
    result: list[tuple[str, str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for identity_type, normalized, display, source in rows:
        key = (identity_type, normalized)
        if not normalized or key in seen:
            continue
        seen.add(key)
        result.append((identity_type, normalized, display, source))
    return result


def _canonical_key(identities: list[tuple[str, str, str, str]]) -> str:
    stable = "|".join(sorted(f"{kind}:{normalized}" for kind, normalized, _, _ in identities))
    digest = hashlib.sha256(stable.encode("utf-8")).hexdigest()[:32]
    return f"relationship:{digest}"


def _bounded(value: str, limit: int = 260) -> str:
    clean = " ".join((value or "").split())
    return clean[:limit]


async def _state(db: AsyncSession) -> RelationshipMemoryState:
    row = await db.get(RelationshipMemoryState, 1)
    if row is None:
        row = RelationshipMemoryState(id=1)
        db.add(row)
        await db.flush()
    return row


async def _merge_profiles(
    db: AsyncSession,
    target: RelationshipProfile,
    losers: list[RelationshipProfile],
) -> RelationshipProfile:
    for loser in losers:
        if loser.id == target.id:
            continue
        identities = list(
            (
                await db.execute(
                    select(RelationshipIdentity).where(RelationshipIdentity.relationship_id == loser.id)
                )
            ).scalars()
        )
        for identity in identities:
            duplicate = (
                await db.execute(
                    select(RelationshipIdentity).where(
                        RelationshipIdentity.relationship_id == target.id,
                        RelationshipIdentity.identity_type == identity.identity_type,
                        RelationshipIdentity.normalized_value == identity.normalized_value,
                    )
                )
            ).scalar_one_or_none()
            if duplicate is not None:
                duplicate.last_seen_at = max(duplicate.last_seen_at, identity.last_seen_at)
                await db.delete(identity)
            else:
                identity.relationship_id = target.id

        interactions = list(
            (
                await db.execute(
                    select(RelationshipInteraction).where(RelationshipInteraction.relationship_id == loser.id)
                )
            ).scalars()
        )
        for interaction in interactions:
            duplicate = (
                await db.execute(
                    select(RelationshipInteraction).where(
                        RelationshipInteraction.relationship_id == target.id,
                        RelationshipInteraction.source_type == interaction.source_type,
                        RelationshipInteraction.source_ref == interaction.source_ref,
                    )
                )
            ).scalar_one_or_none()
            if duplicate is not None:
                await db.delete(interaction)
            else:
                interaction.relationship_id = target.id

        facts = list(
            (
                await db.execute(select(RelationshipFact).where(RelationshipFact.relationship_id == loser.id))
            ).scalars()
        )
        for fact in facts:
            duplicate = (
                await db.execute(
                    select(RelationshipFact).where(
                        RelationshipFact.relationship_id == target.id,
                        RelationshipFact.fact_key == fact.fact_key,
                        RelationshipFact.source_type == fact.source_type,
                        RelationshipFact.source_ref == fact.source_ref,
                    )
                )
            ).scalar_one_or_none()
            if duplicate is not None:
                duplicate.last_seen_at = max(duplicate.last_seen_at, fact.last_seen_at)
                duplicate.value_json = fact.value_json
                await db.delete(fact)
            else:
                fact.relationship_id = target.id

        if not target.display_name and loser.display_name:
            target.display_name = loser.display_name
        if not target.organization and loser.organization:
            target.organization = loser.organization
        await write_audit(
            db,
            "relationship_profiles_merged",
            entity_type="relationship",
            entity_id=str(target.id),
            details={"merged_relationship_id": loser.id, "reason": "shared_verified_identity"},
        )
        await db.delete(loser)
    await db.flush()
    return target


async def _profile_for_identities(
    db: AsyncSession,
    identities: list[tuple[str, str, str, str]],
    *,
    display_name: str = "",
    organization: str = "",
    authoritative_profile: bool = False,
) -> RelationshipProfile | None:
    identities = _dedupe_identities(identities)
    if not identities:
        return None

    matched_ids: set[int] = set()
    for identity_type, normalized, _, _ in identities:
        existing = (
            await db.execute(
                select(RelationshipIdentity).where(
                    RelationshipIdentity.identity_type == identity_type,
                    RelationshipIdentity.normalized_value == normalized,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            matched_ids.add(existing.relationship_id)

    if matched_ids:
        profiles = [row for row in [await db.get(RelationshipProfile, row_id) for row_id in sorted(matched_ids)] if row]
        target = profiles[0]
        if len(profiles) > 1:
            target = await _merge_profiles(db, target, profiles[1:])
    else:
        target = RelationshipProfile(canonical_key=_canonical_key(identities))
        db.add(target)
        await db.flush()

    now = _now()
    for identity_type, normalized, display, source in identities:
        row = (
            await db.execute(
                select(RelationshipIdentity).where(
                    RelationshipIdentity.identity_type == identity_type,
                    RelationshipIdentity.normalized_value == normalized,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            row = RelationshipIdentity(
                relationship_id=target.id,
                identity_type=identity_type,
                normalized_value=normalized,
                display_value=display or normalized,
                source=source,
                first_seen_at=now,
                last_seen_at=now,
            )
            db.add(row)
        else:
            row.relationship_id = target.id
            if source == "google_contacts" or not row.display_value:
                row.display_value = display or row.display_value or normalized
            row.last_seen_at = now
            if source == "google_contacts":
                row.source = source

    clean_name = _bounded(display_name, 255)
    clean_org = _bounded(organization, 255)
    if clean_name and (authoritative_profile or not target.display_name):
        target.display_name = clean_name
    if clean_org and (authoritative_profile or not target.organization):
        target.organization = clean_org
    await db.flush()
    return target


async def _upsert_fact(
    db: AsyncSession,
    profile: RelationshipProfile,
    *,
    fact_key: str,
    value: Any,
    source_type: str,
    source_ref: str,
    confidence: Decimal = Decimal("1.0000"),
) -> None:
    if value in (None, "", [], {}):
        return
    row = (
        await db.execute(
            select(RelationshipFact).where(
                RelationshipFact.relationship_id == profile.id,
                RelationshipFact.fact_key == fact_key,
                RelationshipFact.source_type == source_type,
                RelationshipFact.source_ref == source_ref,
            )
        )
    ).scalar_one_or_none()
    now = _now()
    if row is None:
        row = RelationshipFact(
            relationship_id=profile.id,
            fact_key=fact_key,
            value_json=json.dumps(value, ensure_ascii=False, default=str),
            source_type=source_type,
            source_ref=source_ref,
            confidence=confidence,
            first_seen_at=now,
            last_seen_at=now,
        )
        db.add(row)
    else:
        row.value_json = json.dumps(value, ensure_ascii=False, default=str)
        row.last_seen_at = now
        row.confidence = confidence


async def _upsert_interaction(
    db: AsyncSession,
    profile: RelationshipProfile,
    *,
    source_type: str,
    source_ref: str,
    channel: str,
    direction: str,
    occurred_at: datetime | None,
    subject: str = "",
    summary: str = "",
    context: dict[str, Any] | None = None,
) -> None:
    if not source_ref:
        return
    row = (
        await db.execute(
            select(RelationshipInteraction).where(
                RelationshipInteraction.relationship_id == profile.id,
                RelationshipInteraction.source_type == source_type,
                RelationshipInteraction.source_ref == source_ref,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = RelationshipInteraction(
            relationship_id=profile.id,
            source_type=source_type,
            source_ref=source_ref,
        )
        db.add(row)
    row.channel = channel[:40] or "unknown"
    row.direction = direction[:20] or "shared"
    row.occurred_at = occurred_at
    row.subject = _bounded(subject, 500)
    row.summary = _bounded(summary, 500)
    row.context_json = json.dumps(context or {}, ensure_ascii=False, default=str)


async def _own_emails(db: AsyncSession) -> set[str]:
    rows = list(
        (
            await db.execute(
                select(OAuthConnection.account_key).where(
                    OAuthConnection.provider == "google",
                    OAuthConnection.enabled.is_(True),
                )
            )
        ).scalars()
    )
    return {email for email in (_normalize_email(value or "") for value in rows) if email}


async def _profile_for_party(
    db: AsyncSession,
    value: str,
    *,
    source: str,
    own_emails: set[str],
) -> RelationshipProfile | None:
    identities = [row for row in _identity_candidates(value, source=source) if not (row[0] == "email" and row[1] in own_emails)]
    if not identities:
        return None
    name = ""
    parsed = getaddresses([value or ""])
    if parsed and parsed[0][0].strip():
        name = parsed[0][0].strip()
    return await _profile_for_identities(db, identities, display_name=name)


async def _scan_contacts(db: AsyncSession, own_emails: set[str]) -> int:
    count = 0
    contacts = list((await db.execute(select(ContactRecord).order_by(ContactRecord.id))).scalars())
    for contact in contacts:
        identities: list[tuple[str, str, str, str]] = []
        for value in _loads_list(contact.emails_json):
            email = _normalize_email(str(value))
            if email and email not in own_emails:
                identities.append(("email", email, str(value), "google_contacts"))
        for value in _loads_list(contact.phones_json):
            phone = _normalize_phone(str(value))
            if phone:
                identities.append(("phone", phone, str(value), "google_contacts"))
        if not identities:
            continue
        profile = await _profile_for_identities(
            db,
            identities,
            display_name=contact.display_name,
            organization=contact.organization,
            authoritative_profile=True,
        )
        if profile is None:
            continue
        await _upsert_fact(
            db,
            profile,
            fact_key="display_name",
            value=contact.display_name,
            source_type="google_contact",
            source_ref=contact.resource_name,
        )
        await _upsert_fact(
            db,
            profile,
            fact_key="organization",
            value=contact.organization,
            source_type="google_contact",
            source_ref=contact.resource_name,
        )
        count += 1
    return count


async def _scan_gmail(db: AsyncSession, own_emails: set[str]) -> int:
    count = 0
    inbound = list((await db.execute(select(EmailMessage).order_by(EmailMessage.id))).scalars())
    for message in inbound:
        profile = await _profile_for_party(db, message.sender, source="gmail", own_emails=own_emails)
        if profile is None:
            continue
        category_key = (message.category or "").lower()
        sensitive_category = any(
            token in category_key
            for token in ("finance", "security", "legal", "medical", "health", "account", "identity")
        )
        await _upsert_interaction(
            db,
            profile,
            source_type="gmail_message",
            source_ref=message.provider_message_id,
            channel="email",
            direction="incoming",
            occurred_at=message.received_at or message.created_at,
            subject=message.subject,
            summary=f"Protected {message.category} email" if sensitive_category else message.snippet,
            context={"thread_id": message.thread_id, "category": message.category, "priority": message.priority},
        )
        count += 1

    outbound = list(
        (
            await db.execute(
                select(GmailOutboundMessage).where(
                    GmailOutboundMessage.status == "verified"
                )
            )
        ).scalars()
    )
    for message in outbound:
        profile = await _profile_for_party(db, message.recipient, source="gmail", own_emails=own_emails)
        if profile is None:
            continue
        await _upsert_interaction(
            db,
            profile,
            source_type="gmail_outbound",
            source_ref=message.idempotency_key,
            channel="email",
            direction="outgoing",
            occurred_at=message.verified_at or message.sent_at or message.updated_at,
            subject=message.subject,
            summary="",
            context={"gmail_thread_id": message.external_thread_id or message.gmail_thread_id},
        )
        count += 1
    return count


async def _scan_device_communications(db: AsyncSession, own_emails: set[str]) -> int:
    count = 0
    rows = list((await db.execute(select(CommunicationEvent).order_by(CommunicationEvent.id))).scalars())
    for event in rows:
        party = event.sender if event.direction == "incoming" else event.recipient
        profile = await _profile_for_party(db, party, source=event.provider or event.channel, own_emails=own_emails)
        if profile is None:
            continue
        protected_summary = f"Protected {event.channel} interaction" if event.protected else event.body
        await _upsert_interaction(
            db,
            profile,
            source_type="communication_event",
            source_ref=event.external_id,
            channel=event.channel,
            direction=event.direction,
            occurred_at=event.occurred_at or event.created_at,
            subject="" if event.category in {"", "unclassified"} else event.category,
            summary=protected_summary,
            context={"provider": event.provider, "event_type": event.event_type, "protected": event.protected},
        )
        count += 1
    return count


async def _scan_calendar(db: AsyncSession, own_emails: set[str]) -> int:
    count = 0
    events = list((await db.execute(select(CalendarEventMirror).order_by(CalendarEventMirror.id))).scalars())
    for event in events:
        attendees = [item for item in _loads_list(event.attendees_json) if isinstance(item, dict)]
        organizer = _loads_dict(event.organizer_json)
        parties = list(attendees)
        if organizer.get("email"):
            parties.append(organizer)
        seen_emails: set[str] = set()
        for attendee in parties:
            email = _normalize_email(str(attendee.get("email") or ""))
            if not email or email in own_emails or email in seen_emails:
                continue
            seen_emails.add(email)
            profile = await _profile_for_identities(
                db,
                [("email", email, email, "google_calendar")],
                display_name=str(attendee.get("displayName") or ""),
            )
            if profile is None:
                continue
            await _upsert_interaction(
                db,
                profile,
                source_type="calendar_event",
                source_ref=f"{event.provider_event_id}:{email}",
                channel="calendar",
                direction="shared",
                occurred_at=event.start_at or event.created_at,
                subject=event.summary,
                summary=event.description,
                context={
                    "provider_event_id": event.provider_event_id,
                    "response_status": attendee.get("responseStatus"),
                    "location": event.location,
                    "status": event.status,
                    "organizer": email == _normalize_email(str(organizer.get("email") or "")),
                },
            )
            count += 1
    return count


async def _scan_followups(db: AsyncSession, own_emails: set[str]) -> int:
    touched: set[int] = set()
    profiles = list((await db.execute(select(RelationshipProfile))).scalars())
    for profile in profiles:
        profile.next_follow_up_at = None
        profile.waiting_on_counterparty = False

    threads = list(
        (
            await db.execute(
                select(VACommunicationThread).where(VACommunicationThread.status.not_in(["completed", "cancelled"]))
            )
        ).scalars()
    )
    for thread in threads:
        profile = await _profile_for_party(
            db,
            thread.participant,
            source=thread.provider or thread.channel,
            own_emails=own_emails,
        )
        if profile is None:
            profile = await _profile_for_party(
                db,
                thread.thread_key,
                source=thread.provider or thread.channel,
                own_emails=own_emails,
            )
        if profile is None:
            continue
        if thread.waiting_on == "counterparty":
            profile.waiting_on_counterparty = True
        if thread.next_follow_up_at and (
            profile.next_follow_up_at is None or thread.next_follow_up_at < profile.next_follow_up_at
        ):
            profile.next_follow_up_at = thread.next_follow_up_at
        touched.add(profile.id)

    followups = list(
        (
            await db.execute(select(VAFollowUp).where(VAFollowUp.status.in_(["pending", "dispatching"])))
        ).scalars()
    )
    for followup in followups:
        profile = await _profile_for_party(db, followup.target, source=followup.channel, own_emails=own_emails)
        if profile is None:
            continue
        if profile.next_follow_up_at is None or followup.due_at < profile.next_follow_up_at:
            profile.next_follow_up_at = followup.due_at
        profile.waiting_on_counterparty = True
        touched.add(profile.id)
    return len(touched)


async def _refresh_aggregates(db: AsyncSession) -> int:
    profiles = list((await db.execute(select(RelationshipProfile).order_by(RelationshipProfile.id))).scalars())
    now = _now()
    for profile in profiles:
        identities = list(
            (
                await db.execute(
                    select(RelationshipIdentity)
                    .where(RelationshipIdentity.relationship_id == profile.id)
                    .order_by(RelationshipIdentity.source.desc(), RelationshipIdentity.id.asc())
                )
            ).scalars()
        )
        profile.primary_email = next((row.normalized_value for row in identities if row.identity_type == "email"), "")
        profile.primary_phone = next((row.display_value for row in identities if row.identity_type == "phone"), "")

        interactions = list(
            (
                await db.execute(
                    select(RelationshipInteraction)
                    .where(RelationshipInteraction.relationship_id == profile.id)
                    .order_by(RelationshipInteraction.occurred_at.desc().nullslast(), RelationshipInteraction.id.desc())
                )
            ).scalars()
        )
        profile.interaction_count = len(interactions)
        dated = [row for row in interactions if row.occurred_at is not None and row.occurred_at <= now]
        profile.last_interaction_at = dated[0].occurred_at if dated else None
        inbound = next((row.occurred_at for row in interactions if row.direction == "incoming" and row.occurred_at and row.occurred_at <= now), None)
        outbound = next((row.occurred_at for row in interactions if row.direction == "outgoing" and row.occurred_at and row.occurred_at <= now), None)
        profile.last_inbound_at = inbound
        profile.last_outbound_at = outbound

        counts = Counter(row.channel for row in interactions if row.channel and row.channel != "calendar")
        if counts:
            profile.preferred_channel = counts.most_common(1)[0][0]
        else:
            profile.preferred_channel = ""

        topics: list[str] = []
        for row in interactions:
            candidate = _bounded(row.subject or row.summary, 120)
            if candidate and candidate not in topics:
                topics.append(candidate)
            if len(topics) >= 3:
                break
        profile.memory_summary = " · ".join(topics)

        score = min(60, len(interactions) * 3)
        if profile.last_interaction_at:
            age = now - profile.last_interaction_at
            if age <= timedelta(days=7):
                score += 25
            elif age <= timedelta(days=30):
                score += 15
            elif age <= timedelta(days=90):
                score += 5
        if profile.waiting_on_counterparty:
            score += 10
        profile.engagement_score = min(100, score)
    await db.flush()
    return len(profiles)


async def reconcile_relationship_memory(db: AsyncSession) -> dict[str, int]:
    state = await _state(db)
    state.last_reconcile_attempt_at = _now()
    await db.commit()
    try:
        own_emails = await _own_emails(db)
        contacts = await _scan_contacts(db, own_emails)
        gmail = await _scan_gmail(db, own_emails)
        communications = await _scan_device_communications(db, own_emails)
        calendar = await _scan_calendar(db, own_emails)
        followups = await _scan_followups(db, own_emails)
        profiles = await _refresh_aggregates(db)
        identities = int((await db.execute(select(func.count(RelationshipIdentity.id)))).scalar_one())
        interactions = int((await db.execute(select(func.count(RelationshipInteraction.id)))).scalar_one())
        state = await _state(db)
        state.last_reconcile_at = _now()
        state.profile_count = profiles
        state.identity_count = identities
        state.interaction_count = interactions
        state.last_error = ""
        await write_audit(
            db,
            "relationship_memory_reconciled",
            entity_type="relationship_memory",
            entity_id="1",
            details={
                "profiles": profiles,
                "identities": identities,
                "interactions": interactions,
                "contacts_scanned": contacts,
                "gmail_interactions": gmail,
                "device_interactions": communications,
                "calendar_interactions": calendar,
                "followup_profiles": followups,
            },
        )
        await db.commit()
        return {
            "profiles": profiles,
            "identities": identities,
            "interactions": interactions,
            "contacts_scanned": contacts,
            "gmail_interactions": gmail,
            "device_interactions": communications,
            "calendar_interactions": calendar,
            "followup_profiles": followups,
        }
    except Exception as exc:
        await db.rollback()
        state = await _state(db)
        state.last_reconcile_attempt_at = _now()
        state.last_error = str(exc)[:2000]
        await db.commit()
        raise


def _identity_public(row: RelationshipIdentity) -> dict[str, Any]:
    return {
        "type": row.identity_type,
        "value": row.display_value or row.normalized_value,
        "normalized_value": row.normalized_value,
        "source": row.source,
        "first_seen_at": row.first_seen_at.isoformat() if row.first_seen_at else None,
        "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
    }


def _interaction_public(row: RelationshipInteraction) -> dict[str, Any]:
    return {
        "id": row.id,
        "source_type": row.source_type,
        "source_ref": row.source_ref,
        "channel": row.channel,
        "direction": row.direction,
        "occurred_at": row.occurred_at.isoformat() if row.occurred_at else None,
        "subject": row.subject,
        "summary": row.summary,
        "context": _loads_dict(row.context_json),
    }


def _fact_public(row: RelationshipFact) -> dict[str, Any]:
    try:
        value = json.loads(row.value_json or "null")
    except json.JSONDecodeError:
        value = row.value_json
    return {
        "key": row.fact_key,
        "value": value,
        "source_type": row.source_type,
        "source_ref": row.source_ref,
        "confidence": float(row.confidence),
        "first_seen_at": row.first_seen_at.isoformat() if row.first_seen_at else None,
        "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
    }


def _profile_public(profile: RelationshipProfile) -> dict[str, Any]:
    return {
        "id": profile.id,
        "canonical_key": profile.canonical_key,
        "display_name": profile.display_name,
        "organization": profile.organization,
        "primary_email": profile.primary_email,
        "primary_phone": profile.primary_phone,
        "preferred_channel": profile.preferred_channel,
        "interaction_count": profile.interaction_count,
        "engagement_score": profile.engagement_score,
        "last_interaction_at": profile.last_interaction_at.isoformat() if profile.last_interaction_at else None,
        "last_inbound_at": profile.last_inbound_at.isoformat() if profile.last_inbound_at else None,
        "last_outbound_at": profile.last_outbound_at.isoformat() if profile.last_outbound_at else None,
        "next_follow_up_at": profile.next_follow_up_at.isoformat() if profile.next_follow_up_at else None,
        "waiting_on_counterparty": profile.waiting_on_counterparty,
        "memory_summary": profile.memory_summary,
        "created_at": profile.created_at.isoformat() if profile.created_at else None,
        "updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
    }


async def relationship_memory_status(db: AsyncSession) -> dict[str, Any]:
    state = await _state(db)
    due = int(
        (
            await db.execute(
                select(func.count(RelationshipProfile.id)).where(
                    RelationshipProfile.next_follow_up_at.is_not(None),
                    RelationshipProfile.next_follow_up_at <= _now(),
                )
            )
        ).scalar_one()
    )
    waiting = int(
        (
            await db.execute(
                select(func.count(RelationshipProfile.id)).where(RelationshipProfile.waiting_on_counterparty.is_(True))
            )
        ).scalar_one()
    )
    return {
        "profiles": state.profile_count,
        "identities": state.identity_count,
        "interactions": state.interaction_count,
        "waiting_on_counterparty": waiting,
        "followups_due": due,
        "last_reconcile_attempt_at": state.last_reconcile_attempt_at.isoformat() if state.last_reconcile_attempt_at else None,
        "last_reconcile_at": state.last_reconcile_at.isoformat() if state.last_reconcile_at else None,
        "last_error": state.last_error,
    }


async def list_relationships(
    db: AsyncSession,
    *,
    limit: int = 200,
    query: str = "",
) -> list[dict[str, Any]]:
    statement = select(RelationshipProfile)
    clean_query = query.strip().lower()
    if clean_query:
        like = f"%{clean_query}%"
        statement = statement.where(
            func.lower(
                RelationshipProfile.display_name
                + " "
                + RelationshipProfile.organization
                + " "
                + RelationshipProfile.primary_email
                + " "
                + RelationshipProfile.primary_phone
            ).like(like)
        )
    statement = statement.order_by(
        RelationshipProfile.waiting_on_counterparty.desc(),
        RelationshipProfile.last_interaction_at.desc().nullslast(),
        RelationshipProfile.engagement_score.desc(),
        RelationshipProfile.display_name.asc(),
    ).limit(max(1, min(limit, 1000)))
    return [_profile_public(row) for row in (await db.execute(statement)).scalars()]


async def relationship_detail(db: AsyncSession, relationship_id: int) -> dict[str, Any]:
    profile = await db.get(RelationshipProfile, relationship_id)
    if profile is None:
        raise LookupError("relationship not found")
    identities = list(
        (
            await db.execute(
                select(RelationshipIdentity)
                .where(RelationshipIdentity.relationship_id == profile.id)
                .order_by(RelationshipIdentity.identity_type, RelationshipIdentity.id)
            )
        ).scalars()
    )
    interactions = list(
        (
            await db.execute(
                select(RelationshipInteraction)
                .where(RelationshipInteraction.relationship_id == profile.id)
                .order_by(RelationshipInteraction.occurred_at.desc().nullslast(), RelationshipInteraction.id.desc())
                .limit(50)
            )
        ).scalars()
    )
    facts = list(
        (
            await db.execute(
                select(RelationshipFact)
                .where(RelationshipFact.relationship_id == profile.id)
                .order_by(RelationshipFact.fact_key, RelationshipFact.last_seen_at.desc())
            )
        ).scalars()
    )
    result = _profile_public(profile)
    result["identities"] = [_identity_public(row) for row in identities]
    result["recent_interactions"] = [_interaction_public(row) for row in interactions]
    result["facts"] = [_fact_public(row) for row in facts]
    return result
