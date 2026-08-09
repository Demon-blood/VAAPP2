from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from email.utils import parseaddr
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import MessageFingerprint, SenderRule
from app.schemas.api import AutomationDecision

_DYNAMIC_TERMS = {
    "invoice", "factuur", "betaling", "payment", "amount due", "openstaand", "vervaldatum",
    "due date", "overdue", "achterstallig", "reminder", "herinnering", "appointment", "afspraak",
    "meeting", "vergadering", "reservation", "reservering", "delivery", "levering", "tracking",
    "order", "bestelling", "subscription", "abonnement", "renewal", "verlenging", "cancel",
    "opzeg", "reply", "antwoord", "respond", "beantwoord", "deadline", "before ", "vóór ",
    "support", "complaint", "klacht", "refund", "terugbetaling", "security", "beveiliging",
    "password", "wachtwoord", "verification", "verificatie", "court", "rechtbank", "advocaat",
    "lawyer", "deurwaarder", "bailiff", "medical", "medisch", "doctor", "arts",
}

_PROTECTED_GROUPS: list[tuple[str, list[str], str]] = [
    ("Legal & Government", ["advocaat", "lawyer", "rechtbank", "court", "deurwaarder", "bailiff", "overheid", "government", "gemeente", "fiscus", "belasting", "official document", "officieel document"], "Mail/01 Juridisch & overheid"),
    ("Finance", ["bank", "krediet", "credit", "lening", "loan", "rekening", "account statement", "overschrijving", "transfer", "financ"], "Mail/02 Geldzaken & betalingen"),
    ("Accounts & Security", ["security", "beveiliging", "password", "wachtwoord", "login", "sign-in", "aanmelding", "2fa", "verification code", "verificatiecode"], "Mail/03 Accounts & beveiliging"),
    ("Health", ["medical", "medisch", "doctor", "arts", "hospital", "ziekenhuis", "mutualiteit", "health", "gezondheid"], "Mail/05 Gezondheid"),
    ("Family", ["serenity", "family", "familie", "school", "opvang", "child", "kind"], "Mail/04 Familie & Serenity"),
]

_INVOICE_TERMS = ("invoice", "factuur", "payment request", "betalingsverzoek", "te betalen", "amount due", "openstaand bedrag")


def normalize_sender(sender: str) -> str:
    _, address = parseaddr(sender)
    return (address or sender).strip().lower()


def strip_quoted_history_and_signature(text: str, max_chars: int = 18_000) -> str:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)

    quote_patterns = [
        r"(?im)^on .{1,180}wrote:\s*$",
        r"(?im)^op .{1,180}schreef .{0,120}:\s*$",
        r"(?im)^-{2,}\s*original message\s*-{2,}\s*$",
        r"(?im)^-{2,}\s*oorspronkelijk bericht\s*-{2,}\s*$",
        r"(?im)^from:\s.+$",
        r"(?im)^van:\s.+$",
    ]
    cut = len(text)
    for pattern in quote_patterns:
        match = re.search(pattern, text)
        if match and match.start() > 20:
            cut = min(cut, match.start())
    text = text[:cut]

    # Remove a trailing quoted block (common in plaintext replies).
    lines = text.split("\n")
    while lines and lines[-1].lstrip().startswith(">"):
        lines.pop()
    text = "\n".join(lines)

    signature_patterns = [
        r"(?im)^--\s*$",
        r"(?im)^(kind regards|best regards|regards|met vriendelijke groet(?:en)?|vriendelijke groet(?:en)?|mvg|groeten)[,!]?\s*$",
    ]
    for pattern in signature_patterns:
        matches = list(re.finditer(pattern, text))
        if matches:
            match = matches[-1]
            if match.start() > 10:
                text = text[: match.start()]
                break

    text = re.sub(r"\n{4,}", "\n\n\n", text).strip()
    return text[:max_chars]


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def local_extract(body: str, attachments: list[dict[str, Any]]) -> dict[str, Any]:
    attachment_text = "\n".join(str(item.get("extracted_text") or "")[:8_000] for item in attachments)
    combined = f"{body}\n{attachment_text}"[:48_000]
    lower = combined.lower()

    iban_lengths = {
        "AT": 20, "BE": 16, "BG": 22, "CH": 21, "CY": 28, "CZ": 24, "DE": 22,
        "DK": 18, "EE": 20, "ES": 24, "FI": 18, "FR": 27, "GB": 22, "GR": 27,
        "HR": 21, "HU": 28, "IE": 22, "IS": 26, "IT": 27, "LI": 21, "LT": 20,
        "LU": 20, "LV": 21, "MC": 27, "MT": 31, "NL": 18, "NO": 15, "PL": 28,
        "PT": 25, "RO": 24, "SE": 24, "SI": 19, "SK": 24,
    }

    def iban_valid(value: str) -> bool:
        if not (15 <= len(value) <= 34) or not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]+", value):
            return False
        expected = iban_lengths.get(value[:2])
        if expected and len(value) != expected:
            return False
        rearranged = value[4:] + value[:4]
        numeric = "".join(str(ord(char) - 55) if char.isalpha() else char for char in rearranged)
        try:
            return int(numeric) % 97 == 1
        except ValueError:
            return False

    ibans = []
    for line in combined.splitlines():
        for start in re.finditer(r"\b[A-Z]{2}[ ]?\d{2}", line, re.I):
            tail = line[start.start():]
            raw_match = re.match(r"[A-Z]{2}[ ]?\d{2}(?:[ ]?[A-Z0-9]){11,40}", tail, re.I)
            if not raw_match:
                continue
            compact = re.sub(r"[ ]+", "", raw_match.group(0)).upper()
            expected = iban_lengths.get(compact[:2])
            candidates = [compact[:expected]] if expected and len(compact) >= expected else [compact[:length] for length in range(15, min(34, len(compact)) + 1)]
            for value in candidates:
                if iban_valid(value):
                    ibans.append(value)
                    break

    amounts: list[str] = []
    amount_patterns = [
        r"(?:€|EUR)\s*([0-9][0-9.,\s]*(?:[,.][0-9]{2})?)",
        r"([0-9][0-9.,\s]*(?:[,.][0-9]{2}))\s*(?:€|EUR)\b",
    ]
    for pattern in amount_patterns:
        for match in re.finditer(pattern, combined, re.I):
            raw = match.group(1).replace(" ", "")
            if raw:
                amounts.append(raw)

    invoice_number = ""
    invoice_match = re.search(
        r"(?im)\b(?:invoice(?:\s*(?:number|no\.?))?|factuur(?:nummer|nr\.?|\s*nummer)?|document\s*(?:number|no\.?))\s*[:#-]?\s*([A-Z0-9][A-Z0-9._/-]{2,40})",
        combined,
    )
    if invoice_match:
        invoice_number = invoice_match.group(1).strip()

    reference = ""
    structured = re.search(r"\+\+\+\s*\d{3}/\d{4}/\d{5}\s*\+\+\+", combined)
    if structured:
        reference = re.sub(r"\s+", "", structured.group(0))
    else:
        reference_match = re.search(
            r"(?im)\b(?:structured communication|gestructureerde mededeling|reference|referentie|mededeling)\s*[:#-]?\s*([^\n]{3,80})",
            combined,
        )
        if reference_match:
            reference = reference_match.group(1).strip()

    due_dates: list[str] = []
    for match in re.finditer(
        r"(?im)\b(?:due date|vervaldatum|betaal(?:baar)?\s+voor|te betalen voor|pay by)\s*[: -]?\s*(\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{4}-\d{2}-\d{2})",
        combined,
    ):
        due_dates.append(match.group(1))

    date_times = _unique(
        re.findall(
            r"\b(?:\d{4}-\d{2}-\d{2}[T ]\d{1,2}:\d{2}|\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\s+(?:om\s+|at\s+)?\d{1,2}:\d{2})\b",
            combined,
            re.I,
        )
    )[:8]

    cues = []
    cue_map = {
        "invoice": _INVOICE_TERMS,
        "calendar": ("appointment", "afspraak", "meeting", "vergadering", "reservation", "reservering"),
        "order": ("order", "bestelling", "tracking", "shipment", "verzending", "delivery", "levering"),
        "subscription": ("subscription", "abonnement", "renewal", "verlenging", "next charge", "volgende betaling"),
        "security": ("security", "beveiliging", "password", "wachtwoord", "verification", "verificatie", "login", "aanmelding"),
        "legal": ("advocaat", "lawyer", "rechtbank", "court", "deurwaarder", "bailiff"),
    }
    for cue, terms in cue_map.items():
        if any(term in lower for term in terms):
            cues.append(cue)

    return {
        "iban_candidates": _unique(ibans)[:6],
        "amount_candidates": _unique(amounts)[:10],
        "invoice_number": invoice_number,
        "reference": reference,
        "due_date_candidates": _unique(due_dates)[:6],
        "date_time_candidates": date_times,
        "cues": cues,
    }


def has_dynamic_signals(subject: str, body: str, extraction: dict[str, Any]) -> bool:
    haystack = f"{subject}\n{body[:8_000]}".lower()
    if extraction.get("cues") or extraction.get("iban_candidates") or extraction.get("date_time_candidates"):
        return True
    return any(term in haystack for term in _DYNAMIC_TERMS)


def protected_hint(sender: str, subject: str, body: str) -> tuple[str, str] | None:
    haystack = f"{sender}\n{subject}\n{body[:8_000]}".lower()
    for category, terms, label in _PROTECTED_GROUPS:
        if any(term in haystack for term in terms):
            return category, label
    return None


def urgent_hint(subject: str, body: str, extraction: dict[str, Any]) -> bool:
    haystack = f"{subject}\n{body[:6_000]}".lower()
    urgent_terms = (
        "urgent", "dringend", "overdue", "achterstallig", "final reminder", "laatste herinnering",
        "today", "vandaag", "tomorrow", "morgen", "court", "rechtbank", "deurwaarder", "bailiff",
        "suspended", "geblokkeerd", "security alert", "beveiligingswaarschuwing",
    )
    return any(term in haystack for term in urgent_terms) or "legal" in extraction.get("cues", [])


def content_fingerprint(sender: str, subject: str, body: str, attachments: list[dict[str, Any]]) -> str:
    attachment_material = []
    for item in attachments:
        attachment_material.append(
            {
                "filename": str(item.get("filename") or "").lower(),
                "mime_type": str(item.get("mime_type") or ""),
                "extracted_text": strip_quoted_history_and_signature(str(item.get("extracted_text") or ""), 6_000),
            }
        )
    canonical = json.dumps(
        {
            "sender": normalize_sender(sender),
            "subject": re.sub(r"\s+", " ", subject or "").strip().lower(),
            "body": strip_quoted_history_and_signature(body, 18_000),
            "attachments": attachment_material,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def cached_decision(db: AsyncSession, fingerprint: str) -> AutomationDecision | None:
    row = await db.get(MessageFingerprint, fingerprint)
    if row is None:
        return None
    try:
        decision = AutomationDecision.model_validate_json(row.decision_json)
    except Exception:
        return None
    row.use_count += 1
    row.last_used_at = datetime.utcnow()
    return decision


async def cache_decision(
    db: AsyncSession,
    fingerprint: str,
    message_id: str,
    decision: AutomationDecision,
) -> None:
    row = await db.get(MessageFingerprint, fingerprint)
    if row is None:
        db.add(
            MessageFingerprint(
                fingerprint=fingerprint,
                source_message_id=message_id,
                decision_json=decision.model_dump_json(),
            )
        )
    else:
        row.decision_json = decision.model_dump_json()
        row.last_used_at = datetime.utcnow()


async def sender_rule_for(db: AsyncSession, sender: str) -> SenderRule | None:
    key = normalize_sender(sender)
    if not key:
        return None
    return (
        await db.execute(select(SenderRule).where(SenderRule.sender_key == key))
    ).scalar_one_or_none()


def decision_from_sender_rule(rule: SenderRule, *, is_read: bool) -> AutomationDecision:
    labels = json.loads(rule.labels_json or "[]")
    return AutomationDecision(
        category=rule.category,
        priority=rule.priority,
        action_required=False,
        preserve=rule.preserve,
        archive=rule.archive,
        trash=bool(rule.trash_when_read and is_read),
        labels=labels,
        task=None,
        bill=None,
        calendar_event=None,
        reply=None,
        support_case=None,
        order=None,
        subscription=None,
        archive_attachments=False,
        reasoning_summary=f"Applied learned sender rule after {rule.sample_count} consistent messages.",
    )


def deterministic_shortcut(
    *,
    sender: str,
    subject: str,
    body: str,
    headers: dict[str, str],
    label_ids: set[str],
    is_read: bool,
    extraction: dict[str, Any],
    sender_rule: SenderRule | None,
) -> tuple[AutomationDecision | None, str]:
    dynamic = has_dynamic_signals(subject, body, extraction)
    if sender_rule and sender_rule.safe_shortcut and not dynamic:
        return decision_from_sender_rule(sender_rule, is_read=is_read), "sender_rule"

    list_unsubscribe = bool(headers.get("list-unsubscribe"))
    if list_unsubscribe and not dynamic:
        return (
            AutomationDecision(
                category="Newsletters & Promotions",
                priority="low",
                action_required=False,
                preserve=False,
                archive=True,
                trash=is_read,
                labels=["Mail/90 Lage prioriteit/Nieuwsbrieven & reclame"],
                task=None,
                bill=None,
                calendar_event=None,
                reply=None,
                support_case=None,
                order=None,
                subscription=None,
                archive_attachments=False,
                reasoning_summary="Deterministic newsletter rule (List-Unsubscribe present).",
            ),
            "deterministic",
        )

    if "CATEGORY_SOCIAL" in label_ids and not dynamic:
        return (
            AutomationDecision(
                category="Social & Communities",
                priority="low",
                action_required=False,
                preserve=False,
                archive=True,
                trash=False,
                labels=["Mail/90 Lage prioriteit/Sociaal & communities"],
                task=None,
                bill=None,
                calendar_event=None,
                reply=None,
                support_case=None,
                order=None,
                subscription=None,
                archive_attachments=False,
                reasoning_summary="Deterministic Gmail social-category rule.",
            ),
            "deterministic",
        )

    normalized_sender = normalize_sender(sender)
    low_subject = subject.lower()
    notification_terms = ("notification", "melding", "digest", "summary", "samenvatting", "activity", "activiteit")
    if ("no-reply" in normalized_sender or "noreply" in normalized_sender) and any(term in low_subject for term in notification_terms) and not dynamic:
        return (
            AutomationDecision(
                category="Notifications",
                priority="low",
                action_required=False,
                preserve=False,
                archive=True,
                trash=is_read,
                labels=["Mail/90 Lage prioriteit/Meldingen"],
                task=None,
                bill=None,
                calendar_event=None,
                reply=None,
                support_case=None,
                order=None,
                subscription=None,
                archive_attachments=False,
                reasoning_summary="Deterministic routine notification rule.",
            ),
            "deterministic",
        )
    return None, ""


async def learn_sender_rule(db: AsyncSession, sender: str, decision: AutomationDecision) -> None:
    # Only learn classification-only outcomes. Dynamic decisions must continue to inspect each message.
    if (
        decision.action_required
        or decision.task
        or decision.bill
        or decision.calendar_event
        or decision.reply
        or decision.support_case
        or decision.order
        or decision.subscription
        or decision.archive_attachments
    ):
        return
    safe_categories = ("newsletter", "promotion", "notification", "social", "communit")
    if not any(term in decision.category.lower() for term in safe_categories):
        return
    key = normalize_sender(sender)
    if not key:
        return
    labels_json = json.dumps(sorted(set(decision.labels)), ensure_ascii=False)
    row = (
        await db.execute(select(SenderRule).where(SenderRule.sender_key == key))
    ).scalar_one_or_none()
    signature = (decision.category, decision.priority, decision.preserve, decision.archive, decision.trash, labels_json)
    if row is None:
        db.add(
            SenderRule(
                sender_key=key,
                category=decision.category,
                priority=decision.priority,
                preserve=decision.preserve,
                archive=decision.archive,
                trash_when_read=decision.trash,
                labels_json=labels_json,
                sample_count=1,
                safe_shortcut=False,
            )
        )
        return
    current = (row.category, row.priority, row.preserve, row.archive, row.trash_when_read, row.labels_json)
    if current == signature:
        row.sample_count += 1
        row.safe_shortcut = row.sample_count >= 3
    else:
        row.category = decision.category
        row.priority = decision.priority
        row.preserve = decision.preserve
        row.archive = decision.archive
        row.trash_when_read = decision.trash
        row.labels_json = labels_json
        row.sample_count = 1
        row.safe_shortcut = False
    row.last_seen_at = datetime.utcnow()


def _first_amount(extraction: dict[str, Any]) -> str | None:
    values = extraction.get("amount_candidates") or []
    return str(values[0]) if values else None


def safe_fallback_decision(
    *,
    sender: str,
    subject: str,
    body: str,
    is_read: bool,
    extraction: dict[str, Any],
    reason: str,
) -> AutomationDecision:
    protected = protected_hint(sender, subject, body)
    category = protected[0] if protected else "AI review required"
    labels = [protected[1]] if protected else []
    preserve = protected is not None
    priority = "high" if protected or urgent_hint(subject, body, extraction) else "normal"
    bill: dict[str, Any] | None = None
    if "invoice" in extraction.get("cues", []) and _first_amount(extraction):
        sender_name, sender_addr = parseaddr(sender)
        bill = {
            "creditor_name": sender_name.strip() or sender_addr or sender,
            "amount": _first_amount(extraction),
            "currency": "EUR",
            "due_at": None,
            "iban": (extraction.get("iban_candidates") or [None])[0],
            "reference": extraction.get("reference") or "",
            "invoice_number": extraction.get("invoice_number") or "",
            "account_scope": "personal",
        }
        category = "Finance"
        labels = list(dict.fromkeys(labels + ["Mail/02 Geldzaken & betalingen/Facturen & betalingen"]))
        preserve = True
    return AutomationDecision(
        category=category,
        priority=priority,
        action_required=True,
        preserve=preserve,
        archive=False,
        trash=False,
        labels=labels,
        task=None,
        bill=bill,
        calendar_event=None,
        reply=None,
        support_case=None,
        order=None,
        subscription=None,
        archive_attachments=bool(protected or bill),
        reasoning_summary=f"Safe deterministic fallback because AI was unavailable: {reason}",
    )
