from pathlib import Path


def _root() -> Path:
    return Path(__file__).resolve().parents[2]


def test_v081_release_identity_and_dedicated_money_tab() -> None:
    root = _root()
    assert 'APP_VERSION = "1.0.19"' in (root / "backend/app/core/version.py").read_text()
    assert 'version: 1.0.19+62' in (root / "android/pubspec.yaml").read_text()
    money = (root / "android/lib/screens/money_page.dart").read_text()
    assert "Tab(text: 'Investments')" in money
    assert 'InvestmentsPage()' in money
    assert 'length: 7' in money


def test_investments_dashboard_has_real_data_sources() -> None:
    root = _root()
    page = (root / "android/lib/screens/investments_page.dart").read_text()
    state = (root / "android/lib/app_state.dart").read_text()
    routes = (root / "backend/app/api/routes.py").read_text()
    service = (root / "backend/app/services/investment_service.py").read_text()
    kraken = (root / "backend/app/integrations/kraken_api.py").read_text()
    assert '/api/finance/investments' in state
    assert 'financeInvestments' in state
    assert 'Revolut-managed execution' in page
    assert 'Performance & income' in page
    assert 'Contributions & Investment Autopilot' in page
    assert 'get_eur_valued_balances' in routes
    assert 'total_value_by_currency' in service
    assert 'is_latest_snapshot' in service
    assert 'statement.positions and is_latest_snapshot' in service
    assert '/0/private/Balance' in kraken
    assert '/0/public/Ticker' in kraken
    assert 'estimated_value_eur' in kraken


def test_budget_no_longer_contains_investment_dashboard_card() -> None:
    root = _root()
    budget = (root / "android/lib/screens/finance_autopilot_page.dart").read_text()
    assert '_InvestmentSummaryCard' not in budget


def test_kraken_dashboard_never_adds_withdrawal_capability() -> None:
    root = _root()
    kraken = (root / "backend/app/integrations/kraken_api.py").read_text()
    funding = (root / "backend/app/services/investment_autopilot.py").read_text()
    assert '/0/private/Withdraw' not in kraken
    assert 'BankAccount.account_scope == "personal"' in funding
    assert 'func.lower(BankConnection.psu_type) == "personal"' in funding
