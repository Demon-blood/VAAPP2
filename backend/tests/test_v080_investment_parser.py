from decimal import Decimal

from app.services.revolut_investment_parser import (
    parse_revolut_investment_account_xlsx,
    parse_revolut_investment_pnl_xlsx,
)
from investment_test_fixtures import synthetic_investment_account_xlsx, synthetic_investment_pnl_xlsx


def test_revolut_investment_account_xlsx_separates_brokerage_and_robo() -> None:
    brokerage = parse_revolut_investment_account_xlsx(synthetic_investment_account_xlsx(robo=False))
    robo = parse_revolut_investment_account_xlsx(synthetic_investment_account_xlsx(robo=True))
    assert brokerage.portfolio_kind == "brokerage"
    assert robo.portfolio_kind == "robo"
    assert len(brokerage.transactions) == 2
    assert len(robo.transactions) == 3
    assert robo.transactions[-1].amount == Decimal("-0.05")


def test_revolut_investment_pnl_xlsx_parses_fifo_and_income_sections() -> None:
    parsed = parse_revolut_investment_pnl_xlsx(synthetic_investment_pnl_xlsx())
    assert len(parsed.sells) == 1
    assert parsed.sells[0].cost_basis == Decimal("10")
    assert parsed.sells[0].gross_proceeds == Decimal("12")
    assert parsed.sells[0].gross_pnl == Decimal("2")
    assert len(parsed.income) == 1
    assert parsed.income[0].withholding_tax == Decimal("0.1")
    assert parsed.income[0].net_amount == Decimal("0.9")
