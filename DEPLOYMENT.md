# Full-Time VA v0.7.0 deployment

This package is a complete deployable source baseline for backend `0.7.0` and Android `0.7.0+28`, built over the working VAAPP2 source at repository commit `00572687e7fe18ea63f59a73249663ebed5be0ca`.

## Upload

Replace the matching repository paths with the contents of this package and commit them to `main`. Do not copy cache directories. The GitHub Actions workflow validates the backend, installs Flutter, analyzes/tests the Android app, builds the signed release APK, and publishes the prerelease artifact.

## Backend database

v0.7.0 adds new tables for bank transactions, budget envelopes, bank-autopilot policies, own-account transfers, communication events/actions/rules. Existing tables are not altered by this release, so the current SQLAlchemy `metadata.create_all()` startup path creates the new tables without destructive migration of existing PostgreSQL rows.

## First start

The backend will continue its durable workflow recovery and will also:

- reconcile already-processed routine Gmail messages still left in Inbox;
- periodically move only read, aged, safely low-value mail to Gmail Trash;
- quarantine stale money-movement creation intents instead of retrying an uncertain provider POST;
- sync connected bank transactions and run the budgeting/own-account-transfer engine through the existing banking Autopilot schedule.

## Android one-time authorization

After installing the v0.7.0 APK, open **Services → Communications Autopilot** and grant the Android permissions/roles you want the VA to use: SMS, Notification Access, Call Screening, Contacts/Call Log and notifications. Android requires the user to approve these system dialogs.

## Financial Autopilot

Automatic own-account transfers require the source account to be enabled for payment execution and its account policy to allow internal transfers. The backend still obeys provider/bank SCA when required. Runtime defaults include a EUR 1,000 minimum operating-account floor, EUR 1,000 maximum single automatic own-account transfer, and EUR 1,000 daily automatic own-account transfer limit; these are configurable after deployment.

## Validation boundary

See `docs/V0.7.0_VALIDATION.md`. Local source/contract validation is included in the package. Full dependency-backed pytest, Flutter analysis/tests and the signed Android build are authoritative in GitHub Actions because the packaging sandbox does not contain the complete Flutter/Android/backend dependency toolchain.
