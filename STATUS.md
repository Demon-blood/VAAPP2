# VAAPP v1.0.13 — Gmail Late-Evidence Recovery & Delivery Continuity

Updated: 2026-08-30

## Source of truth

- Repository: `Demon-blood/VAAPP2`
- Branch: `main`
- Verified v1.0.12 source baseline: `22a392f1341ef19caf8a761cd7bfa44000fdc08c`
- Verified v1.0.12 GitHub Actions run: `33333446575` — success
- Verified v1.0.12 prerelease tag: `va-android-112-2-1`
- v1.0.12 release identity: backend `1.0.12`, Android `1.0.12+55`
- Historical v1.0.11 evidence remains preserved: source `221205e82444f9c0bff2589cf3ffc015408e664a`, GitHub Actions run `33331650005`, tag `va-android-111-2-1`.

The operator subsequently reported production deployment and phone smoke testing complete for v1.0.12.

## v1.0.13 maintenance scope

v1.0.13 keeps ambiguous Gmail provider delivery under continuous VA ownership instead of abandoning it after an arbitrary thirty-minute window.

- A possibly accepted Gmail send is never automatically submitted a second time.
- The deterministic RFC Message-ID remains the stable provider evidence and idempotency key.
- Fresh ambiguity is reconciled every two minutes, then backs off to fifteen minutes, one hour, and six hours for long-lived uncertainty.
- Elapsed time alone never converts `creation_uncertain` or `sent_unverified` into terminal failure.
- Late Gmail Sent evidence can complete the original durable objective after the old thirty-minute boundary.
- Provider verification outages preserve VA-owned uncertainty and do not create Needs You work.
- Historical `failed_uncertain` rows are migrated back to `creation_uncertain` reconciliation-only state.
- Historical Gmail objective steps failed solely by the old ambiguity cutoff are reopened as `verifying`.
- Definitive Gmail request failures remain system failures.
- Genuine Gmail authentication/authorization remains the existing `failed_user` human boundary.
- No database schema migration is required.

## Release identity

- Backend: `1.0.13`
- Required Android: `1.0.13`
- Android: `1.0.13+56`

This status file is committed only by the guarded v1.0.13 installer after backend tests, Ruff gates, Flutter analysis/tests, Android signing checks, and the signed release APK build have passed. GitHub prerelease publication remains a separate final workflow step and must be independently verified after the run.
