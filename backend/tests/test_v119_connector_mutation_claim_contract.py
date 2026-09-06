from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_v119_release_identity_contract() -> None:
    version = (ROOT / "backend/app/core/version.py").read_text()
    pubspec = (ROOT / "android/pubspec.yaml").read_text()
    release = (ROOT / "android/lib/release_contract.dart").read_text()
    assert 'APP_VERSION = "1.0.19"' in version
    assert 'REQUIRED_ANDROID_VERSION = "1.0.19"' in version
    assert "version: 1.0.19+62" in pubspec
    assert "appRelease = '1.0.19'" in release
    assert "minimumBackendVersion = '1.0.19'" in release


def test_scheduled_connector_mutation_has_durable_occurrence_ledger() -> None:
    models = (ROOT / "backend/app/models/entities.py").read_text()
    assert "class ScheduledConnectorMutationIntent" in models
    assert '"scheduled_connector_mutation_intents"' in models
    assert "automation_rule_id" in models
    assert "occurrence_key" in models
    assert "request_fingerprint" in models
    assert "uq_scheduled_connector_mutation_rule_occurrence" in models


def test_automation_claims_mutation_before_provider_dispatch() -> None:
    automation = (ROOT / "backend/app/services/automation_engine.py").read_text()
    claim_at = automation.index("await claim_scheduled_connector_mutation")
    execute_at = automation.index("result = await execute_connector", claim_at)
    assert claim_at < execute_at
    assert "connector_operation_is_mutating" in automation
    assert "mark_scheduled_connector_mutation_uncertain" in automation
    assert "scheduled_connector_mutation_replay_suppressed" in automation
    assert "scheduled_connector_mutation_uncertain" in automation
    assert "automatic_replay" in automation


def test_ambiguous_mutation_never_enters_normal_transient_rethrow_branch() -> None:
    automation = (ROOT / "backend/app/services/automation_engine.py").read_text()
    mutation_start = automation.index("if is_mutating:")
    read_start = automation.index("# Read-only connector operations", mutation_start)
    mutation_block = automation[mutation_start:read_start]
    assert "failure_recovery_class" not in mutation_block
    assert "_ensure_rule_exception_task" not in mutation_block
    assert "continue" in mutation_block
    assert "execution_uncertain" in mutation_block


def test_legacy_connector_retry_backlog_is_fail_closed() -> None:
    workflow = (ROOT / "backend/app/services/workflow_engine.py").read_text()
    main = (ROOT / "backend/app/main.py").read_text()
    assert "async def repair_v119_connector_rule_retry_backlog" in workflow
    assert 'WorkflowJob.job_type == "connectors.rules.run"' in workflow
    assert 'WorkflowJob.status.in_(["running", "retry", "dead_letter"])' in workflow
    assert '"v119_connector_rule_retry_backlog_repaired"' in workflow
    assert "repair_v119_connector_rule_retry_backlog" in main
    repair_at = main.index("await repair_v119_connector_rule_retry_backlog")
    scheduler_at = main.index("start_scheduler()")
    assert repair_at < scheduler_at


def test_minute_scheduler_remains_fresh_occurrence_source() -> None:
    scheduler = (ROOT / "backend/app/services/scheduler.py").read_text()
    assert 'job_type="connectors.rules.run"' in scheduler
    assert '_bucket_key("connectors.rules.run", 1)' in scheduler
