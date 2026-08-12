from __future__ import annotations

import re

_LOW_VALUE_DOCUMENT_PATTERNS = [
    re.compile(pattern, re.I)
    for pattern in (
        r"(?:^|[_ .-])terms?(?:[_ .-]+of)?[_ .-]+service(?:[_ .-]|$)",
        r"(?:^|[_ .-])terms?[_ .-]+and[_ .-]+conditions?(?:[_ .-]|$)",
        # Branded/localized boilerplate such as transfernow-terms-en.pdf or
        # transfernow-conditions-fr.pdf. The suffix is anchored near the extension
        # so an ordinary file merely mentioning "terms" in the middle is not dropped.
        r"(?:^|[_ .-])(?:terms?|conditions?|voorwaarden)(?:[_ .-]+[a-z]{2,5})?(?:\.[a-z0-9]{1,8})?$",
        r"(?:^|[_ .-])conditions?[_ .-]+g[eé]n[eé]rales?(?:[_ .-]|$)",
        r"(?:^|[_ .-])conditions?[_ .-]+g[eé]n[eé]rales?[_ .-]+(?:d['’]?utilisation|de[_ .-]+vente)(?:[_ .-]|$)",
        r"(?:^|[_ .-])algemene[_ .-]+voorwaarden(?:[_ .-]|$)",
        r"(?:^|[_ .-])(?:gebruiks|service|verkoops?)voorwaarden(?:[_ .-]|$)",
        r"(?:^|[_ .-])privacy[_ .-]+polic(?:y|ies)(?:[_ .-]|$)",
        r"(?:^|[_ .-])cookie[_ .-]+(?:policy|notice|settings?)(?:[_ .-]|$)",
        r"(?:^|[_ .-])acceptable[_ .-]+use[_ .-]+policy(?:[_ .-]|$)",
        r"(?:^|[_ .-])legal[_ .-]+notice(?:[_ .-]|$)",
        r"(?:^|[_ .-])unsubscribe(?:[_ .-]|$)",
        r"(?:^|[_ .-])imprint(?:[_ .-]|$)",
        r"(?:^|[_ .-])disclaimer(?:[_ .-]|$)",
    )
]

_HIGH_VALUE_DOCUMENT_PATTERNS = [
    re.compile(pattern, re.I)
    for pattern in (
        r"invoice|factuur|receipt|bonnetje|statement|afschrift|contract|agreement|overeenkomst",
        r"signed|ondertekend|certificate|attest|insurance|verzekering|tax|belasting|payslip|loonfiche",
        r"medical|medisch|court|rechtbank|lawyer|advocaat|bailiff|deurwaarder|official|officieel",
    )
]

_HIGH_VALUE_TEXT_PATTERNS = [
    re.compile(pattern, re.I)
    for pattern in (
        r"\b(?:invoice|factuur)\s*(?:number|nummer|nr\.?|#)",
        r"\b(?:amount due|te betalen|vervaldatum|payment due)\b",
        r"\b(?:account statement|rekeningafschrift|rekeninguittreksel|bank statement)\b",
        r"\b(?:signed by|ondertekend door|policy number|polisnummer)\b",
    )
]

_LOW_VALUE_TEXT_PATTERNS = [
    re.compile(pattern, re.I)
    for pattern in (
        r"\bterms of service\b",
        r"\bterms and conditions\b",
        r"\bgeneral terms and conditions\b",
        r"\bconditions g[eé]n[eé]rales(?: d['’]?utilisation| de vente)?\b",
        r"\balgemene voorwaarden\b",
        r"\bgebruiksvoorwaarden\b",
        r"\bprivacy policy\b",
        r"\bprivacybeleid\b",
        r"\bcookie policy\b",
        r"\bcookiebeleid\b",
    )
]

_LOW_VALUE_EXACT_MIME_TYPES = {
    "text/calendar",
    "text/vcard",
    "text/x-vcard",
    "application/ics",
    "application/pkcs7-signature",
    "application/x-pkcs7-signature",
}


def document_retention_decision(
    name: str,
    mime_type: str = "",
    size_bytes: int = 0,
    extracted_text: str = "",
) -> tuple[bool, str]:
    """Decide whether an attachment is worth durable VA document retention.

    Strong evidence of a real financial/legal/personal record wins. Generic website
    policies and terms are discarded even when a brand/language prefix is present.
    """
    filename = (name or "").strip()
    lower_name = filename.lower()
    normalized_mime = (mime_type or "").strip().lower()
    text = (extracted_text or "")[:60000]

    if any(pattern.search(lower_name) for pattern in _HIGH_VALUE_DOCUMENT_PATTERNS):
        return True, "high_value_filename"
    if text and any(pattern.search(text) for pattern in _HIGH_VALUE_TEXT_PATTERNS):
        return True, "high_value_document_text"

    if any(pattern.search(lower_name) for pattern in _LOW_VALUE_DOCUMENT_PATTERNS):
        return False, "boilerplate_policy_or_terms"
    if text and any(pattern.search(text) for pattern in _LOW_VALUE_TEXT_PATTERNS):
        return False, "boilerplate_policy_or_terms_text"

    if normalized_mime in _LOW_VALUE_EXACT_MIME_TYPES or lower_name.endswith((".ics", ".vcf", ".p7s")):
        return False, "non_document_attachment"

    # Do not reject arbitrary image attachments because scans/photos can be durable records.
    # Only obvious mail-branding assets are suppressed, and only when reasonably small.
    if normalized_mime.startswith("image/"):
        generic_image = bool(
            re.search(
                r"(?:^|[_ .-])(logo|signature|banner|spacer|pixel|facebook|instagram|linkedin|twitter|x-logo|image00\d|img00\d)(?:[_ .-]|$)",
                lower_name,
                re.I,
            )
        )
        if generic_image and size_bytes <= 512_000:
            return False, "mail_branding_image"

    return True, "retain"


def document_category_decision(
    *,
    name: str,
    extracted_text: str = "",
    parent_category: str = "general",
) -> str:
    """Classify the retained document itself instead of blindly inheriting email category."""
    text = f"{name}\n{extracted_text[:50000]}".casefold()
    if any(term in text for term in ("rekeninguittreksel", "rekeningafschrift", "account statement", "bank statement")):
        return "finance"
    if any(term in text for term in ("invoice", "factuur", "tax", "belasting", "payslip", "loonfiche")):
        return "finance"
    if any(term in text for term in ("receipt", "purchase", "order confirmation", "aankoopbewijs", "bestelbevestiging")):
        return "purchase"
    if any(term in text for term in ("signed", "ondertekend", "contract", "agreement", "overeenkomst", "certificate", "attest")):
        return "important"
    return (parent_category or "general").strip()[:120]
