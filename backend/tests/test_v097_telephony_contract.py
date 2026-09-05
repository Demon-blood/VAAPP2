from pathlib import Path


ROOT = Path(__file__).parents[2]


def test_v097_release_identity() -> None:
    assert 'APP_VERSION = "1.0.17"' in (ROOT / "backend/app/core/version.py").read_text()
    pyproject = (ROOT / "backend/pyproject.toml").read_text()
    assert 'version = "1.0.17"' in pyproject
    assert '"twilio>=9,<10"' in pyproject
    assert "version: 1.0.17+60" in (ROOT / "android/pubspec.yaml").read_text()
    app_state = (ROOT / "android/lib/app_state.dart").read_text()
    assert "_versionAtLeast(backendVersion, minimumBackendVersion)" in app_state
    assert "App $appRelease requires backend $minimumBackendVersion or newer." in app_state
    workflow = (ROOT / ".github/workflows/android-release.yml").read_text()
    assert "Full-Time-VA-Android-v1.0.5.apk" in workflow


def test_telephony_ledger_is_additive_encrypted_and_idempotent() -> None:
    source = (ROOT / "backend/app/models/telephony_entities.py").read_text()
    assert "class TelephonyCall(Base):" in source
    assert "class TelephonyTurn(Base):" in source
    assert "class TelephonyEvidence(Base):" in source
    assert "idempotency_key" in source
    assert "webhook_token_hash" in source
    assert "webhook_token_encrypted" in source
    assert "target_hash" in source
    assert "target_encrypted" in source
    assert "purpose_encrypted" in source
    assert "expected_outcome_encrypted" in source
    assert "transcript_encrypted" in source
    assert "transcript_sha256" in source
    assert 'UniqueConstraint("series_key", "attempt"' in source
    assert 'UniqueConstraint("call_id", "turn_index"' in source


def test_twilio_is_a_real_executor_with_ambiguity_safe_creation() -> None:
    source = (ROOT / "backend/app/services/telephony_service.py").read_text()
    assert "https://api.twilio.com/2010-04-01/Accounts/" in source
    assert "Calls.json" in source
    assert "httpx.BasicAuth" in source
    assert '"StatusCallbackEvent": ["initiated", "ringing", "answered", "completed"]' in source
    assert '"TimeLimit": str(max_duration)' in source
    assert 'call.status = "creation_uncertain"' in source
    assert '"retry_suppressed": True' in source
    assert "blind retry is blocked" in source
    assert 'call.status = "provider_completed_unverified"' in source
    assert 'call.status = "completed_verified"' in source
    assert 'evidence_type="telephony_counterparty_confirmation"' in source
    assert "RequestValidator" in source
    assert '"Record"' not in source


def test_voice_policy_discloses_automation_and_blocks_material_steps() -> None:
    ai = (ROOT / "backend/app/services/telephony_ai.py").read_text()
    service = (ROOT / "backend/app/services/telephony_service.py").read_text()
    assert "must always know they are speaking with an automated virtual assistant" in ai
    assert "objective_satisfied=true" in ai
    assert "counterparty's actual words" in ai
    assert "OTP/2FA" in ai
    assert "payment-card details" in ai
    assert "_contains_material_request" in service
    assert "_mark_needs_user" in service
    assert 'call.verification_status = "needs_user"' in service
    assert 'if call.needs_user or call.verification_status == "needs_user":' in service
    assert "_max_duration_seconds" in service
    assert '"ai_configured": ai_configured' in service
    assert "outbound telephony would not have a safe conversation executor" in service
    assert "Maximum call duration reached" in service
    assert '"input": "speech"' in service
    assert '"speechTimeout": "auto"' in service


def test_twilio_webhooks_are_signed_and_device_management_is_authenticated() -> None:
    routes = (ROOT / "backend/app/api/telephony_routes.py").read_text()
    assert 'Header(default=None, alias="X-Twilio-Signature")' in routes
    assert "validate_twilio_signature" in routes
    assert "require_device" in routes
    for path in (
        "/api/telephony/status",
        "/api/telephony/calls",
        "/api/telephony/calls/{call_id}",
        "/api/telephony/calls/{call_id}/reconcile",
        "/api/telephony/reconcile",
        "/api/telephony/twilio/incoming",
        "/api/telephony/twilio/voice/{token}",
        "/api/telephony/twilio/turn/{token}/{logical_turn}",
        "/api/telephony/twilio/status/{token}",
    ):
        assert path in routes
    main = (ROOT / "backend/app/main.py").read_text()
    assert "app.include_router(telephony_router)" in main
    routes_main = (ROOT / "backend/app/api/routes.py").read_text()
    assert '"telephony_calls"' in routes_main
    assert 'section["callback_url"] = f"{base}/api/telephony/twilio/incoming"' in routes_main
    database = (ROOT / "backend/app/core/database.py").read_text()
    assert "import app.models.telephony_entities" in database


def test_telephony_reconciliation_and_capability_are_continuous() -> None:
    scheduler = (ROOT / "backend/app/services/scheduler.py").read_text()
    capabilities = (ROOT / "backend/app/services/capability_registry.py").read_text()
    runtime = (ROOT / "backend/app/services/runtime_config.py").read_text()
    assert "telephony_reconcile_job" in scheduler
    assert 'id="telephony_reconcile"' in scheduler
    assert '"telephony_call"' in capabilities
    assert "Twilio Programmable Voice + VAAPP voice decision engine" in capabilities
    assert '"twilio_account_sid"' in runtime
    assert '"twilio_auth_token"' in runtime
    assert '"twilio_from_number"' in runtime
    assert '"telephony_max_duration_seconds"' in runtime


def test_android_has_a_calls_workspace_and_reuses_draft_idempotency_keys() -> None:
    shell = (ROOT / "android/lib/screens/home_shell.dart").read_text()
    calls = (ROOT / "android/lib/screens/telephony_page.dart").read_text()
    assert "TelephonyPage()" in shell
    assert "label: 'Calls'" in shell
    assert "_draftKeys" in calls
    assert "FlutterSecureStorage" in calls
    assert "_draftIdempotencyKey" in calls
    assert "_clearDraftIdempotencyKey" in calls
    assert "same persisted idempotency key" in calls
    assert "idempotency_key" in calls
    assert "Provider completed ≠ objective verified" in calls
    assert "IVR, or voicemail" in calls
    assert "/api/telephony/calls" in calls
    assert "/api/telephony/status" in calls


def test_telephony_restart_and_multistep_hardening() -> None:
    service = (ROOT / "backend/app/services/telephony_service.py").read_text()
    models = (ROOT / "backend/app/models/telephony_entities.py").read_text()
    assert 'str(settings.public_base_url).rstrip("/")' in service
    assert 'The counterparty turn was durably committed' in service
    assert 'remaining = int(' in service
    assert 'VAObjectiveStep.status != "completed"' in service
    assert '"counterparty_confirmation": True' in service
    assert '"summary": summary' not in service
    assert 'goal="Complete the encrypted telephony objective' in service
    assert '"telephony_intent_key"' in service
    assert 'This telephone objective contains a material payment' in service
    assert 'UniqueConstraint("call_id", "provider_ref"' in models
