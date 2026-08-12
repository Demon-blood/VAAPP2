from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from app.services.revolut_statement_parser import parse_revolut_pdf, parse_revolut_xlsx
from revolut_test_fixtures import _synthetic_revolut_pdf, _synthetic_revolut_xlsx


def test_revolut_xlsx_is_fee_aware_and_ignores_reverted_rows() -> None:
    parsed = parse_revolut_xlsx(_synthetic_revolut_xlsx())
    assert parsed.provider == "revolut"
    assert parsed.source_format == "xlsx"
    assert parsed.opening_balance == Decimal("40.00")
    assert parsed.total_credits == Decimal("109.99")
    assert parsed.total_debits == Decimal("20.49")
    assert parsed.closing_balance == Decimal("129.50")
    assert len(parsed.transactions) == 5
    assert parsed.ignored_transaction_count == 1
    assert parsed.transactions[3].amount == Decimal("9.99")
    assert parsed.transactions[4].direction == "credit"
    assert parsed.transactions[4].amount == Decimal("9.99")


def test_revolut_pdf_matches_xlsx_primary_and_exposes_secondary_account() -> None:
    xlsx = parse_revolut_xlsx(_synthetic_revolut_xlsx())
    pdf = parse_revolut_pdf(_synthetic_revolut_pdf())
    assert len(pdf) == 2
    assert pdf[0].statement_key == xlsx.statement_key
    assert len(pdf[0].transactions) == len(xlsx.transactions) == 5
    assert pdf[0].ignored_transaction_count == 1
    assert pdf[0].account_iban == "BE00123456789012"
    assert pdf[1].account_display_name == "Junior Tester"
    assert pdf[1].opening_balance == Decimal("0.00")
    assert pdf[1].closing_balance == Decimal("5.00")


def test_real_statement_files_are_not_bundled_as_test_fixtures() -> None:
    root = Path(__file__).parents[2]
    assert not list(root.rglob("*.xlsx"))
    assert not list(root.rglob("*.pdf"))
    fixture = (root / "backend" / "tests" / "revolut_test_fixtures.py").read_text()
    assert "FAKE OWNER" in fixture
    assert "BE00123456789012" in fixture
    # Public test fixtures are deliberately synthetic; real uploaded statement files
    # are external validation inputs and are never copied into the repository.
    assert "Junior Tester" in fixture
