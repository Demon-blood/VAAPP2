# VAAPP v1.0.10 — Payment Recovery & Human Boundary Integrity

Updated: 2026-08-30

## Source of truth

- Repository: `Demon-blood/VAAPP2`
- Branch: `main`
- Verified v1.0.9 source baseline: `12afd780fdeb83fe89f0a6c3010d268dde683103`
- Verified v1.0.9 GitHub Actions run: `33323619938` — success
- Verified v1.0.9 prerelease tag: `va-android-109-4-1`
- v1.0.9 release identity: backend `1.0.9`, Android `1.0.9+52`

The operator subsequently reported production deployment and phone smoke testing complete for v1.0.9.

## v1.0.10 maintenance scope

v1.0.10 keeps ambiguous payment-creation outcomes VA-owned instead of manufacturing a human approval boundary.

- Network/timeout uncertainty after a bank payment POST remains `creation_uncertain` with automatic duplicate retry suppressed.
- A provider response without a payment identifier also remains VA-owned; an unbound authorization URL is not surfaced as a valid SCA action.
- The VA reconciles uncertain payment creation against booked transactions from the exact source bank account.
- Completion requires exactly one provider-backed transaction matching amount, currency, timing, and strong creditor/reference evidence.
- Zero or multiple candidate transactions remain unresolved and VA-owned.
- Recovery creates durable `PaymentRecoveryEvidence` before treating the bill as paid.
- Legacy `payment_creation_uncertain` human tasks are closed by reconciliation rather than kept in Needs You.
- Genuine bank authorization remains human-bound only when a real provider authorization URL is attached to a provider payment identifier.
- The Operational Guardian counts unresolved payment creation uncertainty as a system issue, not Needs You.
- Anti-double-payment behavior is unchanged: an uncertain payment stays active, so a second automatic payment is not submitted while evidence is unresolved.

## Release identity

- Backend: `1.0.10`
- Required Android: `1.0.10`
- Android: `1.0.10+53`

This status file is committed only by the guarded v1.0.10 installer after backend tests, Ruff gates, Flutter analysis/tests, Android signing checks, and the signed release APK build have passed. GitHub prerelease publication remains a separate final workflow step and must be independently verified after the run.
