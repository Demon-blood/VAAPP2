from __future__ import annotations

import ast
from pathlib import Path

from app.core.version import APP_VERSION


def test_backend_version_matches_release() -> None:
    assert APP_VERSION == "0.5.1"


def test_required_system_and_connector_routes_exist() -> None:
    routes_source = (Path(__file__).parents[1] / "app/api/routes.py").read_text()
    module = ast.parse(routes_source)
    paths: set[str] = set()
    for node in module.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not decorator.args:
                continue
            value = decorator.args[0]
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                paths.add(value.value)
    assert "/api/system/info" in paths
    assert "/api/ai/status" in paths
    assert "/api/actions/run" in paths
    assert "/api/tasks/{task_id}/execute" in paths
    assert "/api/payments/auto-run" in paths
    assert "/api/documents/cleanup" in paths
    assert "/api/bills" in paths
    assert "/api/financial-records" in paths
    assert "/api/financial-records/reconcile" in paths
    assert "/api/connectors/templates" in paths
    assert "/api/connectors/presets" in paths
    assert "/api/connectors" in paths
