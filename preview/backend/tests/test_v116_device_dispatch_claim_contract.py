from pathlib import Path

ROOT = Path(__file__).parents[2]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_v116_release_identity() -> None:
    version = _read("backend/app/core/version.py")
    assert 'APP_VERSION = "1.0.16"' in version
    assert 'REQUIRED_ANDROID_VERSION = "1.0.16"' in version
    assert 'version = "1.0.16"' in _read("backend/pyproject.toml")
    assert "version: 1.0.16+59" in _read("android/pubspec.yaml")
    release = _read("android/lib/release_contract.dart")
    assert "const String appRelease = '1.0.16';" in release
    assert "const String minimumBackendVersion = '1.0.16';" in release


def test_backend_claim_is_durable_idempotent_and_cross_device_exclusive() -> None:
    models = _read("backend/app/models/entities.py")
    service = _read("backend/app/services/communications_service.py")
    routes = _read("backend/app/api/routes.py")
    assert "class CommunicationDispatchClaim" in models
    assert '__tablename__ = "communication_dispatch_claims"' in models
    assert 'ForeignKey("communication_actions.id", ondelete="CASCADE")' in models
    assert "async def claim_communication_action" in service
    assert "CommunicationDispatchClaim" in service
    assert "db.begin_nested()" in service
    assert "except IntegrityError" in service
    assert 'existing.device_id == device_id and action.status == "dispatching"' in service
    assert 'action.status != "pending"' in service
    assert 'action.status = "dispatching"' in service
    assert '"creation_uncertain"' in service
    assert "delete(CommunicationDispatchClaim)" in service
    assert "communication_action_dispatch_claimed" in service
    assert 'CommunicationAction.status.in_(["pending", "dispatching"])' in service
    assert 'resumable_claim = action.status == "dispatching" and channel == "sms"' in service
    assert '"can_resume_claimed_dispatch": resumable_claim' in service
    assert '@router.post("/api/communications/actions/{action_id}/claim")' in routes
    assert "claim_communication_action" in routes


def test_android_claim_precedes_local_guard_and_carrier_side_effect() -> None:
    worker = _read(
        "android/android/app/src/main/kotlin/com/fulltimeva/full_time_va/"
        "VaCommunicationPendingWorker.kt"
    )
    client = _read(
        "android/android/app/src/main/kotlin/com/fulltimeva/full_time_va/VaBackendClient.kt"
    )
    claim = worker.index("VaBackendClient.claimCommunicationAction(applicationContext, actionId)")
    local_guard = worker.index("VaBackendClient.markActionExecuted(applicationContext, actionId)")
    carrier_send = worker.index("VaSms.send(applicationContext, target, text, actionId)")
    assert claim < local_guard < carrier_send
    assert "fun claimCommunicationAction(context: Context, actionId: Long): Boolean" in client
    assert "repeat(3)" in client
    assert '"POST",' in client
    assert '"/api/communications/actions/$actionId/claim",' in client
    assert 'return prefs.edit().putBoolean(key, true).commit()' in client


def test_device_local_evidence_reconciliation_remains_before_any_send() -> None:
    worker = _read(
        "android/android/app/src/main/kotlin/com/fulltimeva/full_time_va/"
        "VaCommunicationPendingWorker.kt"
    )
    stored = worker.index("VaBackendClient.repostStoredActionEvidence(applicationContext, actionId)")
    carrier = worker.index("VaSms.repostEvidenceIfAvailable(applicationContext, actionId)")
    claim = worker.index("VaBackendClient.claimCommunicationAction(applicationContext, actionId)")
    send = worker.index("VaSms.send(applicationContext, target, text, actionId)")
    assert stored < claim
    assert carrier < claim
    assert claim < send
    assert "action_done_$actionId" in _read(
        "android/android/app/src/main/kotlin/com/fulltimeva/full_time_va/VaBackendClient.kt"
    )


def test_device_uncertainty_has_no_elapsed_time_terminalization() -> None:
    core = _read("backend/app/services/autonomous_core.py")
    assert "def _device_action_verify_delay" in core
    assert "async def _recover_legacy_device_communication_uncertainty" in core
    assert "device_communication_legacy_uncertainty_reopened" in core
    assert "await _recover_legacy_device_communication_uncertainty(db, now)" in core
    assert "Waiting for the paired Android device to claim and report this action" in core
    assert "Device dispatch was claimed; waiting for durable carrier or RemoteInput evidence" in core
    assert "Android reported an ambiguous multipart SMS send outcome" in core
    assert (
        "Android device did not report a definitive dispatch outcome; automatic resend is unsafe"
        in core
    )
    assert "step.created_at <= now - timedelta(minutes=30)" not in core


def test_definitive_device_failure_and_existing_evidence_contracts_are_preserved() -> None:
    core = _read("backend/app/services/autonomous_core.py")
    service = _read("backend/app/services/communications_service.py")
    assert 'if action.status == "failed":' in core
    assert 'if action.status == "delivery_failed":' in core
    assert '"sms_sent"' in core
    assert '"sms_delivered"' in core
    assert '"remote_input_dispatched"' in core
    assert '"delivery_failed": "sms_delivery_failed"' in service
    assert "Never turn a proven send/provider handoff into a retryable failure" in service


def test_notification_remote_input_claims_before_provider_side_effect() -> None:
    listener = _read(
        "android/android/app/src/main/kotlin/com/fulltimeva/full_time_va/"
        "VaNotificationListenerService.kt"
    )
    claim = listener.index("VaBackendClient.claimCommunicationAction(this, actionId)")
    local_guard = listener.index("VaBackendClient.markActionExecuted(this, actionId)")
    provider_send = listener.index("replyAction.actionIntent.send(this, 0, fillInIntent)")
    assert claim < local_guard < provider_send


def test_negative_device_evidence_is_durable_when_backend_callback_fails() -> None:
    client = _read(
        "android/android/app/src/main/kotlin/com/fulltimeva/full_time_va/VaBackendClient.kt"
    )
    worker = _read(
        "android/android/app/src/main/kotlin/com/fulltimeva/full_time_va/"
        "VaCommunicationPendingWorker.kt"
    )
    listener = _read(
        "android/android/app/src/main/kotlin/com/fulltimeva/full_time_va/"
        "VaNotificationListenerService.kt"
    )
    receiver = _read(
        "android/android/app/src/main/kotlin/com/fulltimeva/full_time_va/SmsStatusReceiver.kt"
    )
    assert "fun postOrStoreActionResult" in client
    assert '.put("failure_reason", failureReason.take(1900))' in client
    assert 'failureReason = evidence.optString("failure_reason")' in client
    assert "VaBackendClient.postOrStoreActionResult" in worker
    assert "VaBackendClient.postOrStoreActionResult" in listener
    assert receiver.count("VaBackendClient.postOrStoreActionResult") >= 2
    assert "val multipartUncertain = partCount > 1" in receiver
    assert 'if (multipartUncertain) "creation_uncertain" else "failed"' in receiver
    assert "if (!multipartUncertain) VaBackendClient.clearActionExecuted" in receiver


def test_reconciliation_only_rows_preserve_positive_sms_evidence_before_failure_evidence() -> None:
    worker = _read(
        "android/android/app/src/main/kotlin/com/fulltimeva/full_time_va/"
        "VaCommunicationPendingWorker.kt"
    )
    carrier = worker.index("VaSms.repostEvidenceIfAvailable(applicationContext, actionId)")
    stored = worker.index(
        "VaBackendClient.repostStoredActionEvidence(applicationContext, actionId)",
        carrier,
    )
    claim = worker.index("VaBackendClient.claimCommunicationAction(applicationContext, actionId)")
    assert carrier < stored < claim
    assert 'val channel = action.optString("channel")' in worker
    assert 'if (channel != "sms") {' in worker
    assert 'action.optBoolean("can_resume_claimed_dispatch", false)' in worker
