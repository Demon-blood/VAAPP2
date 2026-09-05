from pathlib import Path


def _root() -> Path:
    return Path(__file__).resolve().parents[2]


def test_v117_release_identity_and_additive_recovery_ledgers() -> None:
    root = _root()
    version = (root / "backend/app/core/version.py").read_text()
    pubspec = (root / "android/pubspec.yaml").read_text()
    models = (root / "backend/app/models/entities.py").read_text()

    assert 'APP_VERSION = "1.0.18"' in version
    assert 'REQUIRED_ANDROID_VERSION = "1.0.18"' in version
    assert "version: 1.0.18+61" in pubspec
    assert "class InvestmentFundingRecoveryEvidence" in models
    assert '__tablename__ = "investment_funding_recovery_evidence"' in models
    assert "class InvestmentTradeIntent" in models
    assert '__tablename__ = "investment_trade_intents"' in models
    assert 'uq_investment_funding_recovery_account_transaction' in models
    assert "client_order_id" in models


def test_kraken_trade_api_has_read_only_client_order_reconciliation() -> None:
    root = _root()
    kraken = (root / "backend/app/integrations/kraken_api.py").read_text()

    assert "class KrakenOrderCreationUncertainError" in kraken
    assert "async def get_orders_by_client_order_id" in kraken
    assert '"/0/private/OpenOrders"' in kraken
    assert '"/0/private/ClosedOrders"' in kraken
    assert '"cl_ord_id": client_order_id' in kraken
    assert '"/0/private/AddOrder"' in kraken


def test_funding_creation_uncertainty_is_va_owned_without_fake_needs_you() -> None:
    root = _root()
    source = (root / "backend/app/services/investment_autopilot.py").read_text()
    block = source.split("async def run_kraken_funding_autopilot", 1)[1].split(
        "async def refresh_kraken_funding_transfer", 1
    )[0]

    assert 'transfer.status = "creation_uncertain"' in block
    assert 'transfer.requires_user_action = False' in block
    assert 'transfer.authorization_url = None' in block
    assert 'source_type="kraken_funding_uncertain"' not in block
    assert "Check bank before retrying Kraken funding" not in block
    assert "await db.delete(state_row)" in block


def test_unbound_funding_and_trade_ambiguity_are_reconciliation_only() -> None:
    root = _root()
    source = (root / "backend/app/services/investment_autopilot.py").read_text()
    recovery = (root / "backend/app/services/investment_recovery.py").read_text()

    assert "reconcile_uncertain_kraken_funding" in source
    assert 'if transfer.status == "trade_pending":' in source
    assert "reconcile_kraken_trade_intent" in source
    assert "KrakenOrderCreationUncertainError" in source
    assert 'intent.status = "creation_uncertain"' in source
    assert "query-open-trades" in source
    assert "query-closed-trades" in source
    assert "automatic_retry\": False" in recovery
    assert "stable_trade_client_order_id" in recovery
    assert "legacy_trade_client_order_id" in recovery


def test_trade_intent_is_committed_before_add_order_and_never_replayed_when_uncertain() -> None:
    root = _root()
    source = (root / "backend/app/services/investment_autopilot.py").read_text()
    trade = source.split("async def reconcile_kraken_funding_and_trade", 1)[1].split(
        "async def refresh_all_kraken_funding", 1
    )[0]

    prepared = trade.index("prepare_kraken_trade_intent")
    commit = trade.index("await db.commit()", prepared)
    provider = trade.index("market_buy_eur", commit)
    assert prepared < commit < provider
    pending = trade.index('if transfer.status == "trade_pending":')
    reconcile = trade.index("reconcile_kraken_trade_intent", pending)
    assert pending < reconcile < provider


def test_v117_project_contract_preserves_historical_release_evidence() -> None:
    root = _root()
    state = (root / "VAAPP_PROJECT_STATE.json").read_text()
    handoff = (root / "VAAPP_PROJECT_HANDOFF.md").read_text()

    assert '"verified_baseline_actions_run": 41' in state
    assert '"verified_baseline_actions_conclusion": "success"' in state
    assert "GitHub Actions run #41" in handoff
    assert "830c2c87b89972bc0735028584285f2827ac4bf9" in handoff
    assert "33975481668" in handoff
    assert "va-android-116-3-1" in handoff
