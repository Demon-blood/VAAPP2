# VAAPP v1.0.20 — Drive Archive Folder Creation Recovery & Staged Upload Continuity

Updated: 2026-09-05

## Source of truth

- Repository: `Demon-blood/VAAPP2`
- Branch: `main`
- Verified v1.0.19 source baseline: `41ecf4ae69f9750cac821688df9fb9b9ee1213c6`
- Verified v1.0.19 GitHub Actions run: `33989785817` — success
- Verified v1.0.19 prerelease tag: `va-android-119-1-1`
- v1.0.19 release identity: backend `1.0.19`, Android `1.0.19+62`
- v1.0.19 APK SHA-256: `dec470cc1deacdb17cd1758191b984f1e17f194c5098a929d229efaee4d7a9d8`
- Historical v1.0.18 evidence: source `b0005392a799bc5466a5e77febfd34035fb26ce3`, GitHub Actions run `33986405236`, tag `va-android-118-3-1`.
- Historical v1.0.17 evidence: source `251e2e5a67ba137d2ac7b445a719d4be487df9fc`, GitHub Actions run `33981261146`, tag `va-android-117-2-1`.
- Historical v1.0.16 evidence: source `830c2c87b89972bc0735028584285f2827ac4bf9`, GitHub Actions run `33975481668`, tag `va-android-116-3-1`.
- Historical v1.0.15 evidence: source `2b48b72e720a2e515e346fed253e24c131ae078a`, GitHub Actions run `33967944880`, tag `va-android-115-3-1`.
- Historical v1.0.14 evidence: source `8557dd449db554528ab7e111d0029faf784c996f`, GitHub Actions run `33961135886`, tag `va-android-114-3-1`.
- Historical v1.0.13 evidence: source `ecaa113d4461a550cb49c6046a42ecf880729346`, GitHub Actions run `33434347111`, tag `va-android-113-4-1`.
- Historical v1.0.12 evidence: source `22a392f1341ef19caf8a761cd7bfa44000fdc08c`, GitHub Actions run `33333446575`, tag `va-android-112-2-1`.
- Historical v1.0.11 evidence: source `221205e82444f9c0bff2589cf3ffc015408e664a`, GitHub Actions run `33331650005`, tag `va-android-111-2-1`.

The operator subsequently reported production deployment and phone smoke testing complete for v1.0.19.

## v1.0.20 maintenance scope

- Drive archive folder creation has a durable path-keyed intent ledger.
- Each cumulative archive folder path is reconciled before a new folder create is claimed.
- Folder creates carry stable VA path properties for read-only provider recovery.
- Legacy same-name folders under the exact parent can be adopted without another provider mutation.
- A folder create is one-shot after an atomic `prepared -> submitting` claim.
- `submitting` and `creation_uncertain` folder intents are reconciliation-only and never replay automatically.
- The full folder path resolves before the exact-byte file intent can enter `submitting`.
- Folder ambiguity leaves the file intent `prepared` with zero file-dispatch attempts.
- Existing v1.0.18 file ambiguity remains fail-closed and is never blindly reopened.
- Drive provider ambiguity remains VA-owned and creates no fake Needs You work.

## Release identity

- Backend: `1.0.20`
- Required Android: `1.0.20`
- Android: `1.0.20+63`

Source publication remains gated by backend tests, Ruff, Flutter analysis/tests, Android signing, and the signed APK build.
