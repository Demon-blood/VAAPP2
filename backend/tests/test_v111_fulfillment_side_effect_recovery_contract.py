from pathlib import Path

ROOT = Path(__file__).parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_v111_release_identity_is_consistent():
    assert 'APP_VERSION = "1.0.19"' in read("backend/app/core/version.py")
    assert 'REQUIRED_ANDROID_VERSION = "1.0.19"' in read("backend/app/core/version.py")
    assert 'version = "1.0.19"' in read("backend/pyproject.toml")
    assert "version: 1.0.19+62" in read("android/pubspec.yaml")
    release = read("android/lib/release_contract.dart")
    assert "appRelease = '1.0.19'" in release
    assert "minimumBackendVersion = '1.0.19'" in release


def test_non_replay_safe_side_effect_marker_survives_until_postcondition_verification():
    browser = read("backend/app/services/browser_operator.py")
    assert "def operation_requires_postcondition_reconciliation" in browser
    assert "operation.side_effect_step is not None" in browser
    assert "operation.current_step >= operation.side_effect_step" in browser
    assert '"replay_safe": replay_safe' in browser
    assert 'operation.status = "creation_uncertain"' in browser
    assert "Provider side effect may already have occurred" in browser


def test_creation_uncertain_resume_is_verification_only_not_recipe_replay():
    browser = read("backend/app/services/browser_operator.py")
    resume = browser.split("async def resume_browser_operation", 1)[1].split("async def browser_status", 1)[0]
    execute = browser.split("async def execute_browser_operation", 1)[1].split(
        "def operation_requires_material_decision", 1
    )[0]
    assert '"creation_uncertain"' in resume
    assert "operation.side_effect_step is None" in resume
    assert "operation_requires_postcondition_reconciliation(operation)" in execute
    reconcile = execute.split("operation_requires_postcondition_reconciliation(operation)", 1)[1].split(
        "username, password = await _auto_login_if_needed", 1
    )[0]
    assert "verify_page" in reconcile
    assert 'operation.status = "verified"' in reconcile
    assert 'operation.status = "creation_uncertain"' in reconcile
    assert "_perform_step(" not in reconcile


def test_provider_runtime_failures_cannot_downgrade_ambiguous_side_effect_to_failed():
    browser = read("backend/app/services/browser_operator.py")
    tail = browser.split("except BrowserSecurityError as exc:", 1)[1].split(
        "def operation_requires_material_decision", 1
    )[0]
    assert tail.count("operation_requires_postcondition_reconciliation(operation)") >= 3
    assert tail.count('operation.status = "creation_uncertain"') >= 3
    assert "Automatic replay remains blocked" in tail
    assert "Provider timeout during side-effect reconciliation" in tail
    assert "Provider/runtime error during side-effect reconciliation" in tail


def test_fulfillment_keeps_uncertainty_va_owned_and_reuses_same_browser_operation():
    fulfillment = read("backend/app/services/fulfillment_service.py")
    assert "resume_browser_operation" in fulfillment
    reconcile = fulfillment.split("async def _reconcile_existing_action", 1)[1].split(
        "async def run_request", 1
    )[0]
    branch = reconcile.split('if operation.status == "creation_uncertain":', 1)[1].split(
        'action.status = "failed"', 1
    )[0]
    assert "await resume_browser_operation(db, operation.id)" in branch
    assert 'action.status = "waiting_provider"' in branch
    assert 'request.status = "waiting_provider"' in branch
    assert "request.requires_user_action = False" in branch
    assert 'request.needs_user_reason = ""' in branch
    assert "timedelta(minutes=30)" in branch


def test_project_metadata_preserves_original_v1_baseline_evidence():
    state = read("VAAPP_PROJECT_STATE.json")
    handoff = read("VAAPP_PROJECT_HANDOFF.md")
    assert '"verified_baseline_actions_run": 41' in state
    assert '"verified_baseline_actions_conclusion": "success"' in state
    assert "GitHub Actions run #41" in handoff
    assert "v1.0.11" in read("STATUS.md")
