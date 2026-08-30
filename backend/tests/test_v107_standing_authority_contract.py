from pathlib import Path

ROOT = Path(__file__).parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_v107_release_identity_and_authority_routes():
    assert 'APP_VERSION = "1.0.9"' in read("backend/app/core/version.py")
    assert 'REQUIRED_ANDROID_VERSION = "1.0.9"' in read("backend/app/core/version.py")
    assert 'version = "1.0.9"' in read("backend/pyproject.toml")
    assert "version: 1.0.9+52" in read("android/pubspec.yaml")
    contract = read("android/lib/release_contract.dart")
    assert "appRelease = '1.0.9'" in contract
    assert "minimumBackendVersion = '1.0.9'" in contract

    routes = read("backend/app/api/routes.py")
    authority_routes = read("backend/app/api/v107_routes.py")
    main = read("backend/app/main.py")
    assert '"standing_authority"' in routes
    assert '"risk_bounded_delegation"' in routes
    assert '"/api/va/authorities"' in authority_routes
    assert '"/api/va/authorities/{policy_key}"' in authority_routes
    assert "v107_router" in main


def test_standing_authority_uses_existing_explicit_preference_store_and_audit():
    service = read("backend/app/services/standing_authority.py")
    assert 'domain=_DOMAIN' in service
    assert '_DOMAIN = "standing_authority"' in service
    assert 'source="explicit"' in service
    assert '"va_standing_authority_used"' in service
    assert '"va_standing_authority_updated"' in service
    assert '"critical"' in service
    assert '"otp"' in service
    assert '"passport"' in service
    assert '"sign contract"' in service
    assert '"bank_authorization"' in service
    assert '"calendar_coordination"' not in service  # ordinary calendar work is already autonomous


def test_specific_authorization_auto_resumes_only_when_policy_covers_exact_proposal():
    service = read("backend/app/services/specific_authorization.py")
    core = read("backend/app/services/autonomous_core.py")
    assert "apply_standing_authority_objectives" in service
    assert "evaluate_standing_authority" in service
    assert "record_standing_authority_use" in service
    assert '"standing_authority"' in service
    assert "apply_standing_authority_objectives" in core


def test_browser_material_commitments_can_use_only_bounded_non_human_authority():
    policy = read("backend/app/services/va_policy.py")
    assert "evaluate_standing_authority" in policy
    assert "browser_transactions" in read("backend/app/services/standing_authority.py")
    assert "material_approved_at" in policy
    assert "record_standing_authority_use" in policy


def test_android_exposes_explicit_delegation_controls_without_hiding_hard_boundaries():
    state = read("android/lib/app_state.dart")
    page = read("android/lib/screens/va_operations_page.dart")
    assert "vaAuthorities" in state
    assert "updateVaAuthority" in state
    assert "Standing authority" in page
    assert "Hard human boundaries always stay with you" in page
    assert "Bounded portal transactions" in page
