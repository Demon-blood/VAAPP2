from pathlib import Path


def _root() -> Path:
    return Path(__file__).resolve().parents[2]


def test_v090_release_identity_and_core_routes() -> None:
    root = _root()
    assert 'APP_VERSION = "0.9.2"' in (root / "backend/app/core/version.py").read_text()
    assert 'version = "0.9.2"' in (root / "backend/pyproject.toml").read_text()
    assert 'version: 0.9.2+35' in (root / "android/pubspec.yaml").read_text()
    workflow = (root / ".github/workflows/android-release.yml").read_text()
    assert "Full-Time-VA-Android-v0.9.2.apk" in workflow
    routes = (root / "backend/app/api/routes.py").read_text()
    for route in (
        '"/api/va/overview"',
        '"/api/va/capabilities"',
        '"/api/va/objectives"',
        '"/api/va/objectives/{objective_id}"',
        '"/api/va/objectives/{objective_id}/recheck"',
        '"/api/va/run"',
    ):
        assert route in routes


def test_v090_core_schema_has_durable_idempotent_objective_contract() -> None:
    root = _root()
    entities = (root / "backend/app/models/entities.py").read_text()
    for model in (
        "VAEvent",
        "VAObjective",
        "VAObjectiveStep",
        "VAOutcomeEvidence",
        "VAFollowUp",
        "AutonomyMetricDaily",
    ):
        assert f"class {model}(Base):" in entities
    assert '__tablename__ = "va_events"' in entities
    assert '__tablename__ = "va_objectives"' in entities
    assert '__tablename__ = "va_objective_steps"' in entities
    assert 'idempotency_key: Mapped[str]' in entities
    assert 'policy_json: Mapped[str]' in entities
    assert 'capability_json: Mapped[str]' in entities
    assert 'verification_type: Mapped[str]' in entities
    assert 'workflow_run_id: Mapped[int | None]' in entities
    assert 'user_intervention_count: Mapped[int]' in entities


def test_v090_core_uses_existing_real_workflow_engine_and_postcondition_verification() -> None:
    root = _root()
    core = (root / "backend/app/services/autonomous_core.py").read_text()
    policy = (root / "backend/app/services/va_policy.py").read_text()
    capabilities = (root / "backend/app/services/capability_registry.py").read_text()
    workflow = (root / "backend/app/services/workflow_engine.py").read_text()
    scheduler = (root / "backend/app/services/scheduler.py").read_text()

    assert "dispatch_intent" in core
    assert '"workflow_run_completed"' in core
    assert "VAOutcomeEvidence(" in core
    assert "workflow_superseded_by_completed_work" in core
    assert "failure_recovery_class" in core
    assert "recover_resolved_user_blockers" in core
    assert "requeue_dead_letter" in core
    assert 'action_type == "workflow_intent"' in policy
    assert '"workflow_engine"' in capabilities
    assert '@job_handler("va.core.cycle")' in workflow
    assert 'job_type="va.core.cycle"' in scheduler
    assert 'idempotency_key=_bucket_key("va.core.cycle", 1)' in scheduler


def test_v090_needs_you_is_not_used_for_unimplemented_system_capability() -> None:
    root = _root()
    core = (root / "backend/app/services/autonomous_core.py").read_text()
    assert 'status="blocked_capability"' in core
    assert 'status="blocked_system"' in core
    assert 'event.event_type == "payment_authorization_required"' in core
    assert 'event.event_type == "workflow_user_blocker"' in core
    assert 'event.event_type == "task_needs_decision"' in core
    assert "its domain executor has not yet been migrated" in core


def test_v090_android_has_professional_operator_view() -> None:
    root = _root()
    work = (root / "android/lib/screens/work_page.dart").read_text()
    page = (root / "android/lib/screens/va_operations_page.dart").read_text()
    state = (root / "android/lib/app_state.dart").read_text()
    assert "Tab(text: 'Operations')" in work
    assert "const VaOperationsPage()" in work
    assert "length: 9" in work
    assert "Full-Time VA operator" in page
    assert "rate == null ? '—'" in page
    assert "Needs You" in page
    assert "Only real executors and live connections are shown as available." in page
    assert "VA-owned work" in page
    assert "runAutonomousCoreNow" in state
    assert "recheckVaObjective" in state
    assert "_versionAtLeast(backendVersion, '0.9.2')" in state


def test_v090_phase1_has_no_fake_or_simulation_execution_mode() -> None:
    root = _root()
    for relative in (
        "backend/app/services/autonomous_core.py",
        "backend/app/services/capability_registry.py",
        "backend/app/services/va_policy.py",
    ):
        lowered = (root / relative).read_text().lower()
        assert "paper_mode" not in lowered
        assert "simulation_mode" not in lowered
        assert "fake_success" not in lowered
        assert "pretend_success" not in lowered
