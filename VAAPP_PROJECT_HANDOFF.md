# VAAPP project handoff

Updated: 2026-08-13  
Repository: `Demon-blood/VAAPP2`  
Branch: `main`

## Verified source of truth

Phases 1–9 are complete on GitHub. The verified Phase-9 baseline is commit `7e8be1f82cb86c66ae07b2b90fe2173858757aa7` (`Phase 9 CI fix`). GitHub Actions run #40 completed successfully on 2026-08-13, including the full backend test suite, Flutter analysis/tests, persistent-signing Android release build, and GitHub prerelease publication.

Verified baseline release: backend `0.9.8` / Android `0.9.8+41`.

## Current local candidate

Backend `1.0.0` / Android `1.0.0+42`.

Current phase: **Phase 10 — Professional Product Cleanup / v1.0**.  
Status: **implemented locally from the verified Phase-9 baseline; awaiting upload and full GitHub CI**.

Next work after the v1.0 gate is green: **v1.x maintenance and real-world hardening**.

## Product objective

VAAPP is a real autonomous full-time virtual assistant, not a chatbot, approval queue, paper-mode simulator, or placeholder executor.

Operating lifecycle:

`Observe -> Understand -> Own objective -> Check policy/capability -> Plan -> Execute -> Verify -> Follow up/recover -> Reconcile -> Complete -> Learn`

Routine reversible work executes automatically. **Needs You** is reserved for unavoidable provider authentication/security, genuinely material decisions without valid standing/specific authorization, or physical/manual work without a real executor.

## Completed phases

1. **Autonomous Core** — durable objectives, steps, evidence, policy/capability decisions, workflow execution and recovery.
2. **Inbox & Communications Ownership** — durable Gmail ownership, ambiguity-safe sends, SMS/RemoteInput evidence and persistent follow-up.
3. **Calendar & Scheduling Agent** — Google Calendar mirror/mutations, conflict checks, deterministic IDs, provider verification and attendee-response ownership.
4. **CRM / Relationship Memory** — source-backed identities/facts, safe merging, cross-channel interaction timelines and follow-up projection.
5. **Secure Browser / Portal Operator** — allowlisted Chromium execution, encrypted sessions/credentials, MFA/CAPTCHA handoff, ambiguity-safe side effects and postcondition evidence.
6. **Documents / Forms / Deadlines** — source-backed document intelligence, exact deadlines, encrypted facts, durable form intents and provider-verified completion.
7. **Financial Allocation & Forecasting** — conservative cash forecasts, protected floors, same-scope surplus allocations and real bank-transfer verification.
8. **Calls / Telephony** — real Twilio PSTN calls, signed callbacks, encrypted call/turn/evidence ledgers, ambiguity-safe creation, bounded voice interaction and counterparty-verified objective completion.
9. **Purchasing / Travel / Logistics / Customer Service** — durable fulfillment ledger, allowlisted browser/Twilio execution, bounded standing payment authority, order/support reconciliation, and provider-verified terminal evidence. CI green on run #40 after the metadata import-order compatibility fix.

## Phase 10 v1.0 hardening

### Stable compatibility contract

Backend and Android move to `1.0.0` / `1.0.0+42`. The backend reports `REQUIRED_ANDROID_VERSION = 1.0.0`, and Android uses one `release_contract.dart` source for the app release and minimum backend version. AppState, Product Status, phone provisioning, and deployment health verification use the same minimum.

### Phone deployment correctness

The Render deployment verifier and onboarding flow no longer use the historical `0.4.16` floor. A newly deployed/repaired backend must expose `/health` and `/api/system/info` with backend `1.0.0` or newer before pairing continues. Existing database and encryption-key preservation behavior remains unchanged.

### Product status / diagnostics

Android exposes **Product Status** from the main app bar. It reports release compatibility, Autopilot health, endpoint failures, Needs You count, verified executor availability, and unresolved capability/setup gaps. A configured provider or dispatched operation is never displayed as proof that an objective completed.

### Completion contract

No Phase-10 feature relaxes the core evidence rule. Provider/browser/call/payment intent can be `initiated`, `dispatching`, `creation_uncertain`, `needs_user`, `blocked_capability`, or `blocked_system`; terminal completion still requires the relevant domain's independent provider/source postcondition.

## Finance constraints remain mandatory

- Beobank Personal = operating/salary/critical obligations.
- Revolut Personal = personal spending + Revolut investment funding.
- Revolut Pro = pro/Uber operating.
- Revolut Pro never directly funds personal Kraken.
- Pro -> Personal = owner draw.
- Personal -> Pro = owner contribution.
- Personal -> Kraken = investment contribution.
- Safety/emergency reserve is never investable.
- Critical Beobank standing orders remain; VA predicts/reserves/verifies.
- Revolut native scheduled investment contributions remain; VA prefunds/reconciles.
- Kraken auto funding/trading remains off until explicitly configured.
- Kraken withdrawals remain disabled/not implemented.

## v1.0 release gate

1. Overlay the Phase-10 package at repository root.
2. Commit the extracted files, not the ZIP.
3. Push to `main`.
4. Confirm the full GitHub Actions workflow is green.
5. If CI fails, fix v1.0 before declaring the roadmap complete.

Suggested commit message: `Phase 10 — Professional Product Cleanup / v1.0`.
