from pathlib import Path

ROOT = Path(__file__).parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_v108_release_identity_and_operational_guardian_contract():
    assert 'APP_VERSION = "1.0.17"' in read("backend/app/core/version.py")
    assert 'REQUIRED_ANDROID_VERSION = "1.0.17"' in read("backend/app/core/version.py")
    assert 'version = "1.0.17"' in read("backend/pyproject.toml")
    assert "version: 1.0.17+60" in read("android/pubspec.yaml")
    release = read("android/lib/release_contract.dart")
    assert "appRelease = '1.0.17'" in release
    assert "minimumBackendVersion = '1.0.17'" in release

    service = read("backend/app/services/operational_guardian.py")
    assert 'source_type="operational_guardian"' in service
    assert 'status="needs_user"' in service
    assert 'evidence_type="provider_state"' in service
    assert "refresh_token_encrypted" in service
    assert "operational_guardian:" in service
    assert "Never manufacture a second copy" in service

    scheduler = read("backend/app/services/scheduler.py")
    assert "operational_guardian_job" in scheduler
    assert 'id="operational_guardian"' in scheduler

    autopilot = read("backend/app/api_autopilot.py")
    assert "operational_guardian_status" in autopilot
    assert '"reliability": reliability' in autopilot

    routes = read("backend/app/api/routes.py")
    assert '"operational_guardian"' in routes
    assert '"consent_continuity"' in routes

    page = read("android/lib/screens/va_operations_page.dart")
    assert "Operational continuity" in page
    assert "Consent continuity" in page
    assert "System recovery stays VA-owned" in page
