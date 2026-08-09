# Full-Time VA v0.5.0 — Autopilot CI repair

Target repository state: `Demon-blood/VAAPP2` commit `910648297bb9e6da9492744df60082fb0fd4b7aa` (`AutoPilot`).

The commit contains the durable workflow engine, scheduler wiring and Autopilot API,
but it omitted the three ORM model classes that those modules import. It also still
reports backend/package version `0.4.16`.

## Apply

From any directory:

```bash
python repair_v050_autopilot.py /path/to/VAAPP2
```

Then:

```bash
cd /path/to/VAAPP2/backend
python -m compileall -q app tests
pytest -q
ruff check app tests
```

If green:

```bash
git diff -- backend/
git add backend/app/models/entities.py \
        backend/app/core/version.py \
        backend/pyproject.toml \
        backend/tests/test_version_and_routes.py
git commit -m "Fix v0.5.0 Autopilot workflow models and release metadata"
git push origin main
```

## Database safety

This repair only adds three SQLAlchemy model definitions:

- `workflow_runs`
- `workflow_jobs`
- `workflow_job_dependencies`

The existing startup `metadata.create_all()` creates those tables if absent. No existing
table is dropped, renamed or rewritten by this repair.

## What this does not do

It does not call Gmail, banking, Google APIs, AI providers or connectors. It does not
modify existing OAuth/banking rows. It does not fake provider success.
