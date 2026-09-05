from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_v105_release_identity():
    assert 'APP_VERSION = "1.0.16"' in _read("backend/app/core/version.py")
    assert 'REQUIRED_ANDROID_VERSION = "1.0.16"' in _read("backend/app/core/version.py")
    assert 'version = "1.0.16"' in _read("backend/pyproject.toml")
    assert "version: 1.0.16+59" in _read("android/pubspec.yaml")
    contract = _read("android/lib/release_contract.dart")
    assert "const String appRelease = '1.0.16';" in contract
    assert "const String minimumBackendVersion = '1.0.16';" in contract
    workflow = _read(".github/workflows/android-release.yml")
    assert "Full-Time-VA-Android-v1.0.5.apk" in workflow


def test_specific_authorization_is_objective_bound_and_not_completion_evidence():
    service = _read("backend/app/services/specific_authorization.py")
    routes = _read("backend/app/api/v105_routes.py")
    assert "action_fingerprint" in service
    assert "va_specific_authorization_granted" in service
    assert "va_specific_authorization_declined" in service
    assert "specific_objective_authorization" in service
    assert "VAOutcomeEvidence" not in service
    assert '/api/va/objectives/{objective_id}/authorize' in routes
    assert '/api/va/objectives/{objective_id}/decline' in routes
    assert "run_core_cycle" in routes


def test_communication_correlation_fixes_source_ownership_not_just_ui_duplicates():
    correlation = _read("backend/app/services/communication_correlation.py")
    core = _read("backend/app/services/autonomous_core.py")
    assert "communication_task_projection_superseded" in correlation
    assert "communication_cross_transport_duplicate_superseded" in correlation
    assert "com.google.android.apps.messaging" in correlation
    assert "com.samsung.android.messaging" in correlation
    assert "Same-channel repeats are never collapsed" in correlation
    assert 'if task.source_type == "communication":' in core
    assert "repair_communication_correlation" in core


def test_relationship_preferences_reuse_phase4_provenance_and_cannot_grant_material_authority():
    preferences = _read("backend/app/services/relationship_preferences.py")
    policy = _read("backend/app/services/autonomy_policy.py")
    assert "RelationshipFact" in preferences
    assert 'FACT_KEY = "communication_preferences"' in preferences
    assert 'SOURCE_TYPE = "user_explicit"' in preferences
    assert "routine_auto_send" in preferences
    assert "approval_topics" in preferences
    assert "channel_aliases" in preferences
    assert "Ambiguous duplicate aliases" in preferences
    assert "payment" in preferences and "authority" in preferences
    assert "relationship_pref_requires_review" in preferences
    assert "relationship_preferences" in policy
    assert "relationship_reply_review_reason" in policy
    assert "learn_from_history" in preferences
    assert '"partner"' in preferences


def test_learned_relationship_style_uses_verified_user_sent_history_without_self_training():
    learning = _read("backend/app/services/relationship_style_learning.py")
    routes = _read("backend/app/api/v105_routes.py")
    assert 'FACT_KEY = "learned_communication_style"' in learning
    assert 'SOURCE_TYPE = "verified_user_outbound_aggregate"' in learning
    assert '"android_sms_history"' in learning
    assert 'CommunicationEvent.direction == "outgoing"' in learning
    assert "_looks_va_generated_history" in learning
    assert "CommunicationAction.status.in_" in learning
    assert "event.protected" in learning
    assert "representative_examples" in learning
    assert "MIN_SAMPLES = 3" in learning
    assert '/api/relationships/{relationship_id}/communication-style/relearn' in routes
    assert "learn_from_history" in routes


def test_relationship_preferences_reach_email_and_device_ai_context():
    ai = _read("backend/app/integrations/ai_client.py")
    email = _read("backend/app/services/email_processor.py")
    communications = _read("backend/app/services/communications_service.py")
    assert "relationship_reply_preferences" in ai
    assert "relationship_reply_preferences" in email
    assert "preference_digest" in email
    assert "relationship_reply_context_for_party" in email
    assert "relationship_reply_preferences" in communications
    assert "relationship_reply_context_for_party" in communications
    assert "relationship_review_required" in communications
    assert "channel=payload.channel" in communications
    assert "provider=payload.provider" in communications


def test_android_exposes_real_authorize_decline_and_editable_relationship_preferences():
    operations = _read("android/lib/screens/va_operations_page.dart")
    state = _read("android/lib/app_state.dart")
    work = _read("android/lib/screens/work_page.dart")
    preferences = _read("android/lib/screens/relationship_preferences_page.dart")
    assert "Authorize" in operations
    assert "Decline" in operations
    assert "Open provider authorization" in operations
    assert "Recheck after authorization" in operations
    assert "action_fingerprint" in operations
    assert "authorizeVaObjective" in state
    assert "declineVaObjective" in state
    assert "relationshipCommunicationPreferences" in state
    assert "updateRelationshipCommunicationPreferences" in state
    assert "relearnRelationshipCommunicationStyle" in state
    assert "RelationshipPreferencesPage" in work
    assert "Routine replies" in preferences
    assert "Topics that always require approval" in preferences
    assert "Messaging-app identity links" in preferences
    assert "Learn how I write to this person" in preferences
    assert "Relearn from verified sent messages" in preferences
    assert "WhatsApp displayed name(s)" in preferences
    assert "Messenger displayed name(s)" in preferences
    assert "do not grant payment" in preferences
