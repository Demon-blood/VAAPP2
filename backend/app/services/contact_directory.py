from __future__ import annotations

import json
import re
from datetime import datetime
from email.utils import parseaddr
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.google_api import get_google_connection, people_service
from app.models.entities import RelationshipFact, RelationshipIdentity, RelationshipProfile
from app.models.people_entities import ContactSourceRecord
from app.services.audit import write_audit
from app.services.relationship_memory import _profile_for_identities, _refresh_aggregates, _upsert_fact
from app.services.relationship_preferences import FACT_KEY as COMMUNICATION_PREFERENCES_FACT_KEY


def _now() -> datetime:
    return datetime.utcnow()


def _clean(value: Any, limit: int = 255) -> str:
    return " ".join(str(value or "").split())[:limit]


def _normalize_email(value: str) -> str:
    _, parsed = parseaddr(value or "")
    candidate = (parsed or value or "").strip().lower()
    return candidate if re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", candidate) else ""


def _normalize_phone(value: str) -> str:
    raw = (value or "").strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) < 7:
        return ""
    if raw.startswith("+"):
        return "+" + digits
    if digits.startswith("00") and len(digits) > 8:
        return "+" + digits[2:]
    return digits


def _unique_strings(values: Any, *, limit: int = 50, item_limit: int = 255) -> list[str]:
    if not isinstance(values, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for raw in values[:limit]:
        value = _clean(raw, item_limit)
        key = value.casefold()
        if value and key not in seen:
            result.append(value)
            seen.add(key)
    return result


def _safe_relations(values: Any) -> list[dict[str, str]]:
    if not isinstance(values, list):
        return []
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for raw in values[:30]:
        if not isinstance(raw, dict):
            continue
        relation_type = _clean(raw.get("type") or raw.get("relation") or raw.get("label"), 80)
        person = _clean(raw.get("person") or raw.get("name"), 255)
        if not relation_type and not person:
            continue
        key = (relation_type.casefold(), person.casefold())
        if key in seen:
            continue
        seen.add(key)
        result.append({"type": relation_type, "person": person})
    return result


def _json_list(value: str) -> list[Any]:
    try:
        decoded = json.loads(value or "[]")
        return decoded if isinstance(decoded, list) else []
    except (TypeError, json.JSONDecodeError):
        return []


def _contact_identities(
    emails: list[str],
    phones: list[str],
    *,
    source: str,
) -> list[tuple[str, str, str, str]]:
    identities: list[tuple[str, str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for raw in emails:
        normalized = _normalize_email(raw)
        key = ("email", normalized)
        if normalized and key not in seen:
            identities.append(("email", normalized, raw, source))
            seen.add(key)
    for raw in phones:
        normalized = _normalize_phone(raw)
        key = ("phone", normalized)
        if normalized and key not in seen:
            identities.append(("phone", normalized, raw, source))
            seen.add(key)
    return identities


def category_hint(groups: list[str], relations: list[dict[str, str]]) -> tuple[str, str]:
    """Suggest a reply-profile category from source metadata without persisting authority."""

    evidence: list[str] = []
    for relation in relations:
        relation_type = _clean(relation.get("type"), 80).casefold()
        person = _clean(relation.get("person"), 120)
        if relation_type:
            evidence.append(f"relation:{relation_type}{':' + person if person else ''}")
    for group in groups:
        cleaned = _clean(group, 120)
        if cleaned:
            evidence.append(f"group:{cleaned}")

    haystack = " ".join(evidence).casefold()
    if any(
        token in haystack
        for token in (
            "spouse",
            "partner",
            "husband",
            "wife",
            "echtgenoot",
            "echtgenote",
            "conjoint",
            "conjointe",
            "époux",
            "épouse",
        )
    ):
        return "partner", " · ".join(evidence[:3])
    if any(
        token in haystack
        for token in (
            "family",
            "familie",
            "famille",
            "mother",
            "moeder",
            "mère",
            "father",
            "vader",
            "père",
            "parent",
            "ouder",
            "child",
            "kind",
            "enfant",
            "son",
            "zoon",
            "fils",
            "daughter",
            "dochter",
            "fille",
            "brother",
            "broer",
            "frère",
            "sister",
            "zus",
            "sœur",
            "soeur",
            "sibling",
            "relative",
            "grandparent",
            "grandmother",
            "grandfather",
            "aunt",
            "uncle",
            "cousin",
        )
    ):
        return "family", " · ".join(evidence[:3])
    if any(token in haystack for token in ("friend", "vriend", "vriendin", "ami", "amie")):
        return "friend", " · ".join(evidence[:3])
    if any(token in haystack for token in ("client", "customer", "klant")):
        return "client", " · ".join(evidence[:3])
    if any(
        token in haystack
        for token in ("supplier", "provider", "vendor", "leverancier", "fournisseur")
    ):
        return "provider", " · ".join(evidence[:3])
    if any(
        token in haystack
        for token in (
            "coworker",
            "co-worker",
            "colleague",
            "collega",
            "collègue",
            "work",
            "werk",
            "travail",
            "team",
        )
    ):
        return "colleague", " · ".join(evidence[:3])
    return "other", " · ".join(evidence[:3])


async def _source_row(
    db: AsyncSession,
    *,
    source_type: str,
    source_account: str,
    source_id: str,
) -> ContactSourceRecord:
    row = (
        await db.execute(
            select(ContactSourceRecord).where(
                ContactSourceRecord.source_type == source_type,
                ContactSourceRecord.source_account == source_account,
                ContactSourceRecord.source_id == source_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = ContactSourceRecord(
            source_type=source_type,
            source_account=source_account,
            source_id=source_id,
        )
        db.add(row)
        await db.flush()
    return row


async def _apply_source_contact(
    db: AsyncSession,
    *,
    source_type: str,
    source_account: str,
    source_id: str,
    contact: dict[str, Any],
    sync_marker: str,
) -> tuple[ContactSourceRecord, bool]:
    display_name = _clean(contact.get("display_name"), 255)
    emails = _unique_strings(contact.get("emails"), limit=50, item_limit=500)
    phones = _unique_strings(contact.get("phones"), limit=50, item_limit=120)
    organization = _clean(contact.get("organization"), 255)
    job_title = _clean(contact.get("job_title"), 255)
    department = _clean(contact.get("department"), 255)
    nickname = _clean(contact.get("nickname"), 255)
    groups = _unique_strings(contact.get("groups"), limit=50, item_limit=160)
    relations = _safe_relations(contact.get("relations"))

    row = await _source_row(
        db,
        source_type=source_type,
        source_account=source_account,
        source_id=source_id,
    )
    row.display_name = display_name
    row.emails_json = json.dumps(emails, ensure_ascii=False)
    row.phones_json = json.dumps(phones, ensure_ascii=False)
    row.organization = organization
    row.job_title = job_title
    row.department = department
    row.nickname = nickname
    row.groups_json = json.dumps(groups, ensure_ascii=False)
    row.relations_json = json.dumps(relations, ensure_ascii=False)
    row.starred = contact.get("starred") is True
    row.active = True
    row.sync_marker = sync_marker
    row.last_synced_at = _now()

    identities = _contact_identities(emails, phones, source=source_type)
    profile = None
    if identities:
        # The Android phone book is the user's local address book, so its display
        # name may become presentation-authoritative. Google fills missing profile
        # presentation but does not overwrite a phone-book name.
        profile = await _profile_for_identities(
            db,
            identities,
            display_name=display_name,
            organization=organization,
            authoritative_profile=source_type == "android_contacts",
        )
    row.relationship_id = profile.id if profile is not None else None

    if profile is not None:
        facts = {
            "display_name": display_name,
            "organization": organization,
            "job_title": job_title,
            "department": department,
            "nickname": nickname,
            "contact_groups": groups,
            "contact_relations": relations,
        }
        for fact_key, value in facts.items():
            if value in ("", [], {}):
                continue
            await _upsert_fact(
                db,
                profile,
                fact_key=fact_key,
                value=value,
                source_type=source_type,
                source_ref=source_id,
            )
    return row, profile is not None


async def ingest_device_contact_snapshot(
    db: AsyncSession,
    *,
    device_id: int,
    snapshot_id: str,
    contacts: list[dict[str, Any]],
    snapshot_complete: bool,
) -> dict[str, int | bool]:
    source_account = f"device:{device_id}"
    processed = 0
    linked = 0
    unlinked = 0
    for contact in contacts:
        source_id = _clean(contact.get("external_id"), 320)
        if not source_id:
            continue
        _, was_linked = await _apply_source_contact(
            db,
            source_type="android_contacts",
            source_account=source_account,
            source_id=source_id,
            contact=contact,
            sync_marker=snapshot_id,
        )
        processed += 1
        linked += int(was_linked)
        unlinked += int(not was_linked)

    stale = 0
    if snapshot_complete:
        stale_rows = list(
            (
                await db.execute(
                    select(ContactSourceRecord).where(
                        ContactSourceRecord.source_type == "android_contacts",
                        ContactSourceRecord.source_account == source_account,
                        ContactSourceRecord.active.is_(True),
                        ContactSourceRecord.sync_marker != snapshot_id,
                    )
                )
            ).scalars()
        )
        for row in stale_rows:
            row.active = False
            stale += 1

    await _refresh_aggregates(db)
    await write_audit(
        db,
        "android_contacts_synced",
        entity_type="contact_directory",
        entity_id=source_account,
        details={
            "processed": processed,
            "linked": linked,
            "unlinked": unlinked,
            "stale_deactivated": stale,
            "snapshot_complete": snapshot_complete,
        },
    )
    await db.commit()
    return {
        "processed": processed,
        "linked": linked,
        "unlinked": unlinked,
        "stale_deactivated": stale,
        "snapshot_complete": snapshot_complete,
    }


def _google_group_name_map(service: Any) -> dict[str, str]:
    names: dict[str, str] = {}
    page_token: str | None = None
    while True:
        response = (
            service.contactGroups()
            .list(
                pageSize=1000,
                pageToken=page_token,
                groupFields="name,groupType",
            )
            .execute()
        )
        for group in response.get("contactGroups", []) or []:
            resource_name = _clean(group.get("resourceName"), 255)
            name = _clean(group.get("name"), 160)
            if resource_name and name:
                names[resource_name] = name
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return names


def _google_memberships(person: dict[str, Any], group_names: dict[str, str]) -> tuple[list[str], bool]:
    groups: list[str] = []
    starred = False
    for membership in person.get("memberships", []) or []:
        if not isinstance(membership, dict):
            continue
        raw = membership.get("contactGroupMembership") or {}
        if not isinstance(raw, dict):
            continue
        resource_name = _clean(raw.get("contactGroupResourceName"), 255)
        if not resource_name:
            continue
        name = group_names.get(resource_name, resource_name.removeprefix("contactGroups/"))
        if name and name.casefold() not in {item.casefold() for item in groups}:
            groups.append(name)
        if resource_name == "contactGroups/starred" or name.casefold() == "starred":
            starred = True
    return groups, starred


async def sync_google_contact_sources(db: AsyncSession) -> dict[str, int]:
    connection = await get_google_connection(db)
    source_account = connection.account_key or "google"
    service = await people_service(db)
    group_names = _google_group_name_map(service)
    sync_marker = f"google:{int(_now().timestamp())}"

    processed = 0
    linked = 0
    unlinked = 0
    page_token: str | None = None
    while True:
        response = (
            service.people()
            .connections()
            .list(
                resourceName="people/me",
                pageSize=1000,
                pageToken=page_token,
                personFields=(
                    "names,emailAddresses,phoneNumbers,organizations,memberships,"
                    "relations,nicknames,metadata"
                ),
                sortOrder="LAST_MODIFIED_DESCENDING",
            )
            .execute()
        )
        for person in response.get("connections", []) or []:
            source_id = _clean(person.get("resourceName"), 320)
            if not source_id:
                continue
            names = person.get("names") or []
            emails = [
                _clean(item.get("value"), 500)
                for item in person.get("emailAddresses", []) or []
                if isinstance(item, dict)
            ]
            phones = [
                _clean(item.get("value"), 120)
                for item in person.get("phoneNumbers", []) or []
                if isinstance(item, dict)
            ]
            organizations = [
                item for item in person.get("organizations", []) or [] if isinstance(item, dict)
            ]
            org = organizations[0] if organizations else {}
            nicknames = [
                item for item in person.get("nicknames", []) or [] if isinstance(item, dict)
            ]
            relations = []
            for relation in person.get("relations", []) or []:
                if not isinstance(relation, dict):
                    continue
                relations.append(
                    {
                        "type": _clean(
                            relation.get("type") or relation.get("formattedType"),
                            80,
                        ),
                        "person": _clean(relation.get("person"), 255),
                    }
                )
            groups, starred = _google_memberships(person, group_names)
            contact = {
                "display_name": _clean(names[0].get("displayName"), 255)
                if names and isinstance(names[0], dict)
                else "",
                "emails": [value for value in emails if value],
                "phones": [value for value in phones if value],
                "organization": _clean(org.get("name"), 255),
                "job_title": _clean(org.get("title"), 255),
                "department": _clean(org.get("department"), 255),
                "nickname": _clean(nicknames[0].get("value"), 255)
                if nicknames
                else "",
                "groups": groups,
                "relations": relations,
                "starred": starred,
            }
            _, was_linked = await _apply_source_contact(
                db,
                source_type="google_contacts",
                source_account=source_account,
                source_id=source_id,
                contact=contact,
                sync_marker=sync_marker,
            )
            processed += 1
            linked += int(was_linked)
            unlinked += int(not was_linked)
        page_token = response.get("nextPageToken")
        if not page_token:
            break

    stale_rows = list(
        (
            await db.execute(
                select(ContactSourceRecord).where(
                    ContactSourceRecord.source_type == "google_contacts",
                    ContactSourceRecord.source_account == source_account,
                    ContactSourceRecord.active.is_(True),
                    ContactSourceRecord.sync_marker != sync_marker,
                )
            )
        ).scalars()
    )
    for row in stale_rows:
        row.active = False

    await _refresh_aggregates(db)
    await write_audit(
        db,
        "google_contact_directory_synced",
        entity_type="contact_directory",
        entity_id=source_account[:120],
        details={
            "processed": processed,
            "linked": linked,
            "unlinked": unlinked,
            "stale_deactivated": len(stale_rows),
            "group_count": len(group_names),
        },
    )
    await db.commit()
    return {
        "processed": processed,
        "linked": linked,
        "unlinked": unlinked,
        "stale_deactivated": len(stale_rows),
        "groups": len(group_names),
    }


def _preference_category(fact: RelationshipFact | None) -> tuple[bool, str]:
    if fact is None:
        return False, "other"
    try:
        value = json.loads(fact.value_json or "{}")
    except json.JSONDecodeError:
        value = {}
    category = _clean(value.get("relationship_category"), 40).lower()
    return True, category if category else "other"


async def list_people_directory(
    db: AsyncSession,
    *,
    query: str = "",
    filter_name: str = "all",
    limit: int = 1000,
) -> dict[str, Any]:
    profiles = list(
        (
            await db.execute(
                select(RelationshipProfile).order_by(
                    RelationshipProfile.display_name.asc(),
                    RelationshipProfile.id.asc(),
                )
            )
        ).scalars()
    )
    sources = list(
        (
            await db.execute(
                select(ContactSourceRecord)
                .where(ContactSourceRecord.active.is_(True))
                .order_by(ContactSourceRecord.display_name.asc(), ContactSourceRecord.id.asc())
            )
        ).scalars()
    )
    preference_facts = list(
        (
            await db.execute(
                select(RelationshipFact).where(
                    RelationshipFact.fact_key == COMMUNICATION_PREFERENCES_FACT_KEY,
                    RelationshipFact.source_type == "user_explicit",
                )
            )
        ).scalars()
    )
    prefs_by_relationship = {row.relationship_id: row for row in preference_facts}

    identities = list((await db.execute(select(RelationshipIdentity))).scalars())
    identities_by_relationship: dict[int, list[RelationshipIdentity]] = {}
    for identity in identities:
        identities_by_relationship.setdefault(identity.relationship_id, []).append(identity)

    sources_by_relationship: dict[int, list[ContactSourceRecord]] = {}
    for source in sources:
        if source.relationship_id is not None:
            sources_by_relationship.setdefault(source.relationship_id, []).append(source)

    people: list[dict[str, Any]] = []
    seen_source_ids: set[int] = set()
    for profile in profiles:
        linked_sources = sources_by_relationship.get(profile.id, [])
        seen_source_ids.update(row.id for row in linked_sources)
        # Prefer the local phone-book label for presentation, then Google, then
        # the canonical relationship profile inferred from communications.
        phone_source = next(
            (row for row in linked_sources if row.source_type == "android_contacts" and row.display_name),
            None,
        )
        google_source = next(
            (row for row in linked_sources if row.source_type == "google_contacts" and row.display_name),
            None,
        )
        presentation = phone_source or google_source
        display_name = presentation.display_name if presentation else profile.display_name
        organization = (
            next((row.organization for row in linked_sources if row.organization), "")
            or profile.organization
        )
        job_title = next((row.job_title for row in linked_sources if row.job_title), "")
        department = next((row.department for row in linked_sources if row.department), "")
        nickname = next((row.nickname for row in linked_sources if row.nickname), "")
        groups: list[str] = []
        relations: list[dict[str, str]] = []
        favorite = False
        for row in linked_sources:
            favorite = favorite or row.starred
            for group in _json_list(row.groups_json):
                value = _clean(group, 160)
                if value and value.casefold() not in {item.casefold() for item in groups}:
                    groups.append(value)
            relations.extend(_safe_relations(_json_list(row.relations_json)))

        rel_identities = identities_by_relationship.get(profile.id, [])
        emails = [
            row.display_value or row.normalized_value
            for row in rel_identities
            if row.identity_type == "email"
        ]
        phones = [
            row.display_value or row.normalized_value
            for row in rel_identities
            if row.identity_type == "phone"
        ]
        sources_seen = {row.source_type for row in linked_sources}
        sources_seen.update(row.source for row in rel_identities if row.source)
        channels = {
            "email" if row.identity_type == "email" else "phone"
            for row in rel_identities
        }
        if profile.preferred_channel:
            channels.add(profile.preferred_channel)
        configured, category = _preference_category(prefs_by_relationship.get(profile.id))
        suggested_category, suggestion_evidence = category_hint(groups, relations)
        people.append(
            {
                "relationship_id": profile.id,
                "display_name": display_name,
                "organization": organization,
                "job_title": job_title,
                "department": department,
                "nickname": nickname,
                "primary_email": profile.primary_email or (emails[0] if emails else ""),
                "primary_phone": profile.primary_phone or (phones[0] if phones else ""),
                "emails": emails,
                "phones": phones,
                "groups": groups,
                "relations": relations,
                "source_types": sorted(sources_seen),
                "channels": sorted(channels),
                "favorite": favorite,
                "configured": configured,
                "relationship_category": category,
                "suggested_category": suggested_category,
                "suggestion_evidence": suggestion_evidence,
                "interaction_count": profile.interaction_count,
                "last_interaction_at": (
                    profile.last_interaction_at.isoformat()
                    if profile.last_interaction_at
                    else None
                ),
                "configurable": bool(rel_identities),
            }
        )

    # Keep name-only phone/Google contacts discoverable. They cannot safely bind
    # personalized messaging until a stable email/phone identity is available.
    for source in sources:
        if source.id in seen_source_ids:
            continue
        groups = [_clean(value, 160) for value in _json_list(source.groups_json) if _clean(value, 160)]
        relations = _safe_relations(_json_list(source.relations_json))
        suggested_category, evidence = category_hint(groups, relations)
        emails = _unique_strings(_json_list(source.emails_json), limit=50, item_limit=500)
        phones = _unique_strings(_json_list(source.phones_json), limit=50, item_limit=120)
        people.append(
            {
                "relationship_id": None,
                "display_name": source.display_name,
                "organization": source.organization,
                "job_title": source.job_title,
                "department": source.department,
                "nickname": source.nickname,
                "primary_email": emails[0] if emails else "",
                "primary_phone": phones[0] if phones else "",
                "emails": emails,
                "phones": phones,
                "groups": groups,
                "relations": relations,
                "source_types": [source.source_type],
                "channels": [],
                "favorite": source.starred,
                "configured": False,
                "relationship_category": "other",
                "suggested_category": suggested_category,
                "suggestion_evidence": evidence,
                "interaction_count": 0,
                "last_interaction_at": None,
                "configurable": False,
            }
        )

    clean_query = query.strip().casefold()
    if clean_query:
        people = [
            person
            for person in people
            if clean_query
            in " ".join(
                [
                    str(person.get("display_name") or ""),
                    str(person.get("organization") or ""),
                    str(person.get("job_title") or ""),
                    str(person.get("department") or ""),
                    str(person.get("nickname") or ""),
                    " ".join(person.get("emails") or []),
                    " ".join(person.get("phones") or []),
                    " ".join(person.get("groups") or []),
                    " ".join(
                        f"{item.get('type', '')} {item.get('person', '')}"
                        for item in person.get("relations") or []
                    ),
                ]
            ).casefold()
        ]

    normalized_filter = filter_name.strip().lower()
    if normalized_filter == "configured":
        people = [person for person in people if person["configured"]]
    elif normalized_filter == "unconfigured":
        people = [person for person in people if not person["configured"]]
    elif normalized_filter == "favorites":
        people = [person for person in people if person["favorite"]]

    people.sort(
        key=lambda item: (
            str(item.get("display_name") or item.get("primary_email") or item.get("primary_phone") or "").casefold(),
            str(item.get("primary_email") or ""),
            str(item.get("primary_phone") or ""),
        )
    )
    people = people[: max(1, min(limit, 2000))]
    return {
        "people": people,
        "total": len(people),
        "configured": sum(1 for person in people if person["configured"]),
        "favorites": sum(1 for person in people if person["favorite"]),
        "phone_book": sum(
            1 for person in people if "android_contacts" in (person.get("source_types") or [])
        ),
        "google_contacts": sum(
            1 for person in people if "google_contacts" in (person.get("source_types") or [])
        ),
    }
