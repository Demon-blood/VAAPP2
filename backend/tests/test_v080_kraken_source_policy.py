from types import SimpleNamespace

from app.services.investment_autopilot import _kraken_source_policy_error


def _account(**overrides):
    data = {
        "account_scope": "personal",
        "currency": "EUR",
        "enabled_for_payments": True,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _connection(**overrides):
    data = {"psu_type": "personal"}
    data.update(overrides)
    return SimpleNamespace(**data)


def test_kraken_source_policy_accepts_personal_eur_payment_account() -> None:
    assert _kraken_source_policy_error(_account(), _connection()) is None


def test_kraken_source_policy_blocks_pro_scope() -> None:
    error = _kraken_source_policy_error(_account(account_scope="pro"), _connection())
    assert error is not None
    assert "Personal-scope" in error


def test_kraken_source_policy_blocks_business_psu_even_if_account_mislabeled_personal() -> None:
    error = _kraken_source_policy_error(_account(), _connection(psu_type="business"))
    assert error is not None
    assert "personal bank consent" in error


def test_kraken_source_policy_blocks_non_eur_and_non_payment_sources() -> None:
    assert "EUR source" in (_kraken_source_policy_error(_account(currency="USD"), _connection()) or "")
    assert "payment-enabled" in (
        _kraken_source_policy_error(_account(enabled_for_payments=False), _connection()) or ""
    )
