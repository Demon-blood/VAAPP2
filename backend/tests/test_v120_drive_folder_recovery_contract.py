from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_v120_release_identity_contract() -> None:
    version = (ROOT / "backend/app/core/version.py").read_text()
    pubspec = (ROOT / "android/pubspec.yaml").read_text()
    release = (ROOT / "android/lib/release_contract.dart").read_text()
    assert 'APP_VERSION = "1.0.20"' in version
    assert 'REQUIRED_ANDROID_VERSION = "1.0.20"' in version
    assert "version: 1.0.20+63" in pubspec
    assert "appRelease = '1.0.20'" in release
    assert "minimumBackendVersion = '1.0.20'" in release


def test_drive_folder_intent_is_additive_and_path_keyed() -> None:
    models = (ROOT / "backend/app/models/entities.py").read_text()
    assert "class DriveArchiveFolderIntent" in models
    assert '"drive_archive_folder_intents"' in models
    assert "path_key" in models
    assert "parent_path_key" in models
    assert "drive_folder_id" in models
    assert "uq_drive_archive_folder_path_key" in models


def test_google_folder_create_is_one_shot_and_reconcilable() -> None:
    google = (ROOT / "backend/app/integrations/google_api.py").read_text()
    assert "async def find_drive_folder_candidates" in google
    assert "async def create_drive_folder_once" in google
    assert '"va_folder_path_key"' in google
    assert '"va_managed_folder"' in google
    create_start = google.index("async def create_drive_folder_once")
    upload_start = google.index("async def upload_drive_file", create_start)
    create_block = google[create_start:upload_start]
    assert "_execute_google_request" not in create_block
    assert ".files().create(" in create_block


def test_archive_resolves_folder_path_before_file_claim() -> None:
    recovery = (ROOT / "backend/app/services/document_archive_recovery.py").read_text()
    folder_at = recovery.index("await ensure_drive_archive_folder_path")
    claim_at = recovery.index("claimed = await _claim_fresh_upload", folder_at)
    upload_at = recovery.index("uploaded = await upload_file", claim_at)
    assert folder_at < claim_at < upload_at
    assert 'parent_id=resolved_parent_id' in recovery
    assert 'folder_path=[]' in recovery
    assert "Drive folder setup remains reconciliation-owned" in recovery


def test_legacy_v118_tests_stub_the_new_folder_stage() -> None:
    v118 = (ROOT / "backend/tests/test_v118_drive_archive_recovery.py").read_text()
    portal = (ROOT / "backend/tests/test_portal_document_sync.py").read_text()
    marker = "ensure_drive_archive_folder_path"
    assert marker in v118
    assert marker in portal


def test_old_file_uncertainty_is_not_blindly_reopened() -> None:
    recovery = (ROOT / "backend/app/services/document_archive_recovery.py").read_text()
    assert 'if intent.status in {"submitting", "creation_uncertain"}' in recovery
    assert "Drive archive upload is reconciliation-only" in recovery
    assert "creation_uncertain" in recovery
