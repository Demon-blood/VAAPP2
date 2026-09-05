# VAAPP v1.0.18 — Drive Archive Upload Recovery & Exact-Byte Duplicate Suppression

Updated: 2026-09-05

## Source of truth

- Repository: `Demon-blood/VAAPP2`
- Branch: `main`
- Verified v1.0.17 source baseline: `251e2e5a67ba137d2ac7b445a719d4be487df9fc`
- Verified v1.0.17 GitHub Actions run: `33981261146` — success
- Verified v1.0.17 prerelease tag: `va-android-117-2-1`
- v1.0.17 release identity: backend `1.0.17`, Android `1.0.17+60`
- v1.0.17 APK SHA-256: `caf1c2e41efe1abccd96dd6699efa0f7b50323093f1c09ed1a1397e1da9832fc`
- Historical v1.0.16 evidence: source `830c2c87b89972bc0735028584285f2827ac4bf9`, GitHub Actions run `33975481668`, tag `va-android-116-3-1`.
- Historical v1.0.15 evidence: source `2b48b72e720a2e515e346fed253e24c131ae078a`, GitHub Actions run `33967944880`, tag `va-android-115-3-1`.
- Historical v1.0.14 evidence: source `8557dd449db554528ab7e111d0029faf784c996f`, GitHub Actions run `33961135886`, tag `va-android-114-3-1`.
- Historical v1.0.13 evidence: source `ecaa113d4461a550cb49c6046a42ecf880729346`, GitHub Actions run `33434347111`, tag `va-android-113-4-1`.
- Historical v1.0.12 evidence: source `22a392f1341ef19caf8a761cd7bfa44000fdc08c`, GitHub Actions run `33333446575`, tag `va-android-112-2-1`.
- Historical v1.0.11 evidence: source `221205e82444f9c0bff2589cf3ffc015408e664a`, GitHub Actions run `33331650005`, tag `va-android-111-2-1`.

The operator subsequently reported production deployment and phone smoke testing complete for v1.0.17.

## v1.0.18 maintenance scope

- Exact-byte Drive archive upload intent is durable before provider mutation.
- One intent exists per SHA-256 and account scope.
- Fresh upload dispatch requires an atomic `prepared -> submitting` claim.
- Drive app properties provide independent checksum/scope recovery evidence.
- `submitting` and `creation_uncertain` uploads are reconciliation-only.
- Retry, restart, or elapsed time never authorizes another Drive create.
- Historical orphan Drive files bind without another upload.
- Historical exact-byte duplicates bind the oldest observed copy without a new mutation.
- Drive ambiguity remains VA-owned and creates no fake Needs You work.

## Release identity

- Backend: `1.0.18`
- Required Android: `1.0.18`
- Android: `1.0.18+61`

Source publication remains gated by backend tests, Ruff, Flutter analysis/tests, Android signing, and the signed APK build.
