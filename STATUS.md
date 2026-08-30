# VAAPP v1.0.9 — Prepared Source Patch Status

## Repository baseline verified

- Repository: `Demon-blood/VAAPP2`
- Branch: `main`
- Required baseline: `2bfed2996167dbc440bb4f2a7b95f13c987f8a86`
- Baseline was independently rechecked and still matched when this bundle was produced.
- Existing release identity remains v1.0.8 / Android 1.0.8+51 until this patch is validated and published.

## What the applicator implements

- Additive `BriefingDelivery` ledger keyed by authenticated device + delivery key.
- Server-signed delivery proof carrying the server-generated briefing window.
- Server-authoritative `delivered_at`; Android cannot submit an authoritative client timestamp/window.
- Device-isolated lookup of the last successfully acknowledged briefing boundary.
- 72-hour maximum stale-boundary lookback.
- Existing `GET /api/autopilot/briefing` retained; additive `POST /api/autopilot/briefing/deliveries` acknowledgement endpoint.
- Scheduled Android notification is shown before any delivery proof is persisted or acknowledged.
- Successful OS notification writes a durable local pending ACK; transient ACK failures retry silently before the next briefing fetch.
- Urgent `interrupt=true` notification path remains independent.
- Versions advance to backend/app 1.0.9, required Android 1.0.9, Android 1.0.9+52.
- Living historical release-contract tests advance only explicit current-version literals; historical workflow/release names are preserved.
- Applicator refuses any `.github/workflows/*` diff and requires the exact v1.0.8 baseline plus a clean worktree.

## Validation actually performed in this ChatGPT session

Passed:

- Python compilation of the guarded applicator.
- AST parsing of all newly generated Python service/test modules.
- Synthetic exact-anchor execution of the Android patch logic, including notification → local dedupe/pending proof → ACK ordering.
- Guard/release-contract review against the existing v1.0.8 installer convention.
- Independent final check that GitHub `main` remains at the expected v1.0.8 commit.

Not run / not claimed:

- Full backend pytest suite.
- Ruff over the actual patched repository.
- Flutter analyze/tests against the actual patched repository.
- Android release APK build.
- Commit/push to `main`.
- GitHub v1.0.9 release publication.
- Production deployment/runtime verification.
- Phone installation/smoke test.

Those gates require an actual writable checkout/repository execution environment. The current GitHub App connection can read the public repository but a branch-write attempt to `Demon-blood/VAAPP2` returned HTTP 403, so publication was intentionally not claimed.

## Apply locally if needed

From a clean checkout whose HEAD is exactly the required baseline:

```powershell
python .\apply_v109_briefing_ledger.py D:\path\to\VAAPP2
```

The script fails closed if the baseline is wrong, the worktree is dirty, an expected source anchor changed, or a GitHub workflow file appears in the diff. It does not commit or publish automatically.
