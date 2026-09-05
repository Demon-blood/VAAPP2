from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_generic_browser_uncertainty_reuses_safe_postcondition_only_resume():
    core = _text("backend/app/services/autonomous_core.py")
    helper = core.split("async def _resume_browser_reconciliation", 1)[1].split(
        "async def _recover_legacy_browser_uncertainty", 1
    )[0]
    assert "operation_requires_postcondition_reconciliation" in helper
    assert "resume_browser_operation" in helper
    assert "operation.id" in helper
    assert 'step.status = "verifying"' in helper
    assert "step.finished_at = None" in helper
    assert 'await _transition_objective(db, objective, "verifying")' in helper


def test_execute_and_verify_paths_do_not_terminalize_safe_creation_uncertainty():
    core = _text("backend/app/services/autonomous_core.py")
    execute = core.split('elif step.action_type == "browser_operation":', 1)[1].split(
        'elif step.action_type == "record_only":', 1
    )[0]
    execute_uncertain = execute.split('elif operation.status == "creation_uncertain":', 1)[1].split(
        'elif operation.status == "blocked_capability":', 1
    )[0]
    assert "await _resume_browser_reconciliation" in execute_uncertain
    assert "durable side-effect marker" in execute_uncertain

    verify = core.split('if step.verification_type == "browser_operation_verified":', 1)[1].split(
        'if step.verification_type == "calendar_mutation_verified":', 1
    )[0]
    verify_uncertain = verify.split('if operation.status == "creation_uncertain":', 1)[1].split(
        'if operation.status == "blocked_capability":', 1
    )[0]
    assert "await _resume_browser_reconciliation" in verify_uncertain
    assert "durable side-effect marker" in verify_uncertain


def test_historical_failed_browser_uncertainty_is_reopened_without_new_operation():
    core = _text("backend/app/services/autonomous_core.py")
    recovery = core.split("async def _recover_legacy_browser_uncertainty", 1)[1].split(
        "async def _recover_legacy_gmail_uncertainty", 1
    )[0]
    assert 'VAObjectiveStep.status == "failed"' in recovery
    assert 'VAObjectiveStep.verification_type == "browser_operation_verified"' in recovery
    assert 'operation.status != "creation_uncertain"' in recovery
    assert "await _resume_browser_reconciliation" in recovery
    assert "browser_operation_legacy_uncertainty_reopened" in recovery
    assert '"automatic_replay": False' in recovery
    assert '"recovery": "provider_postcondition_only"' in recovery
    assert "await _recover_legacy_browser_uncertainty(db, now)" in core
    assert "prepare_browser_operation" not in recovery


def test_document_form_projection_reports_active_reconciliation_not_terminal_block():
    source = _text("backend/app/services/document_ownership.py")
    branch = source.split('if operation.status == "creation_uncertain":', 1)[1].split(
        'if operation.status == "failed":', 1
    )[0]
    assert "operation_requires_postcondition_reconciliation(operation)" in branch
    assert 'row.status = "in_progress"' in branch
    assert 'submission.status = "in_progress"' in branch
    assert 'result["in_progress"] += 1' in branch
    assert 'row.status = "blocked_system"' in branch
    assert 'result["blocked"] += 1' in branch



def test_legacy_v095_form_contract_tracks_reconciliation_owned_uncertainty():
    legacy = _text("backend/tests/test_v095_documents_forms_deadlines_contract.py")
    assert 'if operation.status in {"creation_uncertain", "failed"}:' not in legacy
    assert 'if operation.status == "creation_uncertain":' in legacy
    assert 'operation_requires_postcondition_reconciliation(operation)' in legacy
    assert 'row.status = "in_progress"' in legacy
    assert 'if operation.status == "failed":' in legacy
    assert 'row.status = "blocked_system"' in legacy

def test_v111_browser_safety_primitive_remains_fail_closed_and_reconciliation_first():
    browser = _text("backend/app/services/browser_operator.py")
    assert "def operation_requires_postcondition_reconciliation" in browser
    assert "operation.side_effect_step is not None" in browser
    assert "operation.current_step >= operation.side_effect_step" in browser
    resume = browser.split("async def resume_browser_operation", 1)[1].split("async def browser_status", 1)[0]
    assert 'operation.status == "creation_uncertain" and operation.side_effect_step is None' in resume
    assert "cannot be resumed safely" in resume
    execute = browser.split("async def execute_browser_operation", 1)[1].split(
        "def operation_requires_material_decision", 1
    )[0]
    reconcile = execute.split("operation_requires_postcondition_reconciliation(operation)", 1)[1].split(
        "username, password = await _auto_login_if_needed", 1
    )[0]
    assert "verify_page" in reconcile
    assert "_perform_step(" not in reconcile


def test_v115_release_identity_and_historical_evidence_are_preserved():
    version = _text("backend/app/core/version.py")
    pubspec = _text("android/pubspec.yaml")
    status = _text("STATUS.md")
    state = _text("VAAPP_PROJECT_STATE.json")
    handoff = _text("VAAPP_PROJECT_HANDOFF.md")

    assert 'APP_VERSION = "1.0.18"' in version
    assert 'REQUIRED_ANDROID_VERSION = "1.0.18"' in version
    assert "version: 1.0.18+61" in pubspec
    assert "v1.0.14" in status
    assert "v1.0.13" in status
    assert "v1.0.12" in status
    assert "v1.0.11" in status
    assert '"verified_baseline_actions_run": 41' in state
    assert '"verified_baseline_actions_conclusion": "success"' in state
    assert "GitHub Actions run #41" in handoff
