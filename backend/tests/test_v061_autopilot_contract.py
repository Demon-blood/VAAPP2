from pathlib import Path


def _root() -> Path:
    return Path(__file__).parents[2]


def test_scheduler_runs_proactive_planner_and_local_daily_gate() -> None:
    source = (_root() / "backend" / "app" / "services" / "scheduler.py").read_text()
    assert 'job_type="autopilot.plan"' in source
    assert 'minutes=15' in source
    assert 'daily_briefing_hour_local' in source
    assert 'autopilot.daily_briefing:{local_now.date().isoformat()}' in source
    assert 'hours=1' in source


def test_payment_initiation_has_one_durable_autopilot_path() -> None:
    workflow = (_root() / "backend" / "app" / "services" / "workflow_engine.py").read_text()
    banking_handler = workflow.split('@job_handler("banking.autopilot")', 1)[1].split(
        '@job_handler("google.contacts.sync")', 1
    )[0]
    assert "auto_pay_eligible_bills" not in banking_handler
    assert 'payment_initiation": "delegated_to_durable_bill_lifecycle"' in banking_handler
    assert '@job_handler("bill.lifecycle")' in workflow


def test_today_is_exception_first_without_routine_count_cards() -> None:
    dashboard = (_root() / "android" / "lib" / "screens" / "dashboard_page.dart").read_text()
    needs = dashboard.index("_NeedsYouSection(")
    briefing = dashboard.index("DailyBriefingCard(briefing: state.dailyBriefing)")
    library = dashboard.index("_WorkLibrary(")
    assert needs < briefing < library
    assert "CountCard(" not in dashboard
    assert "actionable_dead_letters" in dashboard
    assert "recovering_jobs" in dashboard


def test_daily_briefing_exposes_activity_timeline_and_provider_auth_exceptions() -> None:
    briefing = (_root() / "backend" / "app" / "services" / "briefing_service.py").read_text()
    widget = (_root() / "android" / "lib" / "widgets" / "daily_briefing_card.dart").read_text()
    assert '"activity": [_activity_item(row)' in briefing
    assert '"provider_authorization"' in briefing
    assert "VA activity timeline" in widget
