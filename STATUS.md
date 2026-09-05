# VAAPP v1.0.14 — Own-Account Transfer Recovery & Human Boundary Integrity

Updated: 2026-08-31

## Source of truth

- Repository: `Demon-blood/VAAPP2`
- Branch: `main`
- Verified v1.0.13 source baseline: `ecaa113d4461a550cb49c6046a42ecf880729346`
- Verified v1.0.13 GitHub Actions run: `33434347111` — success
- Verified v1.0.13 prerelease tag: `va-android-113-4-1`
- v1.0.13 release identity: backend `1.0.13`, Android `1.0.13+56`
- Historical v1.0.12 evidence: source `22a392f1341ef19caf8a761cd7bfa44000fdc08c`, GitHub Actions run `33333446575`, tag `va-android-112-2-1`.
- Historical v1.0.11 evidence: source `221205e82444f9c0bff2589cf3ffc015408e664a`, GitHub Actions run `33331650005`, tag `va-android-111-2-1`.

The operator subsequently reported production deployment and phone smoke testing complete for v1.0.13.

## v1.0.14 maintenance scope

v1.0.14 makes ambiguous own-account transfer creation a VA-owned reconciliation problem instead of fake user work.

- Network loss after provider submission does not create an approval task.
- A provider response without a payment ID does not expose an unbound authorization URL.
- `creation_uncertain` keeps `requires_user_action = false`.
- The original transfer and idempotency key remain active; automatic creation is never repeated.
- VAAPP checks booked transactions on the exact source account for independent evidence.
- Recovery requires debit direction, exact amount/currency, exact destination IBAN, a booking date within four days, a provider transaction ID, and exactly one candidate.
- One unique candidate marks the existing transfer completed from booked-bank evidence.
- Zero or multiple candidates stay VA-owned and unresolved.
- Provider reconciliation outages stay VA-owned.
- Historical `bank_transfer_uncertain` tasks are closed automatically.
- Genuine authorization remains a human boundary only when a real provider payment ID and authorization URL are bound together.
- No database schema migration is required.

## Release identity

- Backend: `1.0.14`
- Required Android: `1.0.14`
- Android: `1.0.14+57`

This status file is committed only by the guarded v1.0.14 installer after backend tests, Ruff gates, Flutter analysis/tests, Android signing checks, and the signed release APK build have passed. GitHub prerelease publication remains a separate final workflow step and must be independently verified after the run.
