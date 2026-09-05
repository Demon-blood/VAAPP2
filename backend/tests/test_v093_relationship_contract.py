from pathlib import Path


def _root() -> Path:
    return Path(__file__).parents[2]


def test_v093_release_identity() -> None:
    root = _root()
    assert 'APP_VERSION = "1.0.15"' in (root / 'backend/app/core/version.py').read_text()
    assert 'version = "1.0.15"' in (root / 'backend/pyproject.toml').read_text()
    assert 'version: 1.0.15+58' in (root / 'android/pubspec.yaml').read_text()
    workflow = (root / '.github/workflows/android-release.yml').read_text()
    assert 'Full-Time-VA-Android-v1.0.5.apk' in workflow


def test_relationship_memory_has_canonical_identity_and_provenance_ledgers() -> None:
    source = (_root() / 'backend/app/models/entities.py').read_text()
    for model in (
        'RelationshipMemoryState',
        'RelationshipProfile',
        'RelationshipIdentity',
        'RelationshipInteraction',
        'RelationshipFact',
    ):
        assert f'class {model}' in source
    assert 'uq_relationship_identity_global' in source
    assert 'uq_relationship_interaction_source' in source
    assert 'uq_relationship_fact_provenance' in source
    assert 'waiting_on_counterparty' in source
    assert 'next_follow_up_at' in source


def test_relationship_reconciler_uses_verified_identities_not_name_matching() -> None:
    source = (_root() / 'backend/app/services/relationship_memory.py').read_text()
    assert '_normalize_email' in source
    assert '_normalize_phone' in source
    assert 'shared_verified_identity' in source
    assert 'Google Contacts' not in source or 'google_contacts' in source
    assert 'RelationshipIdentity.identity_type == identity_type' in source
    assert 'RelationshipIdentity.normalized_value == normalized' in source
    assert 'display_name == ' not in source
    assert 'Protected {event.channel} interaction' in source
    assert 'source_type="google_contact"' in source
    assert 'source_type="calendar_event"' in source
    assert 'source_type="communication_event"' in source
    assert 'source_type="gmail_outbound"' in source


def test_relationship_memory_is_durable_background_work() -> None:
    workflow = (_root() / 'backend/app/services/workflow_engine.py').read_text()
    scheduler = (_root() / 'backend/app/services/scheduler.py').read_text()
    assert '@job_handler("relationship.reconcile")' in workflow
    assert 'reconcile_relationship_memory' in workflow
    assert 'relationship_memory_enqueue_job' in scheduler
    assert 'job_type="relationship.reconcile"' in scheduler
    assert 'id="relationship_memory_enqueue"' in scheduler


def test_relationship_api_and_android_work_surface_are_wired() -> None:
    root = _root()
    routes = (root / 'backend/app/api/routes.py').read_text()
    state = (root / 'android/lib/app_state.dart').read_text()
    work = (root / 'android/lib/screens/work_page.dart').read_text()
    for route in (
        '/api/relationships/status',
        '/api/relationships',
        '/api/relationships/{relationship_id}',
        '/api/relationships/reconcile',
    ):
        assert route in routes
    assert '"relationship_memory"' in routes
    assert "_safeGet('/api/relationships/status'" in state
    assert "_safeGet('/api/relationships?limit=250'" in state
    assert "postJson('/api/relationships/reconcile')" in state
    assert "Tab(text: 'Relationships')" in work
    assert 'Relationship memory' in work
    assert 'Verified identities' in work
    assert 'Source-backed facts' in work
    assert 'Recent interactions' in work
