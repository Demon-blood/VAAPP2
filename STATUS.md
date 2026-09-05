# VAAPP v1.0.17 — Investment Side-Effect Recovery & Human Boundary Integrity

Updated: 2026-09-05

## Source of truth

- Repository: `Demon-blood/VAAPP2`
- Branch: `main`
- Verified v1.0.16 source baseline: `830c2c87b89972bc0735028584285f2827ac4bf9`
- Verified v1.0.16 GitHub Actions run: `33975481668` — success
- Verified v1.0.16 prerelease tag: `va-android-116-3-1`
- v1.0.16 release identity: backend `1.0.16`, Android `1.0.16+59`
- v1.0.16 APK SHA-256: `caf9810e4ae1c8bd9db2d9e91222ace01265bd67b7dfdfaa0b472f24787ad622`
- Historical v1.0.15 evidence: source `2b48b72e720a2e515e346fed253e24c131ae078a`, GitHub Actions run `33967944880`, tag `va-android-115-3-1`.
- Historical v1.0.14 evidence: source `8557dd449db554528ab7e111d0029faf784c996f`, GitHub Actions run `33961135886`, tag `va-android-114-3-1`.
- Historical v1.0.13 evidence: source `ecaa113d4461a550cb49c6046a42ecf880729346`, GitHub Actions run `33434347111`, tag `va-android-113-4-1`.
- Historical v1.0.12 evidence: source `22a392f1341ef19caf8a761cd7bfa44000fdc08c`, GitHub Actions run `33333446575`, tag `va-android-112-2-1`.
- Historical v1.0.11 evidence: source `221205e82444f9c0bff2589cf3ffc015408e664a`, GitHub Actions run `33331650005`, tag `va-android-111-2-1`.

The operator subsequently reported production deployment and phone smoke testing complete for v1.0.16.

## v1.0.17 maintenance scope

- Kraken funding creation ambiguity stays VA-owned and never creates a fake bank-check approval task.
- Unbound funding authorization URLs are suppressed; genuine SCA remains user-bound only when an external payment id exists.
- Unique booked debit evidence from the exact source account can recover the original funding intent without a replacement payment.
- A booked bank transaction can recover only one investment funding intent.
- Automatic Kraken orders persist a durable client-order intent before AddOrder.
- Kraken OpenOrders and ClosedOrders reconcile ambiguous AddOrder outcomes by the original `cl_ord_id`.
- Automatic trading requires query-open, query-closed, and modify-trades permissions so provider ambiguity is recoverable before any order is placed.
- Network/provider ambiguity or a missing order id never authorizes a second AddOrder.
- Historical `trade_pending` rows are reconciliation-only against the legacy client-order identifier.

## Release identity

- Backend: `1.0.17`
- Required Android: `1.0.17`
- Android: `1.0.17+60`

Source publication remains gated by backend tests, Ruff, Flutter analysis/tests, Android signing, and the signed APK build.
