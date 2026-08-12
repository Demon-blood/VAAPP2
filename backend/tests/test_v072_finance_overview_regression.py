from datetime import datetime
from decimal import Decimal

from app.services.financial_autopilot import _aggregate_current_month


def test_finance_overview_accepts_six_field_learning_rows_and_handles_refunds() -> None:
    rows = [
        ("personal", datetime(2026, 8, 1), "shopping", Decimal("100.00"), "debit", False),
        ("personal", datetime(2026, 8, 2), "shopping", Decimal("25.00"), "credit", True),
        ("personal", datetime(2026, 8, 3), "salary", Decimal("1500.00"), "credit", False),
    ]

    spent, income = _aggregate_current_month(rows)

    assert spent[("personal", "shopping")] == Decimal("75.00")
    assert income["personal"] == Decimal("1500.00")


def test_refund_cannot_make_category_spend_negative() -> None:
    rows = [
        ("personal", datetime(2026, 8, 1), "digital", Decimal("5.00"), "credit", True),
    ]

    spent, income = _aggregate_current_month(rows)

    assert spent[("personal", "digital")] == Decimal("0.00")
    assert income == {}
