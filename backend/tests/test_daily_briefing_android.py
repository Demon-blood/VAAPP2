from pathlib import Path


def test_today_screen_has_daily_briefing_card() -> None:
    root = Path(__file__).parents[2]
    dashboard = (root / "android" / "lib" / "screens" / "dashboard_page.dart").read_text()
    assert "DailyBriefingCard(briefing: state.dailyBriefing)" in dashboard
    widget = (root / "android" / "lib" / "widgets" / "daily_briefing_card.dart").read_text()
    assert "What your mail was about" in widget
    assert "Payments & bills" in widget
    assert "Replies" in widget
    assert "Tasks & deadlines" in widget
    assert "Important documents" in widget
    assert "Unusual, security, legal & financial" in widget
    assert "Automation & provider problems" in widget


def test_background_briefing_is_once_per_day_and_priority_alerts_are_deduplicated() -> None:
    root = Path(__file__).parents[2]
    source = (root / "android" / "lib" / "services" / "background_service.dart").read_text()
    assert "last_va_daily_briefing_day" in source
    assert "last_va_priority_signature" in source
    assert "va_daily_briefing" in source
