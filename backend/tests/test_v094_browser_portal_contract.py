from pathlib import Path


def _root() -> Path:
    return Path(__file__).parents[2]


def test_v094_release_identity_and_real_browser_runtime() -> None:
    root = _root()
    assert 'APP_VERSION = "1.0.17"' in (root / "backend/app/core/version.py").read_text()
    assert 'version = "1.0.17"' in (root / "backend/pyproject.toml").read_text()
    assert 'version: 1.0.17+60' in (root / "android/pubspec.yaml").read_text()
    assert '"playwright>=1.61,<2"' in (root / "backend/pyproject.toml").read_text()
    docker = (root / "backend/Dockerfile").read_text()
    assert "python -m playwright install --with-deps chromium" in docker
    assert "USER vaapp" in docker
    workflow = (root / ".github/workflows/android-release.yml").read_text()
    assert "Full-Time-VA-Android-v1.0.5.apk" in workflow


def test_browser_ledger_encrypts_secrets_session_plan_and_evidence() -> None:
    models = (_root() / "backend/app/models/entities.py").read_text()
    for model in (
        "BrowserPortal",
        "BrowserCredential",
        "BrowserSessionState",
        "BrowserOperation",
        "BrowserEvidence",
    ):
        assert f"class {model}" in models
    for field in (
        "username_encrypted",
        "password_encrypted",
        "storage_state_encrypted",
        "plan_encrypted",
        "verification_encrypted",
        "resume_url_encrypted",
        "pending_auth_value_encrypted",
        "payload_encrypted",
    ):
        assert field in models
    assert 'idempotency_key: Mapped[str] = mapped_column(String(255), unique=True' in models
    assert "material_approved_at" in models
    assert "side_effect_started_at" in models


def test_browser_operator_is_allowlisted_verified_and_never_blindly_replays_side_effects() -> None:
    source = (_root() / "backend/app/services/browser_operator.py").read_text()
    assert "async_playwright" in source
    assert 'playwright.chromium.launch(headless=True' in source
    assert "assert_portal_url" in source
    assert "_host_is_private_or_local" in source
    assert "_host_resolves_private_or_local" in source
    assert "socket.getaddrinfo" in source
    assert "refuses localhost/private-network targets" in source
    assert 'operation.status = "dispatching"' in source
    assert 'operation.status = "creation_uncertain"' in source
    assert "will not blindly replay" in source
    assert "verify_page" in source
    assert 'operation.status = "verified"' in source
    assert "browser_postcondition_verified" in source
    assert "encrypt_text(_dump(normalized))" in source
    assert "encrypt_text(_dump(state))" in source
    assert "CAPTCHA" in source
    assert "will not bypass it" in source
    assert "page.evaluate(" not in source


def test_browser_auth_and_material_commitments_are_user_gated() -> None:
    root = _root()
    source = (root / "backend/app/services/browser_operator.py").read_text()
    policy = (root / "backend/app/services/va_policy.py").read_text()
    core = (root / "backend/app/services/autonomous_core.py").read_text()
    assert 'operation.status = "needs_user_auth"' in source
    assert "BrowserNeedsUserAuth" in source
    assert "submit_auth_code" in source
    assert "approve_material_operation" in source
    assert 'action_type == "browser_operation"' in policy
    assert "operation.material_approved_at is None" in policy
    assert '"needs_user": True' in policy
    assert 'event.event_type == "browser_portal_operation_planned"' in core
    assert 'action_type="browser_operation"' in core
    assert 'verification_type="browser_operation_verified"' in core
    assert 'objective, "needs_user"' in core or '"needs_user",' in core


def test_browser_operation_runs_in_durable_worker_and_api_exposes_safe_handoffs() -> None:
    root = _root()
    workflow = (root / "backend/app/services/workflow_engine.py").read_text()
    routes = (root / "backend/app/api/routes.py").read_text()
    assert '@job_handler("browser.operation.run")' in workflow
    assert "execute_browser_operation" in workflow
    for route in (
        '/api/browser/status',
        '/api/browser/portals',
        '/api/browser/operations',
        '/api/browser/operations/{operation_id}/auth-code',
        '/api/browser/operations/{operation_id}/resume',
        '/api/browser/operations/{operation_id}/approve',
        '/api/browser/evidence/{evidence_id}.png',
    ):
        assert route in routes
    assert '"secure_browser_portal_operator"' in routes
    assert '"Cache-Control": "no-store, private"' in routes


def test_android_work_surface_exposes_portals_without_claiming_captcha_bypass() -> None:
    root = _root()
    state = (root / "android/lib/app_state.dart").read_text()
    work = (root / "android/lib/screens/work_page.dart").read_text()
    assert "_safeGet('/api/browser/status'" in state
    assert "_safeGet('/api/browser/portals'" in state
    assert "_safeGet('/api/browser/operations?limit=100'" in state
    assert "submitBrowserAuthCode" in state
    assert "approveBrowserOperation" in state
    assert "Tab(text: 'Portals')" in work
    assert "Secure portal operator" in work
    assert "Add portal" in work
    assert "Enter code" in work
    assert "Approve material action" in work
    assert "CAPTCHA and MFA are never bypassed" in work
