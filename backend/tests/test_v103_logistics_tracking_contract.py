from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_v103_release_identity_and_routes():
    assert 'APP_VERSION = "1.0.12"' in _read("backend/app/core/version.py")
    assert 'REQUIRED_ANDROID_VERSION = "1.0.12"' in _read("backend/app/core/version.py")
    assert 'version = "1.0.12"' in _read("backend/pyproject.toml")
    assert "version: 1.0.12+55" in _read("android/pubspec.yaml")
    workflow = _read(".github/workflows/android-release.yml")
    assert "Full-Time-VA-Android-v1.0.5.apk" in workflow
    routes = _read("backend/app/api/fulfillment_routes.py")
    assert '/api/fulfillment/provider-templates' in routes


def test_tracking_observation_is_additive_and_durable():
    models = _read("backend/app/models/fulfillment_entities.py")
    assert 'class FulfillmentObservation(Base):' in models
    assert '__tablename__ = "fulfillment_observations"' in models
    assert 'observation_key' in models
    assert 'terminal:' in models
    assert 'stalled:' in models
    assert 'details_encrypted' in models


def test_secure_browser_separates_observation_from_postcondition():
    browser = _read("backend/app/services/browser_operator.py")
    assert 'observe_text_any' in browser
    assert 'settle_ms' in browser
    assert 'Observation matches are evidence only' in browser
    assert 'results["observe_text_any"] = observed' in browser
    assert 'term_hashes' in browser


def test_logistics_does_not_complete_on_page_navigation_or_in_transit():
    service = _read("backend/app/services/fulfillment_service.py")
    assert 'async def _reconcile_tracking_browser_action' in service
    assert 'if state == "delivered":' in service
    assert 'request.status = "waiting_provider"' in service
    assert 'request.next_action_at = _tracking_recheck_at(state, config)' in service
    assert 'if state == "available_for_pickup":' in service
    assert 'physical pickup' in service
    assert 'async def _tracking_browser_failure' in service
    assert 'retry_minutes = min(720' in service
    assert 'tracking_state_observed' in service


def test_bpost_template_is_source_backed_and_editable():
    service = _read("backend/app/services/fulfillment_service.py")
    assert '"key": "bpost_track_trace"' in service
    assert 'https://track.bpost.cloud/btr/web/' in service
    assert '"url": "{{tracking_url}}"' in service
    assert '"mode": "observe"' in service
    assert '"delivered"' in service
    assert '"available_for_pickup"' in service
    ui = _read("android/lib/screens/fulfillment_page.dart")
    assert 'Starter template (optional)' in ui
    assert '/api/fulfillment/provider-templates' in ui
    assert 'Tracking:' in ui
    assert 'next check' in ui
    assert 'Tap to edit' in ui


def test_v103_documentation_exists():
    doc = _read("docs/V1.0.3_LOGISTICS_TRACKING_OWNERSHIP.md")
    assert 'provider-page verification' in doc
    assert 'available_for_pickup' in doc
    assert 'track.bpost.cloud' in doc
    assert 'Navigation' in doc
