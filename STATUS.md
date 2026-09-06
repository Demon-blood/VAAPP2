# VAAPP v1.0.19 — Scheduled Connector Mutation Claim & Retry Integrity

Updated: 2026-09-05

## Source of truth

- Repository: `Demon-blood/VAAPP2`
- Branch: `main`
- Verified v1.0.18 source baseline: `b0005392a799bc5466a5e77febfd34035fb26ce3`
- Verified v1.0.18 GitHub Actions run: `33986405236` — success
- Verified v1.0.18 prerelease tag: `va-android-118-3-1`
- v1.0.18 release identity: backend `1.0.18`, Android `1.0.18+61`
- v1.0.18 APK SHA-256: `93aeddafa680ed4cf4b729fadd6401cad6af2f5240ec4cddaa8a355d3e862558`
- Historical v1.0.17 evidence: source `251e2e5a67ba137d2ac7b445a719d4be487df9fc`, GitHub Actions run `33981261146`, tag `va-android-117-2-1`.
- Historical v1.0.16 evidence: source `830c2c87b89972bc0735028584285f2827ac4bf9`, GitHub Actions run `33975481668`, tag `va-android-116-3-1`.
- Historical v1.0.15 evidence: source `2b48b72e720a2e515e346fed253e24c131ae078a`, GitHub Actions run `33967944880`, tag `va-android-115-3-1`.
- Historical v1.0.14 evidence: source `8557dd449db554528ab7e111d0029faf784c996f`, GitHub Actions run `33961135886`, tag `va-android-114-3-1`.
- Historical v1.0.13 evidence: source `ecaa113d4461a550cb49c6046a42ecf880729346`, GitHub Actions run `33434347111`, tag `va-android-113-4-1`.
- Historical v1.0.12 evidence: source `22a392f1341ef19caf8a761cd7bfa44000fdc08c`, GitHub Actions run `33333446575`, tag `va-android-112-2-1`.
- Historical v1.0.11 evidence: source `221205e82444f9c0bff2589cf3ffc015408e664a`, GitHub Actions run `33331650005`, tag `va-android-111-2-1`.

The operator subsequently reported production deployment and phone smoke testing complete for v1.0.18.

## v1.0.19 maintenance scope

- Scheduled connector writes have a durable per-occurrence mutation ledger.
- REST/OAuth writes, webhooks, Telegram sends, SMTP sends, uploads, and arbitrary Browserless work are non-replay-safe.
- One worker must win an atomic `prepared -> submitting` claim before provider dispatch.
- The claim and scheduled timestamp are committed before the external mutation.
- Any post-claim provider exception becomes `execution_uncertain`.
- `execution_uncertain` occurrences never enter ordinary transient workflow replay.
- Ambiguous connector writes remain VA-owned and create no fake Needs You work.
- Read-only connector rules retain normal bounded transient retry behavior.
- Pre-v1.0.19 connector-rule retry/dead-letter/running jobs are quarantined once at startup.
- Later scheduled interval buckets remain independent occurrences and continue normally.

## Release identity

- Backend: `1.0.19`
- Required Android: `1.0.19`
- Android: `1.0.19+62`

Source publication remains gated by backend tests, Ruff, Flutter analysis/tests, Android signing, and the signed APK build.
