from pathlib import Path


ROOT = Path(__file__).parents[2]


def test_v118_release_identity_contract() -> None:
    version = (ROOT / "backend/app/core/version.py").read_text()
    pubspec = (ROOT / "android/pubspec.yaml").read_text()
    release = (ROOT / "android/lib/release_contract.dart").read_text()
    assert 'APP_VERSION = "1.0.18"' in version
    assert 'REQUIRED_ANDROID_VERSION = "1.0.18"' in version
    assert "version: 1.0.18+61" in pubspec
    assert "appRelease = '1.0.18'" in release
    assert "minimumBackendVersion = '1.0.18'" in release


def test_archive_intent_is_unique_per_exact_bytes_and_scope() -> None:
    models = (ROOT / "backend/app/models/entities.py").read_text()
    assert "class DocumentArchiveUploadIntent" in models
    assert '"document_archive_upload_intents"' in models
    assert '"checksum_sha256", "account_scope"' in models
    assert "uq_document_archive_upload_checksum_scope" in models
    assert "observed_file_json" in models
    assert "drive_file_id" in models


def test_drive_recovery_query_uses_provider_properties() -> None:
    google = (ROOT / "backend/app/integrations/google_api.py").read_text()
    assert "async def find_drive_files_by_app_properties" in google
    assert "appProperties has" in google
    assert '"nextPageToken,files("' in google
    assert "createdTime" in google
    assert "_execute_google_request" in google


def test_ingestion_routes_drive_create_through_durable_recovery() -> None:
    ingestion = (ROOT / "backend/app/services/document_ingestion.py").read_text()
    assert "ensure_document_archive_upload" in ingestion
    assert "find_drive_files_by_app_properties" in ingestion
    assert "upload_file=upload_drive_file" in ingestion
    assert "find_files=find_drive_files_by_app_properties" in ingestion
    ensure_at = ingestion.index("uploaded = await ensure_document_archive_upload")
    record_at = ingestion.index("record = DocumentRecord(", ensure_at)
    assert ensure_at < record_at


def test_ambiguous_archive_state_is_reconciliation_only() -> None:
    recovery = (ROOT / "backend/app/services/document_archive_recovery.py").read_text()
    assert 'intent.status in {"submitting", "creation_uncertain"}' in recovery
    assert "duplicate upload is suppressed" in recovery
    assert "automatic upload replay is disabled" in recovery
    assert "await reconcile_document_archive_upload" in recovery
    assert "prepared -> submitting" in recovery
    assert "No elapsed time or retry counter authorizes another" in recovery


def test_legacy_exact_byte_contract_includes_drive_reconciliation_stub() -> None:
    legacy = (ROOT / "backend/tests/test_portal_document_sync.py").read_text()
    assert "async def fake_find" in legacy
    assert "find_drive_files_by_app_properties" in legacy
    assert 'assert uploads == ["contract.txt"]' in legacy
