import ast
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parents[2]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _database_datetime_function():
    source = _read("backend/app/services/communications_service.py")
    tree = ast.parse(source)
    function = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_database_datetime"
    )
    module = ast.Module(body=[function], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {"datetime": datetime, "timezone": timezone}
    exec(compile(module, "communications_service.py", "exec"), namespace)
    return namespace["_database_datetime"]


def test_android_rfc3339_timestamp_is_normalized_for_naive_sql_datetime() -> None:
    normalize = _database_datetime_function()
    incoming = datetime(2026, 8, 14, 7, 30, 15, 123000, tzinfo=timezone.utc)
    normalized = normalize(incoming)
    assert normalized == datetime(2026, 8, 14, 7, 30, 15, 123000)
    assert normalized.tzinfo is None


def test_naive_timestamp_is_preserved() -> None:
    normalize = _database_datetime_function()
    incoming = datetime(2026, 8, 14, 7, 30, 15)
    assert normalize(incoming) is incoming


def test_batch_ingest_isolates_failed_records() -> None:
    source = _read("backend/app/api/routes.py")
    assert "await db.rollback()" in source
    assert '"failed": failed' in source
    assert '"failures": failures' in source


def test_android_does_not_treat_partial_batch_failure_as_success() -> None:
    client = _read("android/android/app/src/main/kotlin/com/fulltimeva/full_time_va/VaBackendClient.kt")
    activity = _read("android/android/app/src/main/kotlin/com/fulltimeva/full_time_va/MainActivity.kt")
    page = _read("android/lib/screens/communications_page.dart")
    assert 'val chunkFailed = response.optInt("failed", 0)' in client
    assert '.put("success", failed == 0)' in client
    assert '"failed" to history.optInt("failed", 0)' in activity
    assert "final failed = (result['failed'] as num?)?.toInt() ?? 0;" in page
