from pathlib import Path


def _root() -> Path:
    return Path(__file__).parents[2]


def test_v091_gmail_is_event_driven_and_ambiguity_safe() -> None:
    root = _root()
    routes = (root / "backend/app/api/routes.py").read_text()
    google = (root / "backend/app/integrations/google_api.py").read_text()
    delivery = (root / "backend/app/services/gmail_delivery.py").read_text()
    sync = (root / "backend/app/services/gmail_sync_service.py").read_text()
    workflow = (root / "backend/app/services/workflow_engine.py").read_text()
    scheduler = (root / "backend/app/services/scheduler.py").read_text()
    processor = (root / "backend/app/services/email_processor.py").read_text()

    assert '@router.post("/api/google/pubsub")' in routes
    assert 'job_type="gmail.history.sync"' in routes
    assert 'decode_pubsub_notification' in routes
    assert '@job_handler("gmail.history.sync")' in workflow
    assert '@job_handler("gmail.watch.ensure")' in workflow
    assert 'gmail_watch_renew_enqueue' in scheduler
    assert 'list_gmail_history_added_message_ids' in google
    assert '_google_http_status(exc) == 404' in sync
    assert 'deterministic_rfc_message_id' in delivery
    assert 'find_gmail_message_by_rfc_message_id' in delivery
    assert 'creation_uncertain' in delivery
    assert 'failed_uncertain' in delivery
    assert 'row.status in {"creation_uncertain", "sent_unverified"}' in delivery
    assert 'Never re-POST' in delivery
    assert 'default=1' in (root / "backend/app/models/entities.py").read_text()
    assert 'send_gmail_message(' not in processor
    assert 'email_reply_queued' in processor
    assert 'register_email_inbound' in processor


def test_v091_objective_engine_owns_replies_followups_and_responses() -> None:
    root = _root()
    core = (root / "backend/app/services/autonomous_core.py").read_text()
    ownership = (root / "backend/app/services/communication_ownership.py").read_text()
    planner = (root / "backend/app/services/autopilot_planner.py").read_text()

    assert 'event.event_type == "email_reply_planned"' in core
    assert 'action_type="gmail_send_reply"' in core
    assert 'verification_type="gmail_outbound_verified"' in core
    assert 'event.event_type == "device_reply_planned"' in core
    assert 'verification_type="device_action_verified"' in core
    assert 'event.event_type == "communication_response_received"' in core
    assert '_handle_followup_event' in core
    assert 'blocked_capability' in core
    assert 'mark_thread_waiting_for_counterparty' in ownership
    assert 'queue_saved_email_reply' in ownership
    assert 'send_gmail_message' not in planner
    assert 'email_reply_migrated_to_objective' in planner


def test_v091_android_uses_real_sms_and_remoteinput_evidence() -> None:
    root = _root()
    sms = (root / "android/android/app/src/main/kotlin/com/fulltimeva/full_time_va/VaSms.kt").read_text()
    receiver = (root / "android/android/app/src/main/kotlin/com/fulltimeva/full_time_va/SmsStatusReceiver.kt").read_text()
    notification = (root / "android/android/app/src/main/kotlin/com/fulltimeva/full_time_va/VaNotificationListenerService.kt").read_text()
    worker = (root / "android/android/app/src/main/kotlin/com/fulltimeva/full_time_va/VaCommunicationPendingWorker.kt").read_text()
    gradle = (root / "android/tooling/app-build.gradle.kts").read_text()
    manifest = (root / "android/android/app/src/main/AndroidManifest.xml").read_text()

    assert 'sendTextMessage' in sms
    assert 'sendMultipartTextMessage' in sms
    assert 'SmsStatusReceiver' in sms
    assert '"sent"' in receiver and '"delivered"' in receiver
    assert 'delivery_failed' in receiver
    assert 'repostEvidenceIfAvailable' in worker
    assert 'repostStoredActionEvidence' in worker
    assert 'RemoteInput.addResultsToIntent' in notification
    assert 'storeActionEvidence' in notification
    assert '"dispatched"' in notification
    assert 'androidx.work:work-runtime:2.11.2' in gradle
    assert '.SmsStatusReceiver' in manifest


def test_v091_ui_exposes_mailbox_and_conversation_ownership() -> None:
    root = _root()
    state = (root / "android/lib/app_state.dart").read_text()
    page = (root / "android/lib/screens/communications_page.dart").read_text()
    routes = (root / "backend/app/api/routes.py").read_text()
    assert '/api/google/mailbox-status' in state
    assert '/api/communications/threads?limit=100' in state
    assert 'Conversation ownership' in page
    assert 'Inbox ownership' in page
    assert '@router.get("/api/communications/threads")' in routes
    assert '@router.get("/api/communications/actions/pending")' in routes
