from pathlib import Path

ROOT = Path(__file__).parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_v109_release_identity_is_consistent():
    assert 'APP_VERSION = "1.0.9"' in read("backend/app/core/version.py")
    assert 'REQUIRED_ANDROID_VERSION = "1.0.9"' in read("backend/app/core/version.py")
    assert 'version = "1.0.9"' in read("backend/pyproject.toml")
    assert "version: 1.0.9+52" in read("android/pubspec.yaml")
    release = read("android/lib/release_contract.dart")
    assert "appRelease = '1.0.9'" in release
    assert "minimumBackendVersion = '1.0.9'" in release


def test_briefing_delivery_ledger_is_additive_device_scoped_and_idempotent():
    entities = read("backend/app/models/entities.py")
    service = read("backend/app/services/briefing_delivery.py")
    assert "class BriefingDelivery" in entities
    assert '__tablename__ = "briefing_deliveries"' in entities
    assert 'UniqueConstraint("device_id", "delivery_key"' in entities
    assert "BriefingDelivery.device_id == device_id" in service
    assert "device.token_hash" in service
    assert "hmac.compare_digest" in service
    assert "delivered_at = datetime.now(UTC).replace(tzinfo=None)" in service
    assert "IntegrityError" in service


def test_old_briefing_get_contract_remains_and_ack_is_additive():
    api = read("backend/app/api_autopilot.py")
    assert '@router.get("/briefing")' in api
    assert "return await daily_briefing(db, device=device)" in api
    assert '@router.post("/briefing/deliveries")' in api
    assert "delivery_token" in api
    assert "window_end" not in api.split('@router.post("/briefing/deliveries")', 1)[1].split('@router.', 1)[0]


def test_notification_failure_cannot_ack_and_successful_show_is_durably_retried():
    source = read("android/lib/services/background_service.dart")
    scheduled = source.index("id: 1002")
    local_key = source.index("storage.write(key: _briefingPeriodKey", scheduled)
    pending = source.index("storage.write(key: _briefingPendingAckKey", local_key)
    ack = source.index("await _ackBriefingDelivery(", pending)
    assert scheduled < local_key < pending < ack
    # notifications.show is awaited before any local delivered/pending state is written, so
    # a thrown OS-notification failure exits through the outer catch without an ACK.
    show = source.rfind("await notifications.show(", max(0, scheduled - 200), local_key)
    assert show != -1 and show < local_key
    assert "final pendingAckKey = await storage.read(key: _briefingPendingAckKey)" in source
    assert "if (acknowledged)" in source
    assert "storage.delete(key: _briefingPendingAckKey)" in source
    assert "delivery_token" in source
    assert "/api/autopilot/briefing/deliveries" in source


def test_old_android_clients_still_use_the_original_get_briefing_contract():
    api = read("backend/app/api_autopilot.py")
    assert '@router.get("/briefing")' in api
    # Acknowledgement is a separate additive POST; old clients never need to call it.
    assert '@router.post("/briefing/deliveries")' in api


def test_urgent_interrupts_are_independent_from_scheduled_delivery_ack():
    source = read("android/lib/services/background_service.dart")
    urgent_start = source.index("id: 1001")
    scheduled_start = source.index("id: 1002")
    urgent_block = source[urgent_start:scheduled_start]
    assert "_ackBriefingDelivery" not in urgent_block
    assert "last_va_priority_signature" in source


def test_briefing_window_comes_from_proven_ack_or_fallback_not_generation_alone():
    service = read("backend/app/services/briefing_service.py")
    ledger = read("backend/app/services/briefing_delivery.py")
    assert "resolve_briefing_window_start" in service
    assert '"window_source"' in service
    assert "issue_briefing_delivery_token" in service
    assert "select(BriefingDelivery)" in ledger
    assert "max(acknowledged_boundary, floor)" in ledger
