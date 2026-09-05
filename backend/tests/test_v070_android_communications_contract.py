from pathlib import Path


def _root() -> Path:
    return Path(__file__).parents[2]


def test_manifest_declares_sms_notification_and_call_screening_components() -> None:
    manifest = (_root() / "android" / "android" / "app" / "src" / "main" / "AndroidManifest.xml").read_text()
    assert "android.permission.READ_SMS" in manifest
    assert "android.permission.SEND_SMS" in manifest
    assert "android.provider.Telephony.SMS_DELIVER" in manifest
    assert "android.service.notification.NotificationListenerService" in manifest
    assert "android.telecom.CallScreeningService" in manifest
    assert "android.permission.BIND_SCREENING_SERVICE" in manifest


def test_native_bridge_has_direct_reply_call_screening_and_durable_backend_result() -> None:
    native = _root() / "android" / "android" / "app" / "src" / "main" / "kotlin" / "com" / "fulltimeva" / "full_time_va"
    notification = (native / "VaNotificationListenerService.kt").read_text()
    calls = (native / "VaCallScreeningService.kt").read_text()
    sms = (native / "SmsReceiver.kt").read_text()
    sms_sender = (native / "VaSms.kt").read_text()
    main = (native / "MainActivity.kt").read_text()
    assert "RemoteInput.addResultsToIntent" in notification
    assert "storeActionEvidence" in notification
    assert "postOrStoreActionResult" in notification
    assert "respondToCall" in calls
    # Phase 2 requires every automatic SMS to stay correlated to its durable
    # CommunicationAction so carrier SENT/DELIVERED callbacks can be reconciled
    # without an unsafe blind resend.
    assert "VaSms.send(context, sender, text, actionId)" in sms
    assert "SmsStatusReceiver reports real carrier send/delivery callbacks" in sms
    assert "SmsStatusReceiver::class.java" in sms_sender
    assert "statusIntent(context, actionId" in sms_sender
    assert "sendMultipartTextMessage" in sms_sender
    assert "Telephony.Sms.Sent.CONTENT_URI" in sms_sender
    assert "RoleManager.ROLE_SMS" in main
    assert "RoleManager.ROLE_CALL_SCREENING" in main
    assert "syncRecentCommunications" in main
    assert "Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q" in main
    client = (native / "VaBackendClient.kt").read_text()
    assert 'KEY_ALIAS = "full_time_va_native_credentials_v1"' in client
    assert 'KeyProperties.BLOCK_MODE_GCM' in client
