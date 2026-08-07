from __future__ import annotations

import ast
from pathlib import Path


def _catalog() -> tuple[dict, list]:
    module = ast.parse((Path(__file__).parents[1] / "app/services/connector_service.py").read_text())
    values: dict[str, object] = {}
    for node in module.body:
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        for target in targets:
            if isinstance(target, ast.Name) and target.id in {"CONNECTOR_TEMPLATES", "CONNECTOR_PRESETS"}:
                values[target.id] = ast.literal_eval(node.value)
    return values["CONNECTOR_TEMPLATES"], values["CONNECTOR_PRESETS"]


def test_connector_presets_reference_real_templates() -> None:
    templates, presets = _catalog()
    ids: set[str] = set()
    for preset in presets:
        assert preset["id"] not in ids
        ids.add(preset["id"])
        assert preset["connector_type"] in templates
        assert str(preset["title"]).strip()
        assert str(preset["setup_url"]).startswith("https://")


def test_connector_templates_have_live_test_capability() -> None:
    templates, _ = _catalog()
    assert templates
    for connector_type, template in templates.items():
        assert "test" in template["capabilities"], connector_type
        assert isinstance(template["fields"], list)
        assert isinstance(template["operations"], list)
