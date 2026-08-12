from __future__ import annotations

from app.services.document_policy import document_retention_decision


def test_generic_terms_and_policy_attachments_are_filtered() -> None:
    for name in [
        "Terms_of_Service_de_be.html",
        "Terms_of_Service_nl_be.html",
        "Terms and Conditions.pdf",
        "privacy_policy.pdf",
        "Cookie-Notice.html",
        "unsubscribe.html",
        "transfernow-terms-en.pdf",
        "transfernow-conditions-fr.pdf",
        "provider-algemene-voorwaarden-nl.pdf",
    ]:
        keep, reason = document_retention_decision(name, "text/html", 40_000)
        assert keep is False, (name, reason)


def test_high_value_documents_override_generic_terms_words() -> None:
    keep, _ = document_retention_decision(
        "Signed_Service_Agreement_Terms_and_Conditions.pdf",
        "application/pdf",
        500_000,
    )
    assert keep is True


def test_business_records_are_retained() -> None:
    examples = [
        ("Invoice_2026-0812.pdf", "application/pdf"),
        ("Bank_Statement_July.pdf", "application/pdf"),
        ("Uber_Eats_Receipt.pdf", "application/pdf"),
        ("Contract_Provider.pdf", "application/pdf"),
        ("Tax_Document_2026.pdf", "application/pdf"),
    ]
    for name, mime in examples:
        keep, reason = document_retention_decision(name, mime, 200_000)
        assert keep is True, (name, reason)


def test_calendar_vcard_and_small_branding_assets_are_filtered() -> None:
    assert document_retention_decision("invite.ics", "text/calendar", 2_000)[0] is False
    assert document_retention_decision("contact.vcf", "text/vcard", 1_500)[0] is False
    assert document_retention_decision("logo.png", "image/png", 18_000)[0] is False
    assert document_retention_decision("scanned_receipt.jpg", "image/jpeg", 180_000)[0] is True


def test_pdf_text_can_filter_boilerplate_even_when_filename_is_opaque() -> None:
    keep, reason = document_retention_decision(
        "document-23918.pdf",
        "application/pdf",
        90_000,
        "TransferNow - Terms and Conditions\nThese terms of service govern your use of the website.",
    )
    assert keep is False
    assert reason == "boilerplate_policy_or_terms_text"


def test_real_financial_text_wins_over_terms_boilerplate_signal() -> None:
    keep, reason = document_retention_decision(
        "document-23919.pdf",
        "application/pdf",
        90_000,
        "ACCOUNT STATEMENT\nTerms and conditions apply to this account.\nClosing balance EUR 1200.00",
    )
    assert keep is True
    assert reason == "high_value_document_text"
