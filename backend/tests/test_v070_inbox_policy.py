from app.schemas.api import AutomationDecision
from app.services.email_processor import _apply_inbox_policy


def test_routine_classified_mail_is_archived_even_if_ai_forgot() -> None:
    decision = AutomationDecision(category="Shopping", priority="normal", action_required=False, labels=["Mail/Shopping"])
    _apply_inbox_policy(decision, protected=False)
    assert decision.archive is True
    assert decision.trash is False


def test_action_required_or_high_priority_mail_stays_in_inbox() -> None:
    action = AutomationDecision(category="General", priority="normal", action_required=True)
    _apply_inbox_policy(action, protected=False)
    assert action.archive is False

    high = AutomationDecision(category="General", priority="high", action_required=False)
    _apply_inbox_policy(high, protected=False)
    assert high.archive is False


def test_paid_receipt_is_preserved_but_filed_out_of_inbox() -> None:
    decision = AutomationDecision(
        category="Finance",
        financial_document_type="paid_receipt",
        priority="normal",
        action_required=False,
        preserve=True,
    )
    _apply_inbox_policy(decision, protected=True)
    assert decision.preserve is True
    assert decision.archive is True


def test_reply_candidate_stays_in_inbox_until_reply_succeeds() -> None:
    decision = AutomationDecision(
        category="Conversation",
        priority="normal",
        action_required=False,
        reply={"to": "a@example.com", "subject": "Re: hi", "body": "Thanks"},
    )
    _apply_inbox_policy(decision, protected=False)
    assert decision.archive is False
