from __future__ import annotations

import argparse
import ast
from pathlib import Path

MODEL_FRAGMENT = r"""

class WorkflowRun(Base):
    __tablename__ = "workflow_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    workflow_type: Mapped[str] = mapped_column(String(120), index=True)
    correlation_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(40), default="running", index=True)
    intent_json: Mapped[str] = mapped_column(Text, default="{}")
    result_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class WorkflowJob(Base):
    __tablename__ = "workflow_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    workflow_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("workflow_runs.id"), nullable=True, index=True
    )
    job_type: Mapped[str] = mapped_column(String(120), index=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    priority: Mapped[int] = mapped_column(Integer, default=100, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=8)
    run_after: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    lease_owner: Mapped[str] = mapped_column(String(255), default="")
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, index=True
    )
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    result_json: Mapped[str] = mapped_column(Text, default="{}")
    last_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        Index("ix_workflow_jobs_due", "status", "run_after", "priority"),
    )


class WorkflowJobDependency(Base):
    __tablename__ = "workflow_job_dependencies"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("workflow_jobs.id", ondelete="CASCADE"), index=True
    )
    depends_on_job_id: Mapped[int] = mapped_column(
        ForeignKey("workflow_jobs.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        UniqueConstraint(
            "job_id",
            "depends_on_job_id",
            name="uq_workflow_job_dependency",
        ),
    )
"""

EXPECTED_PARTIAL_FILES = (
    "backend/app/services/workflow_engine.py",
    "backend/app/services/scheduler.py",
    "backend/app/api_autopilot.py",
)

def _replace_once(path: Path, old: str, new: str) -> bool:
    text = path.read_text(encoding="utf-8")
    if new in text:
        return False
    if old not in text:
        raise SystemExit(f"Expected text not found in {path}: {old!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    return True

def _validate_python(path: Path) -> None:
    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Repair the partially-applied Full-Time VA v0.5.0 Autopilot commit"
    )
    parser.add_argument("repo", type=Path, help="Path to the VAAPP2 checkout")
    args = parser.parse_args()
    repo = args.repo.resolve()

    for relative in EXPECTED_PARTIAL_FILES:
        path = repo / relative
        if not path.is_file():
            raise SystemExit(
                f"{relative} is missing. This repair targets the partial Autopilot commit "
                "and will not guess against another repository state."
            )

    entities = repo / "backend/app/models/entities.py"
    version = repo / "backend/app/core/version.py"
    pyproject = repo / "backend/pyproject.toml"
    version_test = repo / "backend/tests/test_version_and_routes.py"

    for path in (entities, version, pyproject, version_test):
        if not path.is_file():
            raise SystemExit(f"Missing expected repository file: {path}")

    changes: list[str] = []

    entities_text = entities.read_text(encoding="utf-8")
    has_run = "class WorkflowRun(Base):" in entities_text
    has_job = "class WorkflowJob(Base):" in entities_text
    has_dep = "class WorkflowJobDependency(Base):" in entities_text

    if any((has_run, has_job, has_dep)) and not all((has_run, has_job, has_dep)):
        raise SystemExit(
            "Workflow model definitions are only partially present. Refusing to append "
            "duplicates; inspect backend/app/models/entities.py manually."
        )

    if not has_job:
        required_symbols = (
            "DateTime",
            "ForeignKey",
            "Index",
            "Integer",
            "String",
            "Text",
            "UniqueConstraint",
            "Mapped",
            "mapped_column",
        )
        missing = [name for name in required_symbols if name not in entities_text]
        if missing:
            raise SystemExit(
                "entities.py is not the expected VAAPP2 model module; missing symbols: "
                + ", ".join(missing)
            )
        entities.write_text(entities_text.rstrip() + MODEL_FRAGMENT + "\n", encoding="utf-8")
        changes.append("added workflow ORM models")

    version_text = version.read_text(encoding="utf-8")
    if 'APP_VERSION = "0.4.16"' in version_text:
        version.write_text(
            version_text.replace('APP_VERSION = "0.4.16"', 'APP_VERSION = "0.5.0"', 1),
            encoding="utf-8",
        )
        changes.append("set APP_VERSION to 0.5.0")
    elif 'APP_VERSION = "0.5.0"' not in version_text:
        raise SystemExit("Unexpected APP_VERSION; refusing to guess")

    project_text = pyproject.read_text(encoding="utf-8")
    if 'version = "0.4.16"' in project_text:
        pyproject.write_text(
            project_text.replace('version = "0.4.16"', 'version = "0.5.0"', 1),
            encoding="utf-8",
        )
        changes.append("set package version to 0.5.0")
    elif 'version = "0.5.0"' not in project_text:
        raise SystemExit("Unexpected backend package version; refusing to guess")

    test_text = version_test.read_text(encoding="utf-8")
    if 'assert APP_VERSION == "0.4.16"' in test_text:
        version_test.write_text(
            test_text.replace(
                'assert APP_VERSION == "0.4.16"',
                'assert APP_VERSION == "0.5.0"',
                1,
            ),
            encoding="utf-8",
        )
        changes.append("updated backend version regression test")
    elif 'assert APP_VERSION == "0.5.0"' not in test_text:
        raise SystemExit("Unexpected version regression test; refusing to guess")

    for path in (
        entities,
        version,
        version_test,
        repo / "backend/app/services/workflow_engine.py",
        repo / "backend/app/services/scheduler.py",
        repo / "backend/app/api_autopilot.py",
        repo / "backend/app/main.py",
    ):
        _validate_python(path)

    final_entities = entities.read_text(encoding="utf-8")
    for class_name in ("WorkflowRun", "WorkflowJob", "WorkflowJobDependency"):
        if f"class {class_name}(Base):" not in final_entities:
            raise SystemExit(f"Repair validation failed: {class_name} missing")

    if 'APP_VERSION = "0.5.0"' not in version.read_text(encoding="utf-8"):
        raise SystemExit("Repair validation failed: APP_VERSION not updated")
    if 'version = "0.5.0"' not in pyproject.read_text(encoding="utf-8"):
        raise SystemExit("Repair validation failed: package version not updated")

    if changes:
        print("Applied Autopilot CI repair:")
        for change in changes:
            print(f" - {change}")
    else:
        print("Autopilot CI repair is already applied; no file changes were needed.")

    print()
    print("Run the authoritative gates next:")
    print("  cd backend")
    print("  python -m compileall -q app tests")
    print("  pytest -q")
    print("  ruff check app tests")

if __name__ == "__main__":
    main()
