# v0.5.0 Autopilot backend deployment

## Base

Apply to `Demon-blood/VAAPP2` based on commit `b8fad8c9f185423b2cda6b1349bceff3905f375e` or a descendant that still reports backend `APP_VERSION = "0.4.16"`.

## Apply

From a local checkout of VAAPP2:

```bash
python /path/to/VAAPP-v0.5.0-autopilot-backend/apply_v050_autopilot.py /path/to/VAAPP2
cd /path/to/VAAPP2/backend
pytest -q
ruff check app tests
```

Review the diff before committing:

```bash
git diff -- backend/
```

Recommended commit:

```bash
git add backend

git commit -m "v0.5.0: add durable Autopilot workflow engine"
git push origin main
```

Render can then deploy from `main` through the existing service configuration.

## Database behavior

This tranche adds three tables only:

- `workflow_runs`
- `workflow_jobs`
- `workflow_job_dependencies`

The current application startup already executes SQLAlchemy `metadata.create_all()`, so these tables are created without deleting or rewriting current PostgreSQL rows. No existing table column is modified by this tranche.

## Runtime behavior after deploy

- startup watchdog recovers jobs left `running` with expired leases;
- recurring Gmail, banking, contacts, connector-rule and housekeeping schedules enqueue durable jobs;
- workers lease jobs for execution and heartbeat the lease;
- failures use exponential retry (15s, 30s, 60s...) capped at one hour;
- jobs exceeding `max_attempts` become `dead_letter`;
- job dependencies block downstream execution until every prerequisite completes;
- authenticated diagnostics are exposed under `/api/autopilot/health`, `/api/autopilot/jobs`, `/api/autopilot/workflows`, and `/api/autopilot/jobs/{id}/requeue`.

## Compatibility

`APP_VERSION` becomes `0.5.0`; `REQUIRED_ANDROID_VERSION` remains `0.4.16` during this backend-first rollout, so the existing Android client is not intentionally blocked by this tranche.
