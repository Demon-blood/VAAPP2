from pathlib import Path


ROOT = Path(__file__).parents[2]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_v100_release_identity_is_consistent() -> None:
    version = _read("backend/app/core/version.py")
    assert 'APP_VERSION = "1.0.3"' in version
    assert 'REQUIRED_ANDROID_VERSION = "1.0.3"' in version
    assert 'version = "1.0.3"' in _read("backend/pyproject.toml")
    assert "version: 1.0.3+45" in _read("android/pubspec.yaml")
    workflow = _read(".github/workflows/android-release.yml")
    assert "Full-Time-VA-Android-v1.0.3.apk" in workflow
    assert "Full-Time VA Android v1.0.3" in workflow


def test_android_uses_one_runtime_release_contract() -> None:
    contract = _read("android/lib/release_contract.dart")
    assert "const String appRelease = '1.0.3';" in contract
    assert "const String minimumBackendVersion = '1.0.3';" in contract

    state = _read("android/lib/app_state.dart")
    assert "import 'release_contract.dart';" in state
    assert "_versionAtLeast(backendVersion, minimumBackendVersion)" in state
    assert "App $appRelease requires backend $minimumBackendVersion or newer" in state

    status = _read("android/lib/screens/product_status_page.dart")
    assert "import '../release_contract.dart';" in status
    assert "_versionAtLeast(backendVersion, minimumBackendVersion)" in status
    assert "Android ${appRelease}" in status


def test_phone_deployment_requires_v100_backend_and_has_no_legacy_floor() -> None:
    deploy = _read("android/lib/services/mobile_deployment_service.dart")
    onboarding = _read("android/lib/screens/onboarding_page.dart")
    phone_docs = _read("docs/PHONE_ONLY_SETUP.md")

    assert "String requiredVersion = minimumBackendVersion" in deploy
    assert "requiredVersion: minimumBackendVersion" in onboarding
    assert "Verifying backend $minimumBackendVersion" in onboarding
    assert "backend 1.0.3 or newer" in phone_docs

    # The historical floor caused a real release-readiness defect. Keep it out of
    # executable Android setup/repair code so future version bumps have one source.
    assert "0.4.16" not in deploy
    assert "0.4.16" not in onboarding


def test_product_status_is_operational_not_synthetic_success() -> None:
    page = _read("android/lib/screens/product_status_page.dart")
    shell = _read("android/lib/screens/home_shell.dart")
    capabilities = _read("backend/app/services/capability_registry.py")

    assert "ProductStatusPage" in shell
    assert "tooltip: 'Product status'" in shell
    assert "Verified executors" in page
    assert "Setup gaps" in page
    assert "v1.0 completion contract" in page
    assert "independent provider/source evidence" in page
    assert "endpointErrors" in page
    assert "needsYou" in page
    assert '"telephony_call"' in capabilities
    assert '"fulfillment_automation"' in capabilities


def test_system_info_reports_v100_compatibility_and_real_shipped_domains() -> None:
    routes = _read("backend/app/api/routes.py")
    assert '"required_android_version": REQUIRED_ANDROID_VERSION' in routes
    assert '"secure_browser_portal_operator"' in routes
    assert '"document_forms_deadlines"' in routes
    assert '"financial_allocation_forecasting"' in routes
    assert '"telephony_calls"' in routes
    assert '"fulfillment_automation"' in routes


def test_v100_release_metadata_preserves_the_verified_v1_baseline() -> None:
    manifest = _read("MANIFEST.json")
    state = _read("VAAPP_PROJECT_STATE.json")
    handoff = _read("VAAPP_PROJECT_HANDOFF.md")
    release_doc = _read("docs/V1.0.0_PRODUCT_RELEASE.md")

    assert '"release": "1.0.3"' in manifest
    assert '"android_version": "1.0.3+45"' in manifest
    assert '"baseline_commit": "66c09040326ac553a1402cd06fa6771344195d45"' in manifest
    assert '"verified_baseline_actions_run": 41' in state
    assert '"verified_baseline_actions_conclusion": "success"' in state
    assert "GitHub Actions run #41" in handoff
    assert "provider/source postcondition evidence" in release_doc
