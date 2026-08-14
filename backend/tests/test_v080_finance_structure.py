from pathlib import Path


def _root() -> Path:
    return Path(__file__).resolve().parents[2]


def test_v080_release_identity_and_account_roles() -> None:
    root = _root()
    assert 'APP_VERSION = "1.0.1"' in (root / "backend/app/core/version.py").read_text()
    assert 'version: 1.0.1+43' in (root / "android/pubspec.yaml").read_text()
    finance = (root / "backend/app/services/financial_autopilot.py").read_text()
    assert 'role == "spending"' in finance
    assert '"investment_contribution"' in finance
    assert '"owner_draw"' in finance
    assert 'tax_virtual_reserve' in finance


def test_revolut_personal_and_pro_use_personal_revolut_consent() -> None:
    root = _root()
    source = (root / "android/lib/screens/services_page.dart").read_text()
    banking = (root / "backend/app/services/banking_service.py").read_text()
    assert "Connect Revolut Personal / Pro" in source
    assert "psuType: 'business'" not in source
    assert "_derive_account_scope" in banking
    assert 'derived_scope == "pro"' in banking
    assert 'account.account_scope == "personal"' in banking


def test_kraken_autopilot_has_no_withdrawal_execution() -> None:
    root = _root()
    kraken = (root / "backend/app/integrations/kraken_api.py").read_text()
    funding = (root / "backend/app/services/investment_autopilot.py").read_text()
    assert "/0/private/AddOrder" in kraken
    assert "/0/private/DepositStatus" in kraken
    assert "/0/private/Withdraw" not in kraken
    assert 'BankAccount.account_scope == "personal"' in funding
    assert 'func.lower(BankConnection.psu_type) == "personal"' in funding
    assert '_kraken_source_policy_error' in funding
    assert 'kraken_personal_owner_confirmed' in funding
    assert 'missing": "recipient/IBAN"' in funding
    assert 'missing": "recipient/IBAN/reference"' not in funding
    assert 'kraken_auto_fund_enabled' in funding
    assert 'kraken_auto_trade_enabled' in funding
    assert 'creation_uncertain' in funding


def test_investment_statement_import_is_wired() -> None:
    root = _root()
    parser = (root / "backend/app/services/revolut_investment_parser.py").read_text()
    routes = (root / "backend/app/api/routes.py").read_text()
    assert "ROBO MANAGEMENT FEE" in parser.upper()
    assert "looks_like_revolut_investment" in routes
    assert '"/api/finance/investments"' in routes
    assert '"/api/banking/investment-callback"' in routes
