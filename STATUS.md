# VAAPP v1.0.12 — Telephony Creation Recovery & Retry Integrity

Updated: 2026-08-30

## Source of truth

- Repository: `Demon-blood/VAAPP2`
- Branch: `main`
- Verified v1.0.11 source baseline: `221205e82444f9c0bff2589cf3ffc015408e664a`
- Verified v1.0.11 GitHub Actions run: `33331650005` — success
- Verified v1.0.11 prerelease tag: `va-android-111-2-1`
- v1.0.11 release identity: backend `1.0.11`, Android `1.0.11+54`

The operator subsequently reported production deployment and phone smoke testing complete for v1.0.11.

## v1.0.12 maintenance scope

v1.0.12 recovers ambiguous outbound Twilio call creation without ever converting uncertainty into a blind redial.

- A lost Twilio create response remains `creation_uncertain` and VA-owned.
- VAAPP queries the authenticated Twilio Calls resource for exact To/From provider evidence.
- Twilio's day-level filters are narrowed locally by exact normalized numbers, `outbound-api` direction, and a ten-minute durable creation-time window.
- A candidate CallSid already bound to another durable call intent is excluded.
- Exactly one candidate is required before VAAPP binds a missing CallSid.
- Zero candidates remain unresolved without a retry.
- Multiple candidates remain unresolved without guessing.
- Provider lookup failure remains a system-owned verification issue and creates no Needs You work.
- A retry child can be created only after the previous call has a real CallSid and a terminal Twilio provider status.
- Existing material payment/legal/medical/binding/authentication boundaries during the conversation are unchanged.
- No database schema migration is required; recovery uses the existing TelephonyEvidence ledger.

## Release identity

- Backend: `1.0.12`
- Required Android: `1.0.12`
- Android: `1.0.12+55`

This status file is committed only by the guarded v1.0.12 installer after backend tests, Ruff gates, Flutter analysis/tests, Android signing checks, and the signed release APK build have passed. GitHub prerelease publication remains a separate final workflow step and must be independently verified after the run.
