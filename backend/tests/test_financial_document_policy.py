from app.services.ai_policy import deterministic_shortcut, local_extract, safe_fallback_decision
from app.services.financial_document_policy import (
    PAID_RECEIPT,
    PAYABLE_INVOICE,
    STATEMENT_OR_NOTICE,
    assess_financial_document,
)


def test_google_play_gpa_confirmation_is_paid_receipt_not_bill() -> None:
    body = """
    Thank you for your purchase on Google Play.
    Order number: GPA.3368-6930-9897-52444
    Total: EUR 3.19
    Your payment was successful.
    """
    extraction = local_extract(body, [])
    assessment = assess_financial_document(
        sender="Google Commerce Limited <payments-noreply@google.com>",
        subject="Your Google Play order receipt",
        body=body,
        extraction=extraction,
        bill={"amount": "3.19", "invoice_number": "GPA.3368-6930-9897-52444"},
    )
    assert assessment.document_type == PAID_RECEIPT
    assert assessment.order_number == "GPA.3368-6930-9897-52444"
    assert assessment.confidence > 0.99

    fallback = safe_fallback_decision(
        sender="Google Commerce Limited <payments-noreply@google.com>",
        subject="Your Google Play order receipt",
        body=body,
        is_read=True,
        extraction=extraction,
        reason="test",
    )
    assert fallback.financial_document_type == PAID_RECEIPT
    assert fallback.bill is None
    assert fallback.action_required is False

    shortcut, source = deterministic_shortcut(
        sender="Google Commerce Limited <payments-noreply@google.com>",
        subject="Your Google Play order receipt",
        body=body,
        headers={},
        label_ids={"INBOX"},
        is_read=False,
        extraction=extraction,
        sender_rule=None,
    )
    assert source == "deterministic_financial"
    assert shortcut is not None
    assert shortcut.bill is None
    assert shortcut.financial_document_type == PAID_RECEIPT
    assert shortcut.archive is True


def test_explicit_google_amount_due_stays_payable() -> None:
    body = """
    Invoice GPA.1111-2222-3333-44444
    Amount due: EUR 25.00
    Due date: 2026-08-20
    Please pay the outstanding balance by the due date.
    """
    extraction = local_extract(body, [])
    assessment = assess_financial_document(
        sender="Google Commerce Limited <billing@example.com>",
        subject="Invoice payment due",
        body=body,
        extraction=extraction,
        bill={"amount": "25.00", "due_at": "2026-08-20"},
    )
    assert assessment.document_type == PAYABLE_INVOICE


def test_generic_invoice_requires_outstanding_payment_evidence() -> None:
    payable_body = "Invoice 12345\nAmount due EUR 89.99\nDue date 2026-08-25\nPlease pay by bank transfer."
    payable = assess_financial_document(
        sender="Example Supplier <billing@example.test>",
        subject="Invoice 12345",
        body=payable_body,
        extraction=local_extract(payable_body, []),
        bill={"amount": "89.99", "due_at": "2026-08-25"},
    )
    assert payable.document_type == PAYABLE_INVOICE

    informational_body = "Invoice number 12345\nEUR 89.99\nThis document is for your records."
    informational = assess_financial_document(
        sender="Example Supplier <billing@example.test>",
        subject="Invoice copy",
        body=informational_body,
        extraction=local_extract(informational_body, []),
        bill={"amount": "89.99", "invoice_number": "12345"},
    )
    assert informational.document_type == STATEMENT_OR_NOTICE


def test_common_commerce_payment_confirmations_are_nonpayable() -> None:
    cases = [
        (
            "PayPal <service@paypal.com>",
            "Payment confirmation",
            "You paid EUR 12.50. Your payment was successful.",
        ),
        (
            "Amazon <order-update@amazon.example>",
            "Order confirmation",
            "Order number 123-1234567-1234567. Total EUR 42.00. Thank you for your purchase.",
        ),
        (
            "Apple <no_reply@apple.com>",
            "Your receipt from Apple",
            "Receipt for your App Store purchase. EUR 1.99 was charged to your payment method.",
        ),
        (
            "Stripe <receipts@stripe.com>",
            "Payment receipt",
            "Payment received. EUR 9.00. This is your receipt.",
        ),
    ]
    for sender, subject, body in cases:
        assessment = assess_financial_document(
            sender=sender,
            subject=subject,
            body=body,
            extraction=local_extract(body, []),
            bill={"amount": "1.00"},
        )
        assert assessment.document_type == PAID_RECEIPT, (sender, assessment)


def test_nonpayable_financial_signal_does_not_suppress_protected_legal_review() -> None:
    body = "Lawyer case receipt. Payment received EUR 50.00 for court filing."
    extraction = local_extract(body, [])
    fallback = safe_fallback_decision(
        sender="Lawyer <office@example.test>",
        subject="Court filing receipt",
        body=body,
        is_read=True,
        extraction=extraction,
        reason="test",
    )
    assert fallback.bill is None
    assert fallback.financial_document_type == PAID_RECEIPT
    assert fallback.category == "Legal & Government"
    assert fallback.action_required is True
