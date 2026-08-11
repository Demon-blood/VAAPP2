from pathlib import Path


def _root() -> Path:
    return Path(__file__).parents[2]


def test_gmail_v070_reconciles_old_labeled_inbox_without_historical_trash() -> None:
    source = (_root() / "backend" / "app" / "services" / "email_processor.py").read_text()
    block = source.split("async def reconcile_v070_processed_inbox", 1)[1].split("def _safe_low_value_for_trash", 1)[0]
    assert 'marker = "v070_gmail_inbox_policy_reconciled"' in block
    assert 'q="in:inbox"' in block
    assert "AutomationDecision.model_validate_json" in block
    assert "_apply_inbox_policy" in block
    assert 'remove_labels=["INBOX"]' in block
    assert ".trash(" not in block


def test_money_creation_persists_intent_before_provider_and_serializes_source() -> None:
    banking = (_root() / "backend" / "app" / "services" / "banking_service.py").read_text()
    bill_block = banking.split("async def create_payment_for_bill", 1)[1].split("async def refresh_payment", 1)[0]
    assert ".with_for_update()" in bill_block
    assert bill_block.index("await db.commit()") < bill_block.index("enable_banking.create_sepa_payment")
    assert 'payment.status = "creation_uncertain"' in bill_block
    assert "automatic retry is blocked" in bill_block

    finance = (_root() / "backend" / "app" / "services" / "financial_autopilot.py").read_text()
    transfer_block = finance.split("async def create_own_account_transfer", 1)[1].split("async def refresh_own_account_transfer", 1)[0]
    assert ".with_for_update()" in transfer_block
    assert transfer_block.index("await db.commit()") < transfer_block.index("enable_banking.create_sepa_payment")
    assert "finance_max_single_transfer" in transfer_block
    assert "finance_daily_internal_transfer_limit" in transfer_block
    assert "finance_min_operating_cash_floor" in transfer_block
    assert "monthly_outbound_limit" in transfer_block
    assert 'transfer.status = "creation_uncertain"' in transfer_block
    assert "debtor_iban=source.iban" in transfer_block


def test_successful_sca_callbacks_clear_needs_you_state() -> None:
    banking = (_root() / "backend" / "app" / "services" / "banking_service.py").read_text()
    payment_callback = banking.split("async def complete_payment_authorization", 1)[1].split("async def auto_pay_eligible_bills", 1)[0]
    assert "state_row.expires_at < datetime.utcnow()" in payment_callback
    assert "payment.authorization_url = None" in payment_callback
    assert "payment.requires_user_action = False" in payment_callback

    finance = (_root() / "backend" / "app" / "services" / "financial_autopilot.py").read_text()
    transfer_callback = finance.split("async def complete_own_transfer_authorization", 1)[1].split("async def run_budget_autopilot", 1)[0]
    assert "transfer.authorization_url = None" in transfer_callback
    assert "transfer.requires_user_action = False" in transfer_callback
    assert 'Task.source_type == "bank_transfer_authorization"' in transfer_callback


def test_budget_engine_supports_percentage_of_income_allocation() -> None:
    model = (_root() / "backend" / "app" / "models" / "entities.py").read_text()
    schema = (_root() / "backend" / "app" / "schemas" / "api.py").read_text()
    finance = (_root() / "backend" / "app" / "services" / "financial_autopilot.py").read_text()
    ui = (_root() / "android" / "lib" / "screens" / "finance_autopilot_page.dart").read_text()
    assert "income_allocation_percent" in model
    assert "income_allocation_percent" in schema
    assert "current_month_income_by_scope" in finance
    assert "tax_gap_remaining" in finance
    assert "Income allocation %" in ui


def test_native_background_credentials_use_android_keystore_and_notifications_are_content_deduped() -> None:
    native = _root() / "android" / "android" / "app" / "src" / "main" / "kotlin" / "com" / "fulltimeva" / "full_time_va"
    client = (native / "VaBackendClient.kt").read_text()
    listener = (native / "VaNotificationListenerService.kt").read_text()
    assert 'KeyStore.getInstance("AndroidKeyStore")' in client
    assert 'Cipher.getInstance("AES/GCM/NoPadding")' in client
    assert 'remove("server_url").remove("device_token")' in client
    assert "Notification.FLAG_GROUP_SUMMARY" in listener
    assert "contentFingerprint" in listener
    assert "sbn.postTime}" not in listener


def test_gmail_low_value_cleanup_only_targets_read_aged_safe_categories() -> None:
    source = (_root() / "backend" / "app" / "services" / "email_processor.py").read_text()
    block = source.split("async def cleanup_v070_read_low_value_mail", 1)[1].split("async def sync_gmail", 1)[0]
    assert 'is:read older_than:{days}d -in:trash' in block
    assert "_safe_low_value_for_trash" in block
    assert ".trash(" in block
    policy = source.split("def _safe_low_value_for_trash", 1)[1].split("async def cleanup_v070_read_low_value_mail", 1)[0]
    for guard in (
        "not decision.action_required",
        "not decision.preserve",
        'decision.financial_document_type == "none"',
        "decision.task is None",
        "decision.bill is None",
        "decision.reply is None",
    ):
        assert guard in policy


def test_cash_safety_reserves_unreflected_money_movements() -> None:
    banking = (_root() / "backend" / "app" / "services" / "banking_service.py").read_text()
    finance = (_root() / "backend" / "app" / "services" / "financial_autopilot.py").read_text()
    cash = (_root() / "backend" / "app" / "services" / "cash_safety.py").read_text()
    assert "effective_available_balance" in banking
    assert "effective_available_balance" in finance
    assert "committed_destination_balance" in finance
    assert "Payment.bank_account_id == account.id" in cash
    assert "OwnAccountTransfer.source_account_id == account.id" in cash
    assert "OwnAccountTransfer.destination_account_id == account.id" in cash
    assert "updated_at > last_synced_at" in cash
    assert "quarantine_stale_creation_intents" in cash
    assert 'status = "creation_uncertain"' in cash
    assert "automatic retry is blocked" in cash
    scheduler = (_root() / "backend" / "app" / "services" / "scheduler.py").read_text()
    assert "quarantine_stale_creation_intents" in scheduler
