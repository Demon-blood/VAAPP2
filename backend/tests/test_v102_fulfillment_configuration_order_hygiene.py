from pathlib import Path

ROOT = Path(__file__).parents[2]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_fulfillment_provider_configuration_is_editable_without_numeric_portal_ids() -> None:
    page = _read("android/lib/screens/fulfillment_page.dart")
    service = _read("backend/app/services/fulfillment_service.py")
    assert "api.getJson('/api/browser/portals')" in page
    assert "Future<void> _configureProvider([Map<String, dynamic>? existing])" in page
    assert "Edit fulfillment provider" in page
    assert "DropdownButtonFormField<int?>" in page
    assert "Secure Browser portal" in page
    assert "Secure Browser portal ID (optional)" not in page
    assert "browser_portal_name" in service
    assert '"support_phone": _decrypt_text(row.support_phone_encrypted)' in service
    assert '"recipe": recipe' in service


def test_secure_browser_portals_are_editable_and_preserve_credentials_when_blank() -> None:
    page = _read("android/lib/screens/work_page.dart")
    state = _read("android/lib/app_state.dart")
    browser = _read("backend/app/services/browser_operator.py")
    assert "Future<void> _showAddPortalDialog(BuildContext context, [Map<String, dynamic>? existing])" in page
    assert "Edit secure portal" in page
    assert "Portal enabled" in page
    assert "Leave login fields blank to keep the existing encrypted credentials" in page
    assert "if (portalId > 0 && (username.isNotEmpty || password.isNotEmpty))" in state
    assert 'if is_new or login_recipe:' in browser


def test_payment_receipts_do_not_become_logistics_objectives() -> None:
    service = _read("backend/app/services/fulfillment_service.py")
    routes = _read("backend/app/api/routes.py")
    assert "order_is_fulfillment_candidate" in service
    assert "Google payment/app-store receipt has no shipping, delivery, pickup or tracking evidence" in service
    assert 'request.status = "dismissed"' in service
    assert "await dismiss_order_record(db, order, reason=reason, explicit=False)" in service
    assert '@router.post("/api/orders/{order_id}/dismiss")' in routes
    assert '@router.post("/api/orders/{order_id}/restore")' in routes
    assert "include_dismissed: bool = Query(default=False)" in routes


def test_user_can_correct_a_false_order_from_orders_or_fulfillment() -> None:
    work = _read("android/lib/screens/work_page.dart")
    fulfillment = _read("android/lib/screens/fulfillment_page.dart")
    state = _read("android/lib/app_state.dart")
    assert "Not an order" in work
    assert "Receipt/payment evidence was kept" in work
    assert "Not an order" in fulfillment
    assert "source receipt/payment evidence was kept" in fulfillment
    assert "Future<void> dismissOrder(int orderId)" in state
    assert "'/api/orders/$orderId/dismiss'" in state
