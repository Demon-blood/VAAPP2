from pathlib import Path


def test_android_treats_github_notifications_as_optional() -> None:
    source = Path(__file__).parents[2] / "android" / "lib" / "app_state.dart"
    text = source.read_text()
    assert "_safeGet('/api/github/notifications', optional: true)" in text
    assert "if (!optional) endpointErrors[path] = requestError.toString();" in text


def test_backend_notifications_route_is_fail_soft() -> None:
    source = Path(__file__).parents[1] / "app" / "api" / "routes.py"
    text = source.read_text()
    start = text.index('@router.get("/api/github/notifications")')
    end = text.index('@router.post("/api/github/issues")', start)
    block = text[start:end]
    assert "except Exception:" in block
    assert "return []" in block
