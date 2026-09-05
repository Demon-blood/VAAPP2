from __future__ import annotations

import asyncio
import hashlib
import io
import json
import re
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

import fitz
from bs4 import BeautifulSoup
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_text, encrypt_text
from app.models.entities import (
    BrowserOperation,
    BrowserPortal,
    DocumentIntelligence,
    DocumentObligation,
    DocumentRecord,
    EmailMessage,
    FormSubmission,
    OAuthConnection,
    UserProfileFact,
    VAObjective,
)
from app.services.audit import write_audit
from app.services.browser_operator import (
    operation_requires_material_decision,
    portal_allowed_hosts,
    prepare_browser_operation,
)

MAX_DOCUMENT_BYTES = 12 * 1024 * 1024
MAX_EXTRACTED_TEXT = 250_000

_DATE_PATTERNS = [
    re.compile(r"\b(20\d{2})[-/](0?[1-9]|1[0-2])[-/](0?[1-9]|[12]\d|3[01])\b"),
    re.compile(r"\b(0?[1-9]|[12]\d|3[01])[./-](0?[1-9]|1[0-2])[./-](20\d{2})\b"),
]
_MONTHS = {
    "january": 1, "jan": 1, "januari": 1,
    "february": 2, "feb": 2, "februari": 2,
    "march": 3, "mar": 3, "maart": 3,
    "april": 4, "apr": 4,
    "may": 5, "mei": 5,
    "june": 6, "jun": 6, "juni": 6,
    "july": 7, "jul": 7, "juli": 7,
    "august": 8, "aug": 8, "augustus": 8,
    "september": 9, "sep": 9,
    "october": 10, "oct": 10, "oktober": 10, "okt": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}
_MONTH_PATTERN = re.compile(
    r"\b(0?[1-9]|[12]\d|3[01])\s+([A-Za-zÀ-ÿ]+)\s+(20\d{2})\b",
    re.IGNORECASE,
)
_DEADLINE_SIGNALS = re.compile(
    r"\b(due|deadline|submit(?:ted)? by|respond by|return by|no later than|before|expires? on|renew(?:al)? by|"
    r"uiterlijk|ten laatste|deadline|indienen(?: vóór| voor)?|reageren(?: vóór| voor)?|terugsturen(?: vóór| voor)?|"
    r"vervaldatum|verloopt op|vernieuwen(?: vóór| voor)?)\b",
    re.IGNORECASE,
)
_FORM_SIGNALS = re.compile(
    r"\b(form|formul(?:ier|aire)|application|aanvraag|complete the form|fill (?:in|out)|invullen|"
    r"submit (?:the )?(?:form|application|documents?)|indienen|upload (?:the )?documents?|documenten uploaden|"
    r"return signed|onderteken(?:en|d)?)\b",
    re.IGNORECASE,
)
_MATERIAL_SIGNALS = re.compile(
    r"\b(sign|signature|onderteken|declaration|verklaring|contract|agreement|overeenkomst|"
    r"payment|pay|betaling|bank account|iban|tax return|belastingaangifte|medical consent|toestemming)\b",
    re.IGNORECASE,
)
_PROTECTED_SIGNALS = re.compile(
    r"\b(legal|lawyer|court|gerecht|deurwaarder|government|overheid|belasting|tax|medical|medisch|"
    r"health|gezondheid|contract|security|identity|identiteit|passport|paspoort|national id|rijksregisternummer)\b",
    re.IGNORECASE,
)
_URL_RE = re.compile(r"https://[^\s<>\]\[\)\(\"']+", re.IGNORECASE)
_REFERENCE_RE = re.compile(
    r"\b(?:reference|referentie|dossier(?:nummer)?|case(?: number)?|customer(?: number)?|klantnummer)\s*[:#-]?\s*([A-Z0-9][A-Z0-9._/-]{3,40})",
    re.IGNORECASE,
)

_FIELD_ALIASES: dict[str, list[str]] = {
    "full_name": ["Full name", "Name", "Your name", "Naam", "Volledige naam"],
    "email": ["Email", "E-mail", "Email address", "E-mailadres"],
    "phone": ["Phone", "Telephone", "Mobile", "Telefoon", "Telefoonnummer", "Gsm"],
    "address": ["Address", "Street address", "Adres", "Straat en huisnummer"],
    "postal_code": ["Postal code", "ZIP", "Postcode"],
    "city": ["City", "Town", "Gemeente", "Stad"],
    "country": ["Country", "Land"],
    "date_of_birth": ["Date of birth", "Birth date", "Geboortedatum"],
    "reference": ["Reference", "Reference number", "Referentie", "Dossiernummer", "Klantnummer"],
}


def utcnow() -> datetime:
    return datetime.utcnow()


def _loads(value: str, default: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return default


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def _extract_exact_dates(text: str) -> list[datetime]:
    found: set[datetime] = set()
    for match in _DATE_PATTERNS[0].finditer(text):
        try:
            found.add(datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)), 23, 59, 59))
        except ValueError:
            pass
    for match in _DATE_PATTERNS[1].finditer(text):
        try:
            found.add(datetime(int(match.group(3)), int(match.group(2)), int(match.group(1)), 23, 59, 59))
        except ValueError:
            pass
    for match in _MONTH_PATTERN.finditer(text):
        month = _MONTHS.get(match.group(2).casefold())
        if not month:
            continue
        try:
            found.add(datetime(int(match.group(3)), month, int(match.group(1)), 23, 59, 59))
        except ValueError:
            pass
    return sorted(found)


def _deadline_candidates(text: str) -> list[datetime]:
    candidates: set[datetime] = set()
    positioned: list[tuple[datetime, int]] = []
    for match in _DATE_PATTERNS[0].finditer(text):
        try:
            positioned.append((datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)), 23, 59, 59), match.start()))
        except ValueError:
            pass
    for match in _DATE_PATTERNS[1].finditer(text):
        try:
            positioned.append((datetime(int(match.group(3)), int(match.group(2)), int(match.group(1)), 23, 59, 59), match.start()))
        except ValueError:
            pass
    for match in _MONTH_PATTERN.finditer(text):
        month = _MONTHS.get(match.group(2).casefold())
        if not month:
            continue
        try:
            positioned.append((datetime(int(match.group(3)), month, int(match.group(1)), 23, 59, 59), match.start()))
        except ValueError:
            pass
    for date, pos in positioned:
        context = text[max(0, pos - 120): pos + 100]
        if _DEADLINE_SIGNALS.search(context):
            candidates.add(date)
    return sorted(candidates)


def _extract_urls(text: str) -> list[str]:
    seen: set[str] = set()
    rows: list[str] = []
    for match in _URL_RE.finditer(text):
        url = match.group(0).rstrip(".,;:")
        host = _host(url)
        if not host or host.endswith("googleusercontent.com"):
            continue
        if url not in seen:
            seen.add(url)
            rows.append(url)
    return rows[:25]


def _reference_from_text(text: str) -> str:
    match = _REFERENCE_RE.search(text)
    return (match.group(1) if match else "")[:80]


def _looks_protected(record: DocumentRecord, text: str) -> bool:
    probe = f"{record.category} {record.name} {text[:12000]}"
    return bool(_PROTECTED_SIGNALS.search(probe))


def _looks_material(text: str) -> bool:
    return bool(_MATERIAL_SIGNALS.search(text[:50000]))


def _looks_like_form(text: str) -> bool:
    return bool(_FORM_SIGNALS.search(text[:80000]))


def _priority_for_deadline(deadline: datetime | None) -> str:
    if deadline is None:
        return "normal"
    hours = (deadline - utcnow()).total_seconds() / 3600
    if hours <= 72:
        return "urgent"
    if hours <= 7 * 24:
        return "high"
    return "normal"


async def _download_drive_bytes(db: AsyncSession, record: DocumentRecord) -> bytes:
    from googleapiclient.http import MediaIoBaseDownload

    from app.integrations.google_api import drive_service

    if record.size_bytes and record.size_bytes > MAX_DOCUMENT_BYTES:
        raise ValueError("Document exceeds the 12 MB document-intelligence limit")
    service = await drive_service(db)
    request = service.files().get_media(fileId=record.drive_file_id)
    target = io.BytesIO()
    downloader = MediaIoBaseDownload(target, request, chunksize=1024 * 1024)
    done = False
    while not done:
        _, done = await asyncio.to_thread(downloader.next_chunk)
        if target.tell() > MAX_DOCUMENT_BYTES:
            raise ValueError("Document exceeds the 12 MB document-intelligence limit")
    return target.getvalue()


def _extract_text_from_bytes(content: bytes, mime_type: str, name: str) -> str:
    mime = (mime_type or "").casefold()
    lower_name = name.casefold()
    if mime == "application/pdf" or lower_name.endswith(".pdf"):
        with fitz.open(stream=content, filetype="pdf") as doc:
            return "\n".join(page.get_text("text") for page in doc)[:MAX_EXTRACTED_TEXT]
    if "html" in mime or lower_name.endswith((".html", ".htm")):
        decoded = content.decode("utf-8", errors="replace")
        return BeautifulSoup(decoded, "html.parser").get_text("\n", strip=True)[:MAX_EXTRACTED_TEXT]
    if mime.startswith("text/") or lower_name.endswith((".txt", ".csv", ".md", ".json", ".xml")):
        return content.decode("utf-8", errors="replace")[:MAX_EXTRACTED_TEXT]
    return ""


async def _source_email_text(db: AsyncSession, record: DocumentRecord) -> tuple[str, EmailMessage | None]:
    from app.integrations.google_api import extract_gmail_body, get_gmail_message

    if record.source_type != "email" or not record.source_id:
        return "", None
    email = (
        await db.execute(select(EmailMessage).where(EmailMessage.provider_message_id == record.source_id).limit(1))
    ).scalar_one_or_none()
    try:
        raw = await get_gmail_message(db, record.source_id, format="full")
        plain, html = extract_gmail_body(raw.get("payload") or {})
        body = plain or BeautifulSoup(html, "html.parser").get_text("\n", strip=True)
        return body[:MAX_EXTRACTED_TEXT], email
    except Exception:
        return "", email


async def _upsert_profile_fact(
    db: AsyncSession,
    *,
    key: str,
    value: str,
    source: str,
    verified: bool = True,
) -> UserProfileFact | None:
    key = key.strip().lower().replace(" ", "_")[:120]
    value = value.strip()
    if not key or not value:
        return None
    row = (
        await db.execute(select(UserProfileFact).where(UserProfileFact.fact_key == key).limit(1))
    ).scalar_one_or_none()
    if row is None:
        row = UserProfileFact(fact_key=key)
        db.add(row)
    # Explicit values win over inferred/source-synced facts.
    if row.source == "explicit" and source != "explicit":
        return row
    row.value_encrypted = encrypt_text(value)
    row.value_sha256 = _sha(value)
    row.source = source[:80]
    row.verified = bool(verified)
    row.updated_at = utcnow()
    await db.flush()
    return row


async def seed_profile_facts_from_google(db: AsyncSession) -> int:
    connection = (
        await db.execute(
            select(OAuthConnection).where(OAuthConnection.provider == "google", OAuthConnection.enabled.is_(True)).limit(1)
        )
    ).scalar_one_or_none()
    if connection is None:
        return 0
    metadata = _loads(connection.metadata_json, {})
    count = 0
    name = str(metadata.get("name") or "").strip() if isinstance(metadata, dict) else ""
    if await _upsert_profile_fact(db, key="full_name", value=name, source="google_oauth"):
        count += 1
    if await _upsert_profile_fact(db, key="email", value=connection.account_key, source="google_oauth"):
        count += 1
    await db.commit()
    return count


async def set_user_profile_fact(db: AsyncSession, *, key: str, value: str) -> dict[str, Any]:
    row = await _upsert_profile_fact(db, key=key, value=value, source="explicit", verified=True)
    if row is None:
        raise ValueError("Profile fact key and value are required")
    await write_audit(
        db,
        "document_profile_fact_updated",
        entity_type="user_profile_fact",
        entity_id=str(row.id),
        details={"fact_key": row.fact_key, "source": row.source},
    )
    await db.commit()
    return {"key": row.fact_key, "value": value, "source": row.source, "verified": row.verified}


async def list_user_profile_facts(db: AsyncSession) -> list[dict[str, Any]]:
    await seed_profile_facts_from_google(db)
    rows = list((await db.execute(select(UserProfileFact).order_by(UserProfileFact.fact_key))).scalars())
    result: list[dict[str, Any]] = []
    for row in rows:
        try:
            value = decrypt_text(row.value_encrypted) if row.value_encrypted else ""
        except RuntimeError:
            value = ""
        result.append({
            "key": row.fact_key,
            "value": value,
            "source": row.source,
            "verified": row.verified,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        })
    return result


async def _matching_portal(db: AsyncSession, url: str) -> BrowserPortal | None:
    host = _host(url)
    if not host:
        return None
    portals = list((await db.execute(select(BrowserPortal).where(BrowserPortal.enabled.is_(True)))).scalars())
    for portal in portals:
        if any(host == allowed or host.endswith("." + allowed) for allowed in portal_allowed_hosts(portal)):
            return portal
    return None


async def _profile_field_payload(db: AsyncSession, reference: str = "") -> list[dict[str, Any]]:
    facts = await list_user_profile_facts(db)
    values = {str(row["key"]): str(row["value"]) for row in facts if row.get("verified") and str(row.get("value") or "")}
    if reference:
        values["reference"] = reference
    fields: list[dict[str, Any]] = []
    for key, aliases in _FIELD_ALIASES.items():
        value = values.get(key, "")
        if value:
            fields.append({"key": key, "aliases": aliases, "value": value})
    return fields




async def _record_blocked_obligation_event(db: AsyncSession, obligation: DocumentObligation, reason: str) -> None:
    from app.services.autonomous_core import record_event

    event, _ = await record_event(
        db,
        event_key=f"document-obligation:{obligation.correlation_key}",
        source_type="document_obligation",
        source_id=str(obligation.id),
        event_type="document_obligation_blocked",
        title=f"Resolve {obligation.title}",
        payload={
            "document_obligation_id": obligation.id,
            "goal": f"Resolve {obligation.title} before the source deadline",
            "due_at": obligation.due_at.isoformat() if obligation.due_at else None,
            "priority": obligation.priority,
            "protected": obligation.protected,
            "reason": reason,
        },
    )
    await write_audit(
        db,
        "document_obligation_owned_blocked",
        entity_type="document_obligation",
        entity_id=str(obligation.id),
        details={"event_id": event.id, "reason": reason},
    )

async def _prepare_form_submission(db: AsyncSession, obligation: DocumentObligation, portal: BrowserPortal) -> FormSubmission | None:
    if not obligation.form_url:
        return None
    reference = str((_loads(obligation.details_json, {}) or {}).get("reference") or "")
    fields = await _profile_field_payload(db, reference=reference)
    field_fingerprint = _sha(_dump([(field["key"], _sha(field["value"])) for field in fields]))[:20]
    idempotency_key = f"document-form:{obligation.id}:{field_fingerprint}"
    existing = (
        await db.execute(select(FormSubmission).where(FormSubmission.idempotency_key == idempotency_key).limit(1))
    ).scalar_one_or_none()
    if existing is not None:
        obligation.form_submission_id = existing.id
        obligation.browser_operation_id = existing.browser_operation_id
        return existing

    verification = {
        "text_any_contains": [
            "thank you", "submission received", "form submitted", "application received",
            "bedankt", "formulier ontvangen", "aanvraag ontvangen", "uw aanvraag is ontvangen",
        ]
    }
    steps = [
        {"kind": "goto", "url": obligation.form_url, "side_effect": False, "replay_safe": True},
        {
            "kind": "autofill_form",
            "fields": fields,
            "side_effect": False,
            "replay_safe": True,
        },
        {
            "kind": "click_action",
            "labels": ["Submit", "Send", "Indienen", "Verzenden", "Continue", "Doorgaan"],
            "side_effect": True,
            "replay_safe": False,
            "material_commitment": bool(obligation.material_commitment),
        },
    ]
    operation = await prepare_browser_operation(
        db,
        idempotency_key=idempotency_key,
        portal_id=portal.id,
        title=f"Submit form for {obligation.title}"[:500],
        steps=steps,
        verification=verification,
    )
    submission = FormSubmission(
        idempotency_key=idempotency_key,
        obligation_id=obligation.id,
        portal_id=portal.id,
        browser_operation_id=operation.id,
        field_keys_json=_dump([field["key"] for field in fields]),
        fields_encrypted=encrypt_text(_dump(fields)),
        status="prepared",
        requires_material_approval=operation_requires_material_decision(operation),
    )
    db.add(submission)
    await db.flush()
    obligation.form_submission_id = submission.id
    obligation.browser_operation_id = operation.id
    obligation.portal_id = portal.id
    obligation.status = "in_progress"

    from app.services.autonomous_core import record_event

    event, _ = await record_event(
        db,
        event_key=f"document-form:{idempotency_key}",
        source_type="document_form",
        source_id=str(obligation.id),
        event_type="browser_portal_operation_planned",
        title=f"Submit {obligation.title}",
        payload={
            "browser_operation_id": operation.id,
            "portal_id": portal.id,
            "goal": f"Complete and verify the requested form for {obligation.title}",
            "priority": obligation.priority,
            "risk_level": "high" if obligation.protected else "low",
            "material_commitment": bool(obligation.material_commitment),
            "document_obligation_id": obligation.id,
        },
    )
    await write_audit(
        db,
        "document_form_prepared",
        entity_type="document_obligation",
        entity_id=str(obligation.id),
        details={
            "portal_id": portal.id,
            "browser_operation_id": operation.id,
            "event_id": event.id,
            "field_keys": [field["key"] for field in fields],
            "material_commitment": bool(obligation.material_commitment),
        },
    )
    await db.commit()
    return submission


async def analyze_document_record(db: AsyncSession, record: DocumentRecord) -> dict[str, Any]:
    existing = (
        await db.execute(select(DocumentIntelligence).where(DocumentIntelligence.document_id == record.id).limit(1))
    ).scalar_one_or_none()
    if existing is not None and existing.source_checksum_sha256 == record.checksum_sha256 and existing.status == "analyzed":
        return {"document_id": record.id, "status": "unchanged"}

    if existing is None:
        existing = DocumentIntelligence(document_id=record.id)
        db.add(existing)
    existing.source_checksum_sha256 = record.checksum_sha256
    existing.last_error = ""
    existing.status = "extracting"
    await db.flush()

    try:
        content = await _download_drive_bytes(db, record)
        file_text = _extract_text_from_bytes(content, record.mime_type, record.name)
        email_text, email = await _source_email_text(db, record)
        text = (file_text + "\n" + email_text).strip()[:MAX_EXTRACTED_TEXT]
        deadlines = _deadline_candidates(text)
        form_urls = _extract_urls(text) if _looks_like_form(text) else []
        protected = _looks_protected(record, text)
        material = _looks_material(text)
        reference = _reference_from_text(text)

        existing.text_sha256 = _sha(text)
        existing.extracted_text_encrypted = encrypt_text(text) if text else ""
        existing.document_type = "form" if form_urls else ("deadline_notice" if deadlines else "record")
        existing.deadline_candidates_json = _dump([item.date().isoformat() for item in deadlines])
        existing.form_urls_json = _dump(form_urls)
        existing.protected = protected
        existing.status = "analyzed"
        existing.analyzed_at = utcnow()

        created_obligations = 0
        due = next((item for item in deadlines if item >= utcnow() - timedelta(days=1)), deadlines[0] if deadlines else None)
        if form_urls or due:
            form_url = form_urls[0] if form_urls else ""
            key_material = f"{record.id}|{form_url}|{due.date().isoformat() if due else ''}"
            correlation_key = _sha(key_material)
            obligation = (
                await db.execute(
                    select(DocumentObligation).where(DocumentObligation.correlation_key == correlation_key).limit(1)
                )
            ).scalar_one_or_none()
            if obligation is None:
                obligation = DocumentObligation(
                    correlation_key=correlation_key,
                    document_id=record.id,
                    source_message_id=record.source_id if record.source_type == "email" else "",
                    title=record.name[:1000],
                    issuer=(email.sender[:500] if email is not None else ""),
                    obligation_type="form" if form_url else "deadline",
                    due_at=due,
                    form_url=form_url,
                    priority=_priority_for_deadline(due),
                    protected=protected,
                    material_commitment=material,
                    details_json=_dump({"reference": reference, "category": record.category}),
                )
                db.add(obligation)
                await db.flush()
                created_obligations += 1
            else:
                obligation.due_at = due
                obligation.priority = _priority_for_deadline(due)
                obligation.protected = protected
                obligation.material_commitment = material
                obligation.form_url = form_url
                obligation.details_json = _dump({"reference": reference, "category": record.category})

            if form_url and obligation.status not in {"completed", "cancelled"}:
                portal = await _matching_portal(db, form_url)
                if portal is None:
                    obligation.status = "blocked_capability"
                    obligation.last_error = "No secure portal profile matches the form host"
                    await _record_blocked_obligation_event(db, obligation, obligation.last_error)
                else:
                    await _prepare_form_submission(db, obligation, portal)
            elif due and obligation.status not in {"completed", "cancelled"}:
                obligation.status = "blocked_capability"
                obligation.last_error = "A source deadline was detected, but no safe executable form/portal action was identified"
                await _record_blocked_obligation_event(db, obligation, obligation.last_error)

        await write_audit(
            db,
            "document_intelligence_analyzed",
            entity_type="document",
            entity_id=str(record.id),
            details={
                "document_type": existing.document_type,
                "deadline_count": len(deadlines),
                "form_url_count": len(form_urls),
                "protected": protected,
                "created_obligations": created_obligations,
            },
        )
        await db.commit()
        return {
            "document_id": record.id,
            "status": existing.status,
            "document_type": existing.document_type,
            "deadlines": len(deadlines),
            "forms": len(form_urls),
            "obligations_created": created_obligations,
        }
    except Exception as exc:
        existing.status = "failed"
        existing.last_error = str(exc)[:4000]
        existing.analyzed_at = utcnow()
        await db.commit()
        return {"document_id": record.id, "status": "failed", "error": existing.last_error}


async def _sync_obligation_statuses(db: AsyncSession) -> dict[str, int]:
    rows = list(
        (
            await db.execute(
                select(DocumentObligation).where(DocumentObligation.status.not_in(["completed", "cancelled"]))
            )
        ).scalars()
    )
    result = {"completed": 0, "needs_user": 0, "blocked": 0, "overdue": 0, "in_progress": 0}
    now = utcnow()
    for row in rows:
        if not row.objective_id:
            objective = (
                await db.execute(
                    select(VAObjective).where(
                        VAObjective.source_type == "document_obligation",
                        VAObjective.source_id == str(row.id),
                    ).order_by(VAObjective.id.desc()).limit(1)
                )
            ).scalar_one_or_none()
            if objective is not None:
                row.objective_id = objective.id
        if row.browser_operation_id:
            operation = await db.get(BrowserOperation, row.browser_operation_id)
            if operation is not None:
                if operation.objective_id and not row.objective_id:
                    row.objective_id = operation.objective_id
                submission = await db.get(FormSubmission, row.form_submission_id) if row.form_submission_id else None
                if operation.status == "verified":
                    row.status = "completed"
                    row.completed_at = operation.verified_at or now
                    row.last_error = ""
                    if submission is not None:
                        submission.status = "verified"
                        submission.verified_at = operation.verified_at or now
                    result["completed"] += 1
                    continue
                if operation.status == "needs_user_auth":
                    if operation.challenge_type == "form_input" and row.form_url and row.portal_id:
                        portal = await db.get(BrowserPortal, row.portal_id)
                        if portal is not None and portal.enabled:
                            previous_operation_id = row.browser_operation_id
                            await _prepare_form_submission(db, row, portal)
                            if row.browser_operation_id != previous_operation_id:
                                row.status = "in_progress"
                                row.last_error = ""
                                result["in_progress"] += 1
                                continue
                    row.status = "needs_user_data" if operation.challenge_type == "form_input" else "needs_user_auth"
                    row.last_error = operation.challenge_prompt or operation.last_error
                    if submission is not None:
                        submission.status = row.status
                        submission.last_error = row.last_error
                    result["needs_user"] += 1
                    continue
                if operation.status == "creation_uncertain":
                    from app.services.browser_operator import operation_requires_postcondition_reconciliation

                    row.last_error = operation.last_error
                    if operation_requires_postcondition_reconciliation(operation):
                        # The same BrowserOperation is still owned by the Autonomous Core and
                        # may only perform provider-postcondition reconciliation. Do not project
                        # active duplicate-safe recovery as a terminal document failure.
                        row.status = "in_progress"
                        if submission is not None:
                            submission.status = "in_progress"
                            submission.last_error = row.last_error
                        result["in_progress"] += 1
                    else:
                        # Without the durable marker, the browser runtime cannot prove that
                        # reconciliation-only resume is safe. Keep the obligation system-blocked.
                        row.status = "blocked_system"
                        if submission is not None:
                            submission.status = row.status
                            submission.last_error = row.last_error
                        result["blocked"] += 1
                    continue
                if operation.status == "failed":
                    row.status = "blocked_system"
                    row.last_error = operation.last_error
                    if submission is not None:
                        submission.status = row.status
                        submission.last_error = row.last_error
                    result["blocked"] += 1
                    continue
                if operation.status == "blocked_capability":
                    row.status = "blocked_capability"
                    row.last_error = operation.last_error
                    result["blocked"] += 1
                    continue
                row.status = "in_progress"
                result["in_progress"] += 1
                continue

        if row.form_url and row.status in {"detected", "blocked_capability", "needs_user_data"}:
            portal = await _matching_portal(db, row.form_url)
            if portal is not None:
                await _prepare_form_submission(db, row, portal)
                result["in_progress"] += 1
                continue
        if row.due_at and row.due_at < now:
            row.status = "overdue"
            result["overdue"] += 1
    await db.commit()
    return result


async def reconcile_document_ownership(db: AsyncSession, *, limit: int = 50) -> dict[str, Any]:
    await seed_profile_facts_from_google(db)
    records = list(
        (
            await db.execute(select(DocumentRecord).order_by(DocumentRecord.created_at.desc()).limit(max(1, min(limit, 250))))
        ).scalars()
    )
    analyzed = 0
    failed = 0
    for record in records:
        outcome = await analyze_document_record(db, record)
        if outcome.get("status") == "failed":
            failed += 1
        elif outcome.get("status") != "unchanged":
            analyzed += 1
    states = await _sync_obligation_statuses(db)
    return {"documents_scanned": len(records), "documents_analyzed": analyzed, "failed": failed, **states}


async def document_ownership_status(db: AsyncSession) -> dict[str, Any]:
    await _sync_obligation_statuses(db)
    counts = dict(
        (
            await db.execute(
                select(DocumentObligation.status, func.count(DocumentObligation.id)).group_by(DocumentObligation.status)
            )
        ).all()
    )
    next_due = (
        await db.execute(
            select(func.min(DocumentObligation.due_at)).where(
                DocumentObligation.status.not_in(["completed", "cancelled"]),
                DocumentObligation.due_at.is_not(None),
            )
        )
    ).scalar_one_or_none()
    return {
        "total_obligations": sum(int(value) for value in counts.values()),
        "status_counts": {str(key): int(value) for key, value in counts.items()},
        "next_due_at": next_due.isoformat() if next_due else None,
        "profile_fact_count": int((await db.execute(select(func.count(UserProfileFact.id)))).scalar_one()),
    }


async def list_document_obligations(db: AsyncSession, *, limit: int = 250) -> list[dict[str, Any]]:
    await _sync_obligation_statuses(db)
    rows = list(
        (
            await db.execute(
                select(DocumentObligation)
                .order_by(DocumentObligation.due_at.asc().nullslast(), DocumentObligation.id.desc())
                .limit(max(1, min(limit, 1000)))
            )
        ).scalars()
    )
    result: list[dict[str, Any]] = []
    for row in rows:
        result.append({
            "id": row.id,
            "document_id": row.document_id,
            "title": row.title,
            "issuer": row.issuer,
            "obligation_type": row.obligation_type,
            "due_at": row.due_at.isoformat() if row.due_at else None,
            "form_url": row.form_url,
            "status": row.status,
            "priority": row.priority,
            "protected": row.protected,
            "material_commitment": row.material_commitment,
            "portal_id": row.portal_id,
            "objective_id": row.objective_id,
            "browser_operation_id": row.browser_operation_id,
            "last_error": row.last_error,
            "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        })
    return result


async def document_obligation_detail(db: AsyncSession, obligation_id: int) -> dict[str, Any]:
    row = await db.get(DocumentObligation, obligation_id)
    if row is None:
        raise LookupError("Document obligation not found")
    document = await db.get(DocumentRecord, row.document_id) if row.document_id else None
    objective = await db.get(VAObjective, row.objective_id) if row.objective_id else None
    submission = await db.get(FormSubmission, row.form_submission_id) if row.form_submission_id else None
    payload = (await list_document_obligations(db, limit=1000))
    base = next(item for item in payload if int(item["id"]) == row.id)
    base.update({
        "document": {
            "id": document.id,
            "name": document.name,
            "category": document.category,
            "drive_web_url": document.drive_web_url,
        } if document else None,
        "objective_status": objective.status if objective else None,
        "form_submission": {
            "id": submission.id,
            "status": submission.status,
            "field_keys": _loads(submission.field_keys_json, []),
            "requires_material_approval": submission.requires_material_approval,
            "verified_at": submission.verified_at.isoformat() if submission and submission.verified_at else None,
            "last_error": submission.last_error if submission else "",
        } if submission else None,
    })
    return base
