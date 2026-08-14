# VAAPP project handoff

Updated: 2026-08-14  
Repository: `Demon-blood/VAAPP2`  
Branch: `main`

## Verified source of truth

Phases 1–10 and production v1.0 are complete on GitHub. The verified v1.0 baseline is commit `66c09040326ac553a1402cd06fa6771344195d45` (`Phase 10 — Professional Product Cleanup / v1.0`). GitHub Actions run #41 completed successfully, including the full backend test suite, Flutter analysis/tests, persistent-signing Android release build, and GitHub prerelease publication.

Verified baseline release: backend `1.0.0` / Android `1.0.0+42`.

## Current local candidate

Backend `1.0.2` / Android `1.0.2+44`.

Current maintenance release: **v1.0.2 — Maintenance Reliability**.  
Status: **implemented locally from the verified v1.0.1 baseline; awaiting upload and full GitHub CI**.

The patch normalizes Android UTC timestamps at the PostgreSQL persistence boundary, isolates failed batch records, and prevents Android from clearing encrypted queued events after a partial backend failure. It also makes configured fulfillment providers and browser portals editable from Android and adds source-backed order hygiene so payment receipts do not become parcel-tracking objectives. The v1.0.1 durable inbound outbox and communication diagnostics remain intact.

Next work after the v1.0.2 gate is green: **v1.x maintenance and real-world hardening**.

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

## v1.0.2 maintenance reliability

- Native SMS events are encrypted to a local outbox before backend dispatch. Temporary Render/network failure is retried by WorkManager; a failed HTTP call is no longer silent.
- `Sync SMS/call history & policies now` returns visible counts and errors instead of discarding the native result.
- RECEIVE_SMS is part of the displayed permission health. Native backend-link errors and locally queued inbound-event counts are visible in Communications Autopilot.
- History imports are sent in bounded chunks and do not consume one AI decision call per historical record.
- Google Messages/Samsung Messages notifications are accepted through the notification listener for RCS/message visibility where Android exposes a notification. Provider chat-history scraping is not claimed.
- Configured fulfillment providers can be edited, enabled/disabled, relinked to a named Secure Browser portal, and have their provider recipe/support number updated without recreating the provider.
- Secure Browser portals can be edited in place, including URLs, allowlisted hosts, scope and enabled state; leaving credentials blank preserves the encrypted credentials already stored.
- `OrderRecord` is no longer sufficient evidence by itself for logistics ownership. Source-backed shipping/delivery/pickup/tracking evidence is required, with explicit handling for Google payment/app-store receipts and GPA-style payment identifiers.
- Existing false-positive logistics objectives are deterministically dismissed during fulfillment reconciliation/read, and the Android Orders/Fulfillment screens expose **Not an order** for manual correction without deleting the original receipt/email evidence.

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

## v1.0.2 release gate

1. Overlay the v1.0.2 maintenance package at repository root.
2. Commit the extracted files, not the ZIP.
3. Push to `main` and allow the Render backend auto-deploy to reach 1.0.2.
4. Confirm the full GitHub Actions workflow is green and install the signed Android 1.0.2 APK.
5. In Communications Autopilot, run the phone sync and verify the reported scan/accepted/error counts.

Suggested commit message: `v1.0.2 — Maintenance Reliability`.
