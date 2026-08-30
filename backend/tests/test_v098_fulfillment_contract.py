from pathlib import Path


ROOT = Path(__file__).parents[2]


def test_v098_release_identity() -> None:
    assert 'APP_VERSION = "1.0.9"' in (ROOT / "backend/app/core/version.py").read_text()
    assert 'version = "1.0.9"' in (ROOT / "backend/pyproject.toml").read_text()
    assert "version: 1.0.9+52" in (ROOT / "android/pubspec.yaml").read_text()
    state = (ROOT / "android/lib/app_state.dart").read_text()
    assert "_versionAtLeast(backendVersion, minimumBackendVersion)" in state
    workflow = (ROOT / ".github/workflows/android-release.yml").read_text()
    assert "Full-Time-VA-Android-v1.0.5.apk" in workflow


def test_fulfillment_ledger_is_additive_encrypted_and_idempotent() -> None:
    source = (ROOT / "backend/app/models/fulfillment_entities.py").read_text()
    for model in ("FulfillmentProvider", "FulfillmentRequest", "FulfillmentAction", "FulfillmentEvidence"):
        assert f"class {model}(Base):" in source
    assert "idempotency_key" in source
    assert "goal_encrypted" in source
    assert "details_encrypted" in source
    assert "recipe_encrypted" in source
    assert "support_phone_encrypted" in source
    assert "material_authorized_at" in source
    assert "authorization_basis" in source
    assert 'UniqueConstraint("request_id", "sequence"' in source
    database = (ROOT / "backend/app/core/database.py").read_text()
    assert "import app.models.fulfillment_entities" in database


def test_provider_action_is_persisted_before_real_external_execution() -> None:
    source = (ROOT / "backend/app/services/fulfillment_service.py").read_text()
    persist = source.index("# Persist the local intent before any provider-specific preparation or POST.")
    prepare = source.index("operation = await prepare_browser_operation(")
    call = source.index("call = await create_outbound_call(")
    assert persist < prepare
    assert persist < call
    assert "enqueue_browser_operation" in source
    assert "create_outbound_call" in source
    assert 'operation.status == "creation_uncertain"' in source or 'operation.status == "creation_uncertain"' in source
    assert 'request.status = "blocked_system" if operation.status == "creation_uncertain" else "failed"' in source


def test_purchase_and_travel_payment_authority_is_explicit_and_bounded() -> None:
    source = (ROOT / "backend/app/services/fulfillment_service.py").read_text()
    runtime = (ROOT / "backend/app/services/runtime_config.py").read_text()
    assert '"fulfillment_auto_purchase_enabled"' in runtime
    assert '"fulfillment_max_single_purchase_eur"' in runtime
    assert '"fulfillment_auto_travel_enabled"' in runtime
    assert '"fulfillment_max_single_travel_eur"' in runtime
    assert '"fulfillment_monthly_purchase_limit_eur"' in runtime
    assert 'request.currency != "EUR" or request.amount is None or request.amount <= 0' in source
    assert 'return True, "standing_spend_policy"' in source
    assert 'request.authorization_basis = "specific_user_authorization"' in source
    assert 'operation.material_approved_at = utcnow()' in source
    assert 'evidence_type="standing_payment_authorization"' in source
    assert 'evidence_type="specific_payment_authorization"' in source
    assert "browser_material_operation_preauthorized_by_fulfillment_policy" in source


def test_existing_orders_and_support_cases_become_owned_objectives() -> None:
    source = (ROOT / "backend/app/services/fulfillment_service.py").read_text()
    assert "async def ingest_existing_operations" in source
    assert 'request_type="logistics"' in source
    assert 'request_type="customer_service"' in source
    assert 'source_type="order"' in source
    assert 'source_type="support_case"' in source
    assert "ORDER_TERMINAL_STATES" in source
    assert "SUPPORT_TERMINAL_STATES" in source
    assert 'provider="order_ledger"' in source
    assert 'provider="support_case_ledger"' in source
    assert 'evidence_type="objective_completed"' in source


def test_fulfillment_routes_scheduler_capability_and_android_workspace_exist() -> None:
    routes = (ROOT / "backend/app/api/fulfillment_routes.py").read_text()
    for path in (
        "/api/fulfillment/status",
        "/api/fulfillment/providers",
        "/api/fulfillment/requests",
        "/api/fulfillment/requests/{request_id}",
        "/api/fulfillment/requests/{request_id}/run",
        "/api/fulfillment/requests/{request_id}/authorize-payment",
        "/api/fulfillment/reconcile",
    ):
        assert path in routes
    assert "require_device" in routes
    main = (ROOT / "backend/app/main.py").read_text()
    assert "app.include_router(fulfillment_router)" in main
    scheduler = (ROOT / "backend/app/services/scheduler.py").read_text()
    assert "fulfillment_reconcile_job" in scheduler
    assert 'id="fulfillment_reconcile"' in scheduler
    capabilities = (ROOT / "backend/app/services/capability_registry.py").read_text()
    assert '"fulfillment_automation"' in capabilities
    page = (ROOT / "android/lib/screens/fulfillment_page.dart").read_text()
    shell = (ROOT / "android/lib/screens/home_shell.dart").read_text()
    assert "Purchasing · Travel · Logistics · Customer Service" in page
    assert "/api/fulfillment/requests" in page
    assert "Authorize payment" in page
    assert "Browser/payment intent is never treated as proof of completion." in page
    assert "FulfillmentPage" in shell
    assert "Purchasing, travel & support" in shell


def test_provider_recipe_execution_is_allowlisted_and_not_universal_claim() -> None:
    fulfillment = (ROOT / "backend/app/services/fulfillment_service.py").read_text()
    browser = (ROOT / "backend/app/services/browser_operator.py").read_text()
    assert "provider.browser_portal_id" in fulfillment
    assert "prepare_browser_operation" in fulfillment
    assert "Provider {provider.name} has no real executor recipe" in fulfillment
    assert "assert_portal_url" in browser
    assert "Browser operator refuses localhost/private-network targets" in browser
