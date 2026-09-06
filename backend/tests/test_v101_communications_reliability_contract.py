from pathlib import Path

ROOT = Path(__file__).parents[2]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_v101_release_identity() -> None:
    assert 'APP_VERSION = "1.0.19"' in _read("backend/app/core/version.py")
    assert 'REQUIRED_ANDROID_VERSION = "1.0.19"' in _read("backend/app/core/version.py")
    assert 'version = "1.0.19"' in _read("backend/pyproject.toml")
    assert "version: 1.0.19+62" in _read("android/pubspec.yaml")
    assert "const String appRelease = '1.0.19';" in _read("android/lib/release_contract.dart")
    assert "const String minimumBackendVersion = '1.0.19';" in _read("android/lib/release_contract.dart")
    workflow = _read(".github/workflows/android-release.yml")
    assert "Full-Time-VA-Android-v1.0.5.apk" in workflow


def test_phone_sync_is_observable_and_checks_receive_permission() -> None:
    state = _read("android/lib/app_state.dart")
    page = _read("android/lib/screens/communications_page.dart")
    activity = _read("android/android/app/src/main/kotlin/com/fulltimeva/full_time_va/MainActivity.kt")

    assert "lastCommunicationSync" in state
    assert "lastCommunicationSync = await DeviceBridge.syncRecentCommunications()" in state
    assert "policy_synced" in state
    assert "Phone communication sync failed" in state
    assert "Phone sync complete" in page
    assert "Sync SMS/call history & policies now" in page
    assert "SMS receive/read/send permissions" in page
    assert "ok('receive_sms')" in page
    assert '"receive_sms" to hasPermission(Manifest.permission.RECEIVE_SMS)' in activity
    assert '"pending_communication_events"' in activity
    assert '"last_backend_error"' in activity


def test_history_upload_is_bounded_and_does_not_spend_ai_per_record() -> None:
    activity = _read("android/android/app/src/main/kotlin/com/fulltimeva/full_time_va/MainActivity.kt")
    client = _read("android/android/app/src/main/kotlin/com/fulltimeva/full_time_va/VaBackendClient.kt")
    service = _read("backend/app/services/communications_service.py")

    assert "postBatchChunked(this, events, 25)" in activity
    assert "fun postBatchChunked" in client
    assert "chunkSize: Int = 25" in client
    assert 'payload.provider in {"android_sms_history", "android_mms_history", "android_call_log"}' in service
    assert "return fallback" in service


def test_inbound_sms_is_durable_before_network_dispatch() -> None:
    receiver = _read("android/android/app/src/main/kotlin/com/fulltimeva/full_time_va/SmsReceiver.kt")
    mms = _read("android/android/app/src/main/kotlin/com/fulltimeva/full_time_va/MmsReceiver.kt")
    client = _read("android/android/app/src/main/kotlin/com/fulltimeva/full_time_va/VaBackendClient.kt")
    worker = _read("android/android/app/src/main/kotlin/com/fulltimeva/full_time_va/VaCommunicationPendingWorker.kt")

    queue_pos = receiver.index("VaBackendClient.queueCommunicationEvent(context, event)")
    post_pos = receiver.index("VaBackendClient.postEvent(context, event)")
    assert queue_pos < post_pos
    assert "goAsync()" in receiver
    assert "VaCommunicationPendingWorker.scheduleImmediate(context)" in receiver
    assert "removeQueuedCommunicationEvent" in receiver
    assert "KEY_PENDING_EVENTS" in client
    assert "encrypt(events.toString())" in client
    assert "flushPendingCommunicationEvents" in worker
    assert "Result.retry()" in worker
    assert "fun scheduleImmediate(context: Context)" in worker
    assert "VaBackendClient.queueCommunicationEvent(context, event)" in mms
    assert "goAsync()" in mms
    assert "VaCommunicationPendingWorker.scheduleImmediate(context)" in mms


def test_supported_notification_ingestion_has_record_only_failure_recovery_and_rcs_capture() -> None:
    listener = _read("android/android/app/src/main/kotlin/com/fulltimeva/full_time_va/VaNotificationListenerService.kt")
    page = _read("android/lib/screens/communications_page.dart")

    assert "VaBackendClient.queueCommunicationEvent(this, queued)" in listener
    assert '.put("supports_direct_reply", false)' in listener
    assert '.put("allow_action", false)' in listener
    assert 'packageName == "com.google.android.apps.messaging"' in listener
    assert 'packageName == "com.samsung.android.messaging"' in listener
    assert "Messenger / RCS access" in page
    assert "captured from new Android notifications" in page


def test_native_http_failures_are_not_silently_discarded() -> None:
    client = _read("android/android/app/src/main/kotlin/com/fulltimeva/full_time_va/VaBackendClient.kt")
    activity = _read("android/android/app/src/main/kotlin/com/fulltimeva/full_time_va/MainActivity.kt")

    assert "KEY_LAST_REQUEST_ERROR" in client
    assert "fun lastRequestError" in client
    assert 'saveLastRequestError(context, "Native VA backend link is missing")' in client
    assert 'saveLastRequestError(context, "HTTP $code' in client
    assert '"success" to false' in activity
    assert '"error" to "The native phone bridge is not linked to the VA backend."' in activity


def test_gmail_pubsub_configuration_activates_watch_without_waiting_for_periodic_tick() -> None:
    routes = _read("backend/app/api/routes.py")
    assert 'section_slug == "google"' in routes
    assert 'job_type="gmail.watch.ensure"' in routes
    assert 'payload={"force": True}' in routes
    assert 'gmail.watch.configure:' in routes
    assert 'gmail.watch.oauth:' in routes
