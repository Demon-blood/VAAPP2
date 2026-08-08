from __future__ import annotations

import ast
from pathlib import Path


def test_fine_grained_pat_skips_unsupported_notifications_endpoint() -> None:
    source = (Path(__file__).parents[1] / "app/integrations/github_api.py").read_text()
    module = ast.parse(source)
    function = next(
        node
        for node in module.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "list_notifications"
    )
    rendered = ast.unparse(function)
    assert "startswith('github_pat_')" in rendered or 'startswith("github_pat_")' in rendered
    assert "return []" in rendered
    assert "'/notifications'" in rendered or '"/notifications"' in rendered
