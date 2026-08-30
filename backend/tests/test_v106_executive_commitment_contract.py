from pathlib import Path

ROOT = Path(__file__).parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_objectives_are_projected_as_va_owned_commitments():
    entities = read("backend/app/models/entities.py")
    graph = read("backend/app/services/commitment_graph.py")
    core = read("backend/app/services/autonomous_core.py")
    assert "class VACommitmentEdge" in entities
    assert '"owner": "va"' in graph
    assert '"waiting_on": "user"' not in graph  # ownership and waiting are separate concepts
    assert "reconcile_commitment_graph" in core
    assert 'payload["commitment"]' in core
    assert '"commitments": commitments' in core


def test_execution_queue_is_ranked_by_commitment_priority_and_due_date():
    core = read("backend/app/services/autonomous_core.py")
    assert "case(" in core
    assert 'VAObjective.priority == "urgent"' in core
    assert "VAObjective.due_at.asc().nullslast()" in core


def test_executive_work_view_replaces_raw_recent_objective_feed():
    page = read("android/lib/screens/va_operations_page.dart")
    assert "Working now" in page
    assert "Waiting / following up" in page
    assert "VA is resolving" in page
    assert "Recently finished" in page
    assert "recent_objectives" not in page
    assert "next_action" in page
    assert "waiting_on" in page


def test_v106_release_contract_and_routes():
    routes = read("backend/app/api/routes.py")
    assert '"commitment_graph"' in routes
    assert '"/api/va/commitments"' in routes
    assert '"/api/va/commitments/{objective_id}"' in routes
    assert 'APP_VERSION = "1.0.7"' in read("backend/app/core/version.py")
    assert 'version = "1.0.7"' in read("backend/pyproject.toml")
    assert "version: 1.0.7+50" in read("android/pubspec.yaml")
    assert "appRelease = '1.0.7'" in read("android/lib/release_contract.dart")
