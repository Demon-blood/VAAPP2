from pathlib import Path


ROOT = Path(__file__).parents[2]


def test_v096_release_identity() -> None:
    assert 'APP_VERSION = "1.0.11"' in (ROOT / "backend/app/core/version.py").read_text()
    assert 'version = "1.0.11"' in (ROOT / "backend/pyproject.toml").read_text()
    assert "version: 1.0.11+54" in (ROOT / "android/pubspec.yaml").read_text()
    workflow = (ROOT / ".github/workflows/android-release.yml").read_text()
    assert "Full-Time-VA-Android-v1.0.5.apk" in workflow


def test_forecast_models_are_additive_and_encrypted() -> None:
    source = (ROOT / "backend/app/models/entities.py").read_text()
    assert 'class FinancialForecastRun(Base):' in source
    assert 'class FinancialAllocationPlan(Base):' in source
    assert 'class FinancialAllocationAction(Base):' in source
    assert 'snapshot_encrypted' in source
    assert 'details_encrypted' in source
    assert 'own_account_transfer_id' in source
    assert 'UniqueConstraint("forecast_run_id", "account_scope"' in source


def test_forecast_is_conservative_and_source_backed() -> None:
    source = (ROOT / "backend/app/services/financial_forecasting.py").read_text()
    assert 'learn_recurring_cashflows' in source
    assert 'monthly_spend_by_scope' in source
    assert 'budget_cash_plan_by_scope' in source
    assert 'investment_funding_forecast_by_scope' in source
    assert 'effective_available_balance' in source
    assert 'conservative_factor = Decimal("0.00")' in source
    assert 'daily_variable * Decimal("1.10")' in source
    assert 'allocatable = max(Decimal("0.00"), conservative_min - protected_floor)' in source


def test_allocations_reuse_real_transfer_executor_and_never_cross_scope() -> None:
    source = (ROOT / "backend/app/services/financial_forecasting.py").read_text()
    assert 'create_own_account_transfer' in source
    assert 'account.account_scope == scope' in source
    assert 'policy.role == "operating"' in source
    assert 'policy.accept_surplus' in source
    assert 'policy.role in {"spending", "tax", "reserve", "savings"}' in source
    assert 'action.status = "creation_uncertain"' in source
    assert 'action.status = "needs_user_auth"' in source
    assert 'action.status = "verified"' in source
    assert 'action.status = "dispatching"' in source
    assert 'OwnAccountTransfer.idempotency_key == action.idempotency_key' in source
    assert 'action.status = "planned"' in source
    assert 'allocation_compatibility_summary' in source


def test_banking_worker_uses_forecast_allocator() -> None:
    workflow = (ROOT / "backend/app/services/workflow_engine.py").read_text()
    assert '@job_handler("banking.autopilot")' in workflow
    assert 'run_financial_allocation_cycle' in workflow
    assert 'force_forecast=False' in workflow
    assert '"forecast_allocation": allocation' in workflow


def test_forecast_routes_and_mobile_workspace_exist() -> None:
    routes = (ROOT / "backend/app/api/routes.py").read_text()
    state = (ROOT / "android/lib/app_state.dart").read_text()
    money = (ROOT / "android/lib/screens/money_page.dart").read_text()
    forecast = (ROOT / "android/lib/screens/financial_forecast_page.dart").read_text()
    assert '@router.get("/api/finance/forecast")' in routes
    assert '@router.post("/api/finance/forecast/run")' in routes
    assert "_safeGet('/api/finance/forecast', optional: true)" in state
    assert "runFinancialForecastNow" in state
    assert "Tab(text: 'Forecast')" in money
    assert 'FinancialForecastPage()' in money
    assert 'Only cash that remains surplus in the conservative scenario can be allocated.' in forecast
    assert 'A plan is not complete until the bank transfer is verified.' in forecast
