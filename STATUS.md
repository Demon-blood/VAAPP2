# VAAPP v1.0.15 — Generic Browser Late-Evidence Recovery & Objective Continuity

Updated: 2026-09-05

## Source of truth

- Repository: `Demon-blood/VAAPP2`
- Branch: `main`
- Verified v1.0.14 source baseline: `8557dd449db554528ab7e111d0029faf784c996f`
- Verified v1.0.14 GitHub Actions run: `33961135886` — success
- Verified v1.0.14 prerelease tag: `va-android-114-3-1`
- v1.0.14 release identity: backend `1.0.14`, Android `1.0.14+57`
- v1.0.14 APK SHA-256: `1fae494cb449c48a65997f80709b9404a533b76297144a713f0b574f05d2d4c2`
- Historical v1.0.13 evidence: source `ecaa113d4461a550cb49c6046a42ecf880729346`, GitHub Actions run `33434347111`, tag `va-android-113-4-1`.
- Historical v1.0.12 evidence: source `22a392f1341ef19caf8a761cd7bfa44000fdc08c`, GitHub Actions run `33333446575`, tag `va-android-112-2-1`.
- Historical v1.0.11 evidence: source `221205e82444f9c0bff2589cf3ffc015408e664a`, GitHub Actions run `33331650005`, tag `va-android-111-2-1`.

The operator subsequently reported production deployment and phone smoke testing complete for v1.0.14.

## v1.0.15 maintenance scope

v1.0.15 keeps generic secure-browser side-effect uncertainty under active VA-owned provider reconciliation instead of terminalizing the objective step.

- `creation_uncertain` is resumed only when the durable v1.0.11 side-effect marker proves postcondition-only reconciliation is safe.
- The same BrowserOperation is reused; no second operation or provider mutation is created.
- Provider postconditions are checked before any original business recipe step can execute again.
- Safe uncertainty keeps the objective step `verifying` and clears its historical `finished_at` marker.
- Historical failed generic browser steps are reopened automatically when their linked operation is still safely reconcilable.
- Markerless uncertainty remains `blocked_system`; replay safety is never guessed.
- Genuine portal authentication remains a real Needs You boundary.
- Definitive browser failure remains system-owned.
- Document/form obligations show active uncertainty as `in_progress` and complete only from verified browser evidence.
- No database schema migration is required.

## Release identity

- Backend: `1.0.15`
- Required Android: `1.0.15`
- Android: `1.0.15+58`

This status file is committed only by the guarded v1.0.15 installer after backend tests, Ruff gates, Flutter analysis/tests, Android signing checks, and the signed release APK build have passed. GitHub prerelease publication remains a separate final workflow step and must be independently verified after the run.
