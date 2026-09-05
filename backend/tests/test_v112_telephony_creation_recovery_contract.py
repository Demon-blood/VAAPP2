from pathlib import Path

ROOT = Path(__file__).parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_v112_release_identity_is_consistent():
    assert 'APP_VERSION = "1.0.18"' in read("backend/app/core/version.py")
    assert 'REQUIRED_ANDROID_VERSION = "1.0.18"' in read("backend/app/core/version.py")
    assert 'version = "1.0.18"' in read("backend/pyproject.toml")
    assert "version: 1.0.18+61" in read("android/pubspec.yaml")
    release = read("android/lib/release_contract.dart")
    assert "appRelease = '1.0.18'" in release
    assert "minimumBackendVersion = '1.0.18'" in release


def test_uncertain_call_creation_is_recovered_from_unique_provider_evidence_only():
    source = read("backend/app/services/telephony_service.py")
    assert "def _twilio_provider_timestamp" in source
    assert "async def _twilio_creation_candidates" in source
    assert "async def _recover_uncertain_call_creation" in source
    assert '"To": target' in source
    assert '"From": from_number' in source
    assert 'raw.get("direction") or ""' in source
    assert '!= "outbound-api"' in source
    assert "abs((observed_at - reference).total_seconds()) > 10 * 60" in source
    assert "len(candidates) != 1" in source
    assert "await _bind_sid(db, call, sid)" in source
    assert 'event_type="provider_create_recovered"' in source


def test_zero_or_multiple_matches_never_become_a_blind_redial():
    source = read("backend/app/services/telephony_service.py")
    recovery = source.split("async def _recover_uncertain_call_creation", 1)[1].split(
        "async def reconcile_call", 1
    )[0]
    assert 'call.status = "creation_uncertain"' in recovery
    assert "blind redial remains blocked" in recovery
    assert "Multiple Twilio calls match" in recovery
    assert "No unique Twilio call can yet prove" in recovery
    assert "_dispatch_outbound_call" not in recovery
    assert "_create_retry_call" not in recovery
    assert "_mark_needs_user" not in recovery


def test_reconciliation_searches_uncertain_calls_without_sid_and_retry_requires_terminal_provider_identity():
    source = read("backend/app/services/telephony_service.py")
    reconcile_call = source.split("async def reconcile_call", 1)[1].split(
        "async def _create_retry_call", 1
    )[0]
    assert 'call.status == "creation_uncertain"' in reconcile_call
    assert "await _recover_uncertain_call_creation(db, call)" in reconcile_call

    retry = source.split("async def _create_retry_call", 1)[1].split(
        "async def reconcile_telephony", 1
    )[0]
    assert "if not parent.external_call_sid or parent.provider_status not in PROVIDER_TERMINAL:" in retry
    assert "parent.next_retry_at = None" not in retry.split(
        "if not parent.external_call_sid or parent.provider_status not in PROVIDER_TERMINAL:", 1
    )[1].split("if next_attempt", 1)[0]

    scheduler = source.split("async def reconcile_telephony", 1)[1].split(
        "async def list_calls", 1
    )[0]
    assert 'TelephonyCall.status == "creation_uncertain"' in scheduler
    assert "TelephonyCall.external_call_sid.is_(None)" in scheduler
    assert "TelephonyCall.external_call_sid.is_not(None)" in scheduler
    assert "TelephonyCall.provider_status.in_(PROVIDER_TERMINAL)" in scheduler
    assert '"creation_recovered"' in scheduler
    assert '"creation_unresolved"' in scheduler


def test_legacy_telephony_safety_and_original_v1_release_evidence_are_preserved():
    telephony = read("backend/app/services/telephony_service.py")
    assert 'call.status = "creation_uncertain"' in telephony
    assert '"retry_suppressed": True' in telephony
    assert "blind retry is blocked" in telephony
    assert "RequestValidator" in telephony
    assert '"Record"' not in telephony
    assert "_mark_needs_user" in telephony

    state = read("VAAPP_PROJECT_STATE.json")
    handoff = read("VAAPP_PROJECT_HANDOFF.md")
    assert '"verified_baseline_actions_run": 41' in state
    assert '"verified_baseline_actions_conclusion": "success"' in state
    assert "GitHub Actions run #41" in handoff
    assert "v1.0.12" in read("STATUS.md")
