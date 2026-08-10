from datetime import datetime
from decimal import Decimal

from app.models.entities import BankAccount, Bill, EmailMessage, Payment
from app.services.briefing_service import _mail_item, _payment_item, _summary_text


def test_mail_briefing_reuses_saved_reasoning_without_new_ai_call() -> None:
    message = EmailMessage(
        provider_message_id="msg-1",
        thread_id="thread-1",
        sender="Dentist <clinic@example.com>",
        subject="Your appointment has moved",
        snippet="The appointment is now Thursday at 10:30.",
        received_at=datetime.utcnow(),
        category="Appointments",
        priority="high",
        action_required=False,
        analysis_json='{"reasoning_summary":"The dentist moved the appointment to Thursday at 10:30.","calendar_event":{"summary":"Dentist"},"archive":true}',
    )
    item = _mail_item(message, ["calendar_event_created"])
    assert item["summary"] == "The dentist moved the appointment to Thursday at 10:30."
    assert "Calendar event created" in item["outcome"]
    assert item["action_required"] is False


def test_payment_briefing_explains_what_payment_is_for() -> None:
    bill = Bill(
        creditor_name="Proximus",
        amount=Decimal("89.99"),
        currency="EUR",
        invoice_number="INV-2026-08",
        reference="+++123/4567/89012+++",
        account_scope="personal",
        status="validated",
    )
    account = BankAccount(
        bank_connection_id=1,
        external_account_id="acc-1",
        name="Beobank current account",
        account_scope="personal",
        currency="EUR",
    )
    payment = Payment(
        bill_id=1,
        bank_account_id=1,
        amount=Decimal("89.99"),
        currency="EUR",
        status="completed",
        requires_user_action=False,
    )
    item = _payment_item(payment, bill, account)
    assert "Proximus" in item["purpose"]
    assert "INV-2026-08" in item["purpose"]
    assert item["amount_text"] == "89.99 EUR"
    assert item["account"] == "Beobank current account"


def test_summary_never_claims_pending_payment_completed() -> None:
    stats = {
        "emails_received": 4,
        "emails_handled_automatically": 4,
        "needs_you": 0,
        "calendar_changes": 0,
    }
    payments = [{"status": "received", "purpose": "Utility invoice"}]
    text = _summary_text(stats, payments, [])
    assert "completed" not in text.lower()
    assert "still in progress" in text.lower()


def test_summary_reports_failed_payment_without_calling_it_pending() -> None:
    stats = {
        "emails_received": 1,
        "emails_handled_automatically": 1,
        "needs_you": 0,
        "calendar_changes": 0,
    }
    payments = [{"status": "failed", "purpose": "Energy invoice"}]
    text = _summary_text(stats, payments, [])
    assert "payment failed" in text.lower()
    assert "still in progress" not in text.lower()


def test_summary_uses_configured_window_length() -> None:
    stats = {
        "emails_received": 2,
        "emails_handled_automatically": 2,
        "needs_you": 0,
        "calendar_changes": 0,
    }
    text = _summary_text(stats, [], [], window_hours=12)
    assert "last 12 hours" in text
