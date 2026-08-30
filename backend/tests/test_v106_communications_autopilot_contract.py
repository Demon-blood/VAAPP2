from pathlib import Path

ROOT = Path(__file__).parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_attention_and_backlog_are_wired_into_live_paths():
    service = read("backend/app/services/communications_service.py")
    core = read("backend/app/services/autonomous_core.py")
    planner = read("backend/app/services/autopilot_planner.py")
    reconciler = read("backend/app/services/communication_backlog_repair.py")
    assert "normalize_communication_attention" in service
    assert "android_mms_history" in service
    assert "repair_communication_backlog" in core
    assert "repaired_emails" in reconciler
    assert "blocked_capability" in planner
    assert 'result="needs_user"' not in planner.split("async def _escalate_unexecutable_due_tasks", 1)[1].split("async def proactive_plan", 1)[0]


def test_briefing_is_human_style_and_period_configurable():
    briefing = read("backend/app/services/briefing_service.py")
    policy = read("backend/app/services/briefing_policy.py")
    config = read("backend/app/services/runtime_config.py")
    background = read("android/lib/services/background_service.dart")
    assert "human_briefing_summary" in briefing
    assert "Good afternoon." in policy
    assert "Autopilot handled {stats['va_actions']}" not in briefing
    for name in ("morning", "afternoon", "evening"):
        assert f"briefing_{name}_enabled" in config
        assert f"briefing_{name}_hour_local" in config
    assert "last_va_briefing_period" in background
    assert "_isImmediateInterrupt" in background
    assert '"urgent_communication"' in policy


def test_android_sms_mms_conversation_and_quiet_interrupt_contract():
    activity = read("android/android/app/src/main/kotlin/com/fulltimeva/full_time_va/MainActivity.kt")
    receiver = read("android/android/app/src/main/kotlin/com/fulltimeva/full_time_va/SmsReceiver.kt")
    comms = read("android/lib/screens/communications_page.dart")
    conversations = read("android/lib/screens/message_conversations_page.dart")
    state = read("android/lib/app_state.dart")
    assert "collectMms" in activity
    assert 'provider", "android_mms_history"' in activity
    assert 'optBoolean("interrupt", false)' in receiver
    assert 'optBoolean("delete_from_device", false)' in receiver
    assert "MessageConversationsPage" in comms
    assert "SMS/MMS conversations" in conversations
    assert "/api/communications/events?limit=1000" in state
    assert "version: 1.0.8+51" in read("android/pubspec.yaml")


def test_low_value_mail_and_provider_errors_stay_off_user_queue():
    processor = read("backend/app/services/email_processor.py")
    policy = read("backend/app/services/ai_policy.py")
    assert '"CATEGORY_PROMOTIONS" in label_ids' in policy
    assert "safe_low_value_list" in policy
    assert 'result="blocked_system"' in processor
    assert "if False and existing_archive_task is None" in processor
    assert "Handled with deterministic fallback while the AI provider was unavailable." in policy


def test_manual_complete_is_exception_only():
    tasks = read("android/lib/screens/tasks_page.dart")
    assert "manualCompletionAllowed" in tasks
    assert "I did this" in tasks
    assert "label: const Text('Complete')" not in tasks
