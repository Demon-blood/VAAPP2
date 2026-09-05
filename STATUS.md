# VAAPP v1.0.16 — Device Communication Dispatch Claim & Late-Evidence Continuity

Updated: 2026-09-05

## Source of truth

- Repository: `Demon-blood/VAAPP2`
- Branch: `main`
- Verified v1.0.15 source baseline: `2b48b72e720a2e515e346fed253e24c131ae078a`
- Verified v1.0.15 GitHub Actions run: `33967944880` — success
- Verified v1.0.15 prerelease tag: `va-android-115-3-1`
- v1.0.15 release identity: backend `1.0.15`, Android `1.0.15+58`
- v1.0.15 APK SHA-256: `19165c0c6a531a9bf8545ea9ccf6672f35e269d32b1f896a4d5dcb7f5856360d`
- Historical v1.0.14 evidence: source `8557dd449db554528ab7e111d0029faf784c996f`, GitHub Actions run `33961135886`, tag `va-android-114-3-1`.
- Historical v1.0.13 evidence: source `ecaa113d4461a550cb49c6046a42ecf880729346`, GitHub Actions run `33434347111`, tag `va-android-113-4-1`.
- Historical v1.0.12 evidence: source `22a392f1341ef19caf8a761cd7bfa44000fdc08c`, GitHub Actions run `33333446575`, tag `va-android-112-2-1`.
- Historical v1.0.11 evidence: source `221205e82444f9c0bff2589cf3ffc015408e664a`, GitHub Actions run `33331650005`, tag `va-android-111-2-1`.

The operator subsequently reported production deployment and phone smoke testing complete for v1.0.15.

## v1.0.16 maintenance scope

- Background device communication is atomically claimed on the backend before carrier dispatch, with durable claim ownership bound to the paired device in an additive claim table.
- The first `pending -> dispatching` claim creates ownership; the same device can idempotently re-assert it after a lost response, while another device is denied.
- Claimed SMS actions remain visible for evidence reconciliation and same-device claim recovery; the synchronously durable local marker prevents a prior send from being replayed.
- Existing Android stored-evidence reconciliation remains before any new provider send.
- Local `action_done_<id>` protection is made synchronously durable before `SmsManager` is called.
- Missing evidence no longer becomes terminal solely because 30 minutes elapsed.
- Verification cadence backs off while the same action remains VA-owned.
- Historical steps failed by the old elapsed-time cutoff reopen without creating a replacement action.
- Late device evidence completes the original objective.
- Definitive replay-safe device failure can release the claim for safe retry; multipart partial-send ambiguity remains reconciliation-only. Provider/system uncertainty never becomes fake Needs You work.

## Release identity

- Backend: `1.0.16`
- Required Android: `1.0.16`
- Android: `1.0.16+59`

Source publication remains gated by backend tests, Ruff, Flutter analysis/tests, Android signing, and the signed APK build.
