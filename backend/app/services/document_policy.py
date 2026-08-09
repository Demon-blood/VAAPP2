from __future__ import annotations

import re

_LOW_VALUE_DOCUMENT_PATTERNS = [
    re.compile(pattern, re.I)
    for pattern in (
        r"(?:^|[_ .-])terms?(?:[_ .-]+of)?[_ .-]+service(?:[_ .-]|$)",
        r"(?:^|[_ .-])terms?[_ .-]+and[_ .-]+conditions?(?:[_ .-]|$)",
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
) -> tuple[bool, str]:
    """Decide whether an attachment is worth durable VA document retention.

    Explicitly important filenames win over generic boilerplate terms. This means a signed
    agreement or actual invoice remains retainable even if its filename also mentions terms.
    """
    filename = (name or "").strip()
    lower_name = filename.lower()
    normalized_mime = (mime_type or "").strip().lower()

    if any(pattern.search(lower_name) for pattern in _HIGH_VALUE_DOCUMENT_PATTERNS):
        return True, "high_value_filename"

    if any(pattern.search(lower_name) for pattern in _LOW_VALUE_DOCUMENT_PATTERNS):
        return False, "boilerplate_policy_or_terms"

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
