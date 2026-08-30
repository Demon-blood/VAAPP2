# VAAPP v1.0.11 — Fulfillment Side-Effect Recovery & Duplicate Suppression

Updated: 2026-08-30

## Source of truth

- Repository: `Demon-blood/VAAPP2`
- Branch: `main`
- Verified v1.0.10 source baseline: `4b3b38903545c8598695660c666c3080aff171e2`
- Verified v1.0.10 GitHub Actions run: `33328116694` — success
- Verified v1.0.10 prerelease tag: `va-android-110-4-1`
- v1.0.10 release identity: backend `1.0.10`, Android `1.0.10+53`

The operator subsequently reported production deployment and phone smoke testing complete for v1.0.10.

## v1.0.11 maintenance scope

v1.0.11 prevents duplicate provider mutations when a browser action succeeds but its confirmation/postcondition is delayed or temporarily unverifiable.

- A non-replay-safe side-effect marker persists until explicit provider postcondition verification succeeds.
- Final postcondition failure after a possible side effect becomes `creation_uncertain`, never ordinary `failed`.
- The original FulfillmentAction and browser operation are retained; no new business idempotency key is created while the outcome is uncertain.
- Uncertain operations resume with a new workflow resume sequence in verification-only mode.
- Verification-only recovery may authenticate to the provider, but it checks the postcondition before any original business recipe step can execute.
- A still-missing postcondition remains VA-owned with another verification check scheduled.
- Security boundaries, provider timeouts, and runtime errors during verification-only recovery preserve uncertainty and cannot reopen recipe replay.
- Unsafe or structurally unrecoverable uncertainty becomes `blocked_system`, not Needs You.
- Genuine CAPTCHA/OTP/security authentication remains an explicit human boundary through the existing browser auth path.
- No database schema migration is required.

## Release identity

- Backend: `1.0.11`
- Required Android: `1.0.11`
- Android: `1.0.11+54`

This status file is committed only by the guarded v1.0.11 installer after backend tests, Ruff gates, Flutter analysis/tests, Android signing checks, and the signed release APK build have passed. GitHub prerelease publication remains a separate final workflow step and must be independently verified after the run.
