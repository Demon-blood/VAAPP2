from pathlib import Path


def test_autopilot_briefing_uses_daily_intelligence_service() -> None:
    root = Path(__file__).parents[2]
    api = (root / "backend" / "app" / "api_autopilot.py").read_text()
    assert "from app.services.briefing_service import daily_briefing" in api
    assert '@router.get("/briefing")' in api
    workflow = (root / "backend" / "app" / "services" / "workflow_engine.py").read_text()
    assert "from app.services.briefing_service import daily_briefing" in workflow


def test_daily_briefing_has_zero_input_defaults() -> None:
    root = Path(__file__).parents[2]
    config = (root / "backend" / "app" / "services" / "runtime_config.py").read_text()
    assert '"daily_briefing_enabled"' in config
    assert '"daily_briefing_hour_local"' in config
    assert '"default": "19"' in config
    assert '"daily_briefing_window_hours"' in config


def test_release_version_is_v072() -> None:
    root = Path(__file__).parents[2]
    version = (root / "backend" / "app" / "core" / "version.py").read_text()
    pubspec = (root / "android" / "pubspec.yaml").read_text()
    assert 'APP_VERSION = "0.9.5"' in version
    assert "version: 0.9.5+38" in pubspec


def test_daily_intelligence_covers_full_exception_only_briefing_contract() -> None:
    root = Path(__file__).parents[2]
    source = (root / "backend" / "app" / "services" / "briefing_service.py").read_text()
    for key in (
        '"reply_activity"',
        '"task_activity"',
        '"important_documents"',
        '"unusual_items"',
        '"provider_problems"',
        '"payment_activity"',
        '"bill_activity"',
        '"calendar_changes"',
        '"communications"',
        '"internal_transfers"',
        '"needs_you"',
    ):
        assert key in source
