from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "android" / "lib" / "services" / "background_service.dart"


def _source() -> str:
    return SOURCE.read_text(encoding="utf-8")


def test_v122_briefing_uses_big_text_expansion() -> None:
    source = _source()
    assert "BigTextStyleInformation(" in source
    assert "styleInformation: BigTextStyleInformation(" in source
    assert "_briefingExpandedBody" in source


def test_v122_collapsed_notification_stays_concise() -> None:
    source = _source()
    assert "_briefingCollapsedBody" in source
    assert source.count("body: _briefingCollapsedBody(briefingBody)") == 2
    assert "Expand to read the full briefing." in source


def test_v122_covers_current_and_legacy_briefing_paths() -> None:
    source = _source()
    assert source.count("id: 1002") == 2
    assert source.count("_briefingNotificationDetails(briefingTitle, briefingBody)") == 2
    assert "Your VA briefing is ready." in source
    assert "Your daily VA briefing is ready." in source
