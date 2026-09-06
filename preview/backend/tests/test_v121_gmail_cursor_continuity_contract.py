from pathlib import Path

ROOT = Path(__file__).parents[2]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_v121_release_identity_contract() -> None:
    version = _read("backend/app/core/version.py")
    pubspec = _read("android/pubspec.yaml")
    release = _read("android/lib/release_contract.dart")
    assert 'APP_VERSION = "1.0.21"' in version
    assert 'REQUIRED_ANDROID_VERSION = "1.0.21"' in version
    assert 'version = "1.0.21"' in _read("backend/pyproject.toml")
    assert "version: 1.0.21+64" in pubspec
    assert "appRelease = '1.0.21'" in release
    assert "minimumBackendVersion = '1.0.21'" in release


def test_watch_renewal_cannot_advance_processing_history() -> None:
    source = _read("backend/app/services/gmail_sync_service.py")
    start = source.index("async def ensure_gmail_watch")
    end = source.index("async def full_recovery_sync", start)
    block = source[start:end]
    assert "watch_history_id" in block
    assert '"processing_history_id": row.history_id' in block
    assert "row.history_id = history_id" not in block


def test_full_recovery_brackets_scan_with_history_catchup() -> None:
    source = _read("backend/app/services/gmail_sync_service.py")
    start = source.index("async def full_recovery_sync")
    end = source.index("async def history_sync", start)
    block = source[start:end]
    profile_at = block.index("await get_gmail_profile")
    scan_at = block.index("await sync_gmail")
    checkpoint_at = block.index("row.history_id = bootstrap_history_id")
    catchup_at = block.index("await history_sync")
    assert profile_at < scan_at < checkpoint_at < catchup_at
    assert '"bootstrap_processed"' in block
    assert '"catchup_processed"' in block


def test_periodic_gmail_sync_uses_durable_history_cursor_when_available() -> None:
    source = _read("backend/app/services/workflow_engine.py")
    start = source.index('@job_handler("gmail.sync")')
    end = source.index('@job_handler("gmail.history.sync")', start)
    block = source[start:end]
    assert "mailbox_state" in block
    assert "if state.history_id:" in block
    assert "return await history_sync(db)" in block
    assert "refresh_mailbox_cursor_from_profile" not in block


def test_history_pagination_cannot_silently_advance_on_truncation() -> None:
    source = _read("backend/app/integrations/google_api.py")
    start = source.index("async def list_gmail_history_added_message_ids")
    end = source.index("def _calendar_rfc3339", start)
    block = source[start:end]
    assert "max_pages: int = 1000" in block
    assert "Gmail history pagination limit reached before cursor exhaustion" in block
    assert "if not page_token:" in block


def test_watch_transport_runs_off_event_loop_without_blind_inner_retry() -> None:
    source = _read("backend/app/integrations/google_api.py")
    start = source.index("async def start_gmail_watch")
    end = source.index("async def drive_service", start)
    block = source[start:end]
    assert "_execute_google_request" in block
    assert "attempts=1" in block
    assert "retry_statuses=set()" in block


def test_verified_v1_baseline_metadata_remains_immutable() -> None:
    state = _read("VAAPP_PROJECT_STATE.json")
    handoff = _read("VAAPP_PROJECT_HANDOFF.md")
    assert '"verified_baseline_actions_run": 41' in state
    assert '"verified_baseline_actions_conclusion": "success"' in state
    assert "GitHub Actions run #41" in handoff
