from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_v104_release_identity():
    assert 'APP_VERSION = "1.0.11"' in _read("backend/app/core/version.py")
    assert 'REQUIRED_ANDROID_VERSION = "1.0.11"' in _read("backend/app/core/version.py")
    assert 'version = "1.0.11"' in _read("backend/pyproject.toml")
    assert "version: 1.0.11+54" in _read("android/pubspec.yaml")
    contract = _read("android/lib/release_contract.dart")
    assert "const String appRelease = '1.0.11';" in contract
    assert "const String minimumBackendVersion = '1.0.11';" in contract
    workflow = _read(".github/workflows/android-release.yml")
    assert "Full-Time-VA-Android-v1.0.5.apk" in workflow


def test_gmail_push_requires_real_watch_readiness_not_topic_string_only():
    registry = _read("backend/app/services/capability_registry.py")
    assert "GmailMailboxState" in registry
    assert 'google_pubsub_verification_token' in registry
    assert "gmail_watch_active" in registry
    assert "watch_expiration_at" in registry
    assert "gmail_state.watch_topic == gmail_topic" in registry
    assert "gmail_push_available" in registry
    assert '"ready"' in registry
    assert "gmail_push_observed" in registry
    assert "Activate Gmail watch" in registry


def test_fulfillment_readiness_is_provider_linked():
    registry = _read("backend/app/services/capability_registry.py")
    assert "enabled_portal_ids" in registry
    assert "fulfillment_browser_executor" in registry
    assert "provider.browser_portal_id in enabled_portal_ids" in registry
    assert "fulfillment_phone_executor" in registry
    assert "provider.support_phone_encrypted" in registry
    assert "fulfillment_executor_ready" in registry
    assert '"fulfillment_automation"' in registry


def test_android_operations_exposes_setup_and_safe_gmail_activation():
    ui = _read("android/lib/screens/va_operations_page.dart")
    state = _read("android/lib/app_state.dart")
    assert "_showCapabilitySetup" in ui
    assert "_CapabilityStateBadge" in ui
    assert "What to configure" in ui
    assert "Setup location" in ui
    assert "Open Services" in ui
    assert "Activate watch" in ui
    assert "DefaultTabController.of(context).animateTo(3)" in ui
    assert "FulfillmentPage" in ui
    assert "CommunicationsPage" in ui
    assert "TelephonyPage" in ui
    assert "A configured executor is not completion evidence" in ui
    assert "activateGmailWatch" in state
    assert "api.postJson('/api/google/watch')" in state


def test_v104_setup_guidance_does_not_return_secret_values():
    registry = _read("backend/app/services/capability_registry.py")
    assert "{{backend}}/api/google/pubsub?token=<the same verification token>" in registry
    # Secret configuration is checked as a boolean prerequisite, never inserted into setup metadata.
    assert 'row["setup"] = {' in registry
    assert 'gmail_verification_token' in registry
    assert '"verification_token": gmail_verification_token' not in registry
    assert '"token": gmail_verification_token' not in registry


def test_v104_documentation_exists():
    doc = _read("docs/V1.0.4_EXECUTION_READINESS_SETUP_ASSISTANT.md")
    assert "Execution Readiness & Setup Assistant" in doc
    assert "READY" in doc
    assert "provider-specific executor relationship" in doc
    assert "never returned by the capability matrix" in doc
