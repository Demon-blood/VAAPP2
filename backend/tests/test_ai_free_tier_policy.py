from __future__ import annotations

from app.integrations.ai_client import AUTOMATION_DECISION_SCHEMA
from app.services.ai_policy import (
    deterministic_shortcut,
    local_extract,
    strip_quoted_history_and_signature,
)
from app.services.runtime_config import CONFIG_SECTIONS


def _assert_strict_objects(schema: dict) -> None:
    if schema.get("type") == "object":
        properties = schema.get("properties") or {}
        assert schema.get("additionalProperties") is False
        assert set(schema.get("required") or []) == set(properties)
    for value in schema.values():
        if isinstance(value, dict):
            _assert_strict_objects(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    _assert_strict_objects(item)


def test_groq_schema_is_strict_compatible() -> None:
    _assert_strict_objects(AUTOMATION_DECISION_SCHEMA)


def test_quote_and_signature_stripping_reduces_repeated_history() -> None:
    body = "Hello, please review this.\n\nKind regards,\nGeert\n\nOn Fri, Someone wrote:\n> old message\n> more old message"
    stripped = strip_quoted_history_and_signature(body)
    assert stripped == "Hello, please review this."


def test_local_invoice_extraction_uses_body_without_ai() -> None:
    extraction = local_extract(
        "Factuur INV-2026-42\nTe betalen: EUR 123,45\nIBAN BE68 5390 0754 7034\nGestructureerde mededeling +++123/4567/89012+++\nVervaldatum: 20/08/2026",
        [],
    )
    assert extraction["invoice_number"] == "INV-2026-42"
    assert "123,45" in extraction["amount_candidates"]
    assert "BE68539007547034" in extraction["iban_candidates"]
    assert extraction["reference"] == "+++123/4567/89012+++"
    assert "invoice" in extraction["cues"]
    assert all("\n" not in value and "\r" not in value for value in extraction["amount_candidates"])


def test_amount_extraction_does_not_cross_line_boundaries() -> None:
    extraction = local_extract(
        "Te betalen: EUR 123,45\n2026-08-20\nIBAN BE68 5390 0754 7034",
        [],
    )
    assert extraction["amount_candidates"] == ["123,45"]


def test_read_newsletter_is_shortcut_without_ai() -> None:
    decision, source = deterministic_shortcut(
        sender="News <newsletter@example.com>",
        subject="August news",
        body="Here are this month's stories.",
        headers={"list-unsubscribe": "<mailto:unsubscribe@example.com>"},
        label_ids={"CATEGORY_PROMOTIONS"},
        is_read=True,
        extraction={"cues": [], "iban_candidates": [], "date_time_candidates": []},
        sender_rule=None,
    )
    assert source == "deterministic"
    assert decision is not None
    assert decision.category == "Newsletters & Promotions"
    assert decision.archive is True
    assert decision.trash is True


def test_ai_defaults_match_groq_free_tier_strategy() -> None:
    fields = {field["key"]: field for field in CONFIG_SECTIONS["ai"]["fields"]}
    assert fields["ai_base_url"]["default"] == "https://api.groq.com/openai/v1"
    assert fields["ai_model"]["default"] == "openai/gpt-oss-20b"
    assert fields["ai_daily_request_budget"]["default"] == "1000"
    assert fields["ai_daily_token_budget"]["default"] == "200000"
    assert fields["ai_fallback_allow_sensitive"]["default"] == "false"


def test_amount_parser_handles_european_and_dot_decimals() -> None:
    from app.services.email_processor import _parse_amount

    assert str(_parse_amount("1.234,56 EUR")) == "1234.56"
    assert str(_parse_amount("123.45")) == "123.45"
    assert str(_parse_amount("€ 99,95")) == "99.95"
