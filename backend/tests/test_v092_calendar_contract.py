from pathlib import Path


def _root() -> Path:
    return Path(__file__).parents[2]


def test_v092_release_identity_and_calendar_ledger() -> None:
    root = _root()
    assert 'APP_VERSION = "0.9.5"' in (root / "backend/app/core/version.py").read_text()
    assert 'version = "0.9.5"' in (root / "backend/pyproject.toml").read_text()
    assert 'version: 0.9.5+38' in (root / "android/pubspec.yaml").read_text()
    models = (root / "backend/app/models/entities.py").read_text()
    assert "class CalendarSyncState" in models
    assert "class CalendarEventMirror" in models
    assert "class CalendarMutation" in models
    assert 'idempotency_key: Mapped[str] = mapped_column(String(255), unique=True' in models


def test_v092_calendar_execution_is_durable_idempotent_and_verified() -> None:
    root = _root()
    calendar = (root / "backend/app/services/calendar_ownership.py").read_text()
    google = (root / "backend/app/integrations/google_api.py").read_text()
    core = (root / "backend/app/services/autonomous_core.py").read_text()
    policy = (root / "backend/app/services/va_policy.py").read_text()

    assert "deterministic_calendar_event_id" in calendar
    assert "provider_event_id = provider_event_id or deterministic_calendar_event_id" in calendar
    assert "query_calendar_freebusy" in calendar
    assert 'row.status = "needs_user_conflict"' in calendar
    assert "calendar_event_matches" in calendar
    assert '"description" in desired' in calendar
    assert "Calendar end must be after start" in calendar
    assert "ensure_calendar_mutation_verified" in calendar
    assert 'body["id"] = event_id' in google
    assert '"vaappIdempotencyKey"' in google
    assert 'req.headers["If-Match"] = etag' in google
    assert 'event.event_type == "calendar_event_planned"' in core
    assert 'action_type="calendar_mutation"' in core
    assert 'verification_type="calendar_mutation_verified"' in core
    assert 'evidence_type="calendar_event_verified"' in core
    assert 'action_type == "calendar_mutation"' in policy


def test_v092_calendar_sync_owns_attendee_responses_and_email_scheduling() -> None:
    root = _root()
    calendar = (root / "backend/app/services/calendar_ownership.py").read_text()
    processor = (root / "backend/app/services/email_processor.py").read_text()
    workflow = (root / "backend/app/services/workflow_engine.py").read_text()
    scheduler = (root / "backend/app/services/scheduler.py").read_text()

    assert "calendar_attendee_response_received" in calendar
    assert "owned_objective_id" in calendar
    assert 'event_type="calendar_event_planned"' in processor
    assert "calendar_event_queued" in processor
    assert "create_calendar_event(" not in processor
    routes = (root / "backend/app/api/routes.py").read_text()
    assert "create_calendar_event(" not in routes
    assert 'event_key=f"task:{task.id}:calendar-plan:v1"' in routes
    assert '@job_handler("calendar.sync")' in workflow
    assert 'job_type="calendar.sync"' in scheduler
    assert 'id="calendar_sync_enqueue"' in scheduler


def test_v092_api_and_android_expose_calendar_ownership() -> None:
    root = _root()
    routes = (root / "backend/app/api/routes.py").read_text()
    state = (root / "android/lib/app_state.dart").read_text()
    work = (root / "android/lib/screens/work_page.dart").read_text()

    assert '@router.get("/api/calendar/status")' in routes
    assert '@router.get("/api/calendar/events")' in routes
    assert '@router.get("/api/calendar/availability")' in routes
    assert '@router.post("/api/calendar/sync")' in routes
    assert '@router.post("/api/calendar/objectives")' in routes
    assert "'/api/calendar/status'" in state
    assert "'/api/calendar/events?days=60'" in state
    assert "syncCalendarNow" in state
    assert "Tab(text: 'Calendar')" in work
    assert "Calendar ownership" in work
