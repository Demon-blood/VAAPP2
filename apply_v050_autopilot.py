from __future__ import annotations

import argparse
import shutil
from pathlib import Path

EXPECTED_BASE_VERSION = 'APP_VERSION = "0.4.16"'


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply Full-Time VA v0.5.0 Autopilot backend reliability tranche")
    parser.add_argument("repo", type=Path, help="Path to a VAAPP2 checkout")
    args = parser.parse_args()
    repo = args.repo.resolve()
    bundle = Path(__file__).resolve().parent

    entities = repo / "backend/app/models/entities.py"
    scheduler = repo / "backend/app/services/scheduler.py"
    main_app = repo / "backend/app/main.py"
    autopilot_api = repo / "backend/app/api_autopilot.py"
    version = repo / "backend/app/core/version.py"
    pyproject = repo / "backend/pyproject.toml"
    tests = repo / "backend/tests/test_workflow_engine.py"
    version_test = repo / "backend/tests/test_version_and_routes.py"
    workflow_engine = repo / "backend/app/services/workflow_engine.py"

    for required in (entities, scheduler, main_app, version, pyproject):
        if not required.is_file():
            raise SystemExit(f"Missing expected repository file: {required}")

    version_text = version.read_text(encoding="utf-8")
    if EXPECTED_BASE_VERSION not in version_text and 'APP_VERSION = "0.5.0"' not in version_text:
        raise SystemExit("Unexpected backend base version; refusing to modify an unverified checkout")

    entities_text = entities.read_text(encoding="utf-8")
    if "class WorkflowJob(Base):" not in entities_text:
        entities_text = entities_text.rstrip() + (bundle / "model_append.pyfrag").read_text(encoding="utf-8") + "\n"
        entities.write_text(entities_text, encoding="utf-8")

    workflow_engine.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(bundle / "backend/app/services/workflow_engine.py", workflow_engine)
    shutil.copy2(bundle / "backend/app/services/scheduler.py", scheduler)
    shutil.copy2(bundle / "backend/app/main.py", main_app)
    shutil.copy2(bundle / "backend/app/api_autopilot.py", autopilot_api)
    tests.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(bundle / "backend/tests/test_workflow_engine.py", tests)
    if version_test.is_file():
        text = version_test.read_text(encoding="utf-8")
        text = text.replace('assert APP_VERSION == "0.4.16"', 'assert APP_VERSION == "0.5.0"')
        version_test.write_text(text, encoding="utf-8")

    version_text = version.read_text(encoding="utf-8")
    version_text = version_text.replace('APP_VERSION = "0.4.16"', 'APP_VERSION = "0.5.0"')
    # Keep Android 0.4.16+ compatible during the backend-first rollout.
    version.write_text(version_text, encoding="utf-8")

    pyproject_text = pyproject.read_text(encoding="utf-8")
    pyproject_text = pyproject_text.replace('version = "0.4.16"', 'version = "0.5.0"', 1)
    pyproject.write_text(pyproject_text, encoding="utf-8")

    print("Applied Full-Time VA v0.5.0 Autopilot backend reliability tranche.")
    print("Next: cd backend && pytest -q && ruff check app tests")


if __name__ == "__main__":
    main()
