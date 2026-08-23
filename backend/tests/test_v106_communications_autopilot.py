from types import SimpleNamespace

from app.services.communication_attention import (
    is_marketing_subscription_false_positive,
    normalize_communication_attention,
)


def payload(body: str, provider: str = "android_sms"):
    return SimpleNamespace(body=body, provider=provider, direction="incoming", channel="sms", event_type="message")


def base(**overrides):
    value = {
        "category": "Conversation", "priority": "normal", "action_required": True,
        "protected": False, "spam": False, "auto_reply_safe": False,
        "reply_text": None, "reasoning_summary": "test",
    }
    value.update(overrides)
    return value


def test_otp_is_protected_evidence_not_user_work():
    result = normalize_communication_attention(payload("Your verification code is 123456"), base(protected=True))
    assert result["protected"] is True
    assert result["action_required"] is False
    assert result["interrupt"] is False


def test_financial_confirmation_is_quiet_but_protected():
    result = normalize_communication_attention(payload("EUR withdrawal initiated successfully"), base(protected=True))
    assert result["protected"] is True
    assert result["action_required"] is False
    assert result["interrupt"] is False


def test_historical_sms_never_manufactures_new_user_work():
    result = normalize_communication_attention(payload("Please call us", "android_sms_history"), base(action_required=True))
    assert result["action_required"] is False


def test_fraud_alert_is_a_real_interrupt():
    result = normalize_communication_attention(payload("Suspicious transaction: this payment was not authorized by you"), base(protected=True))
    assert result["action_required"] is True
    assert result["interrupt"] is True
    assert result["priority"] == "urgent"


def test_unsubscribe_footer_is_not_paid_subscription():
    assert is_marketing_subscription_false_positive(
        sender="newsletter@example.com", subject="Weekly news",
        body="Read this week's stories. Unsubscribe from this newsletter.",
        subscription={"provider_name": "Example", "description": "Newsletter"},
    ) is True


def test_real_billed_subscription_is_not_suppressed():
    assert is_marketing_subscription_false_positive(
        sender="service@example.com", subject="Your plan renews",
        body="Your subscription renews monthly. Next charge €9.99. Unsubscribe from marketing mail here.",
        subscription={"provider_name": "Example", "description": "Premium plan"},
    ) is False
