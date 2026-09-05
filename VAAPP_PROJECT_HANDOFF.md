# VAAPP project handoff

Updated: 2026-09-05
Repository: `Demon-blood/VAAPP2`
Branch: `main`

## Verified source of truth

The verified maintenance baseline for this release is commit `2b48b72e720a2e515e346fed253e24c131ae078a` (`v1.0.15 — Generic Browser Late-Evidence Recovery & Objective Continuity`). GitHub Actions run `33967944880` completed successfully end-to-end with 392 backend tests, Ruff gates, Flutter analysis/tests, Android signing, signed APK build, source verification, and prerelease publication under tag `va-android-115-3-1`.

Verified v1.0.15 release identity: backend `1.0.15` / Android `1.0.15+58`. APK SHA-256: `19165c0c6a531a9bf8545ea9ccf6672f35e269d32b1f896a4d5dcb7f5856360d`. The operator subsequently reported production deployment and phone smoke testing complete.

Historical v1.0.14 source remains `8557dd449db554528ab7e111d0029faf784c996f` with successful Actions run `33961135886` and tag `va-android-114-3-1`. Historical v1.0.13 source remains `ecaa113d4461a550cb49c6046a42ecf880729346` with successful Actions run `33434347111` and tag `va-android-113-4-1`. Historical v1.0.12 source remains `22a392f1341ef19caf8a761cd7bfa44000fdc08c` with successful Actions run `33333446575` and tag `va-android-112-2-1`. Historical v1.0.11 source remains `221205e82444f9c0bff2589cf3ffc015408e664a` with successful Actions run `33331650005` and tag `va-android-111-2-1`.

Original production v1.0 baseline remains commit `66c09040326ac553a1402cd06fa6771344195d45`; GitHub Actions run #41 completed successfully.

## Current maintenance candidate

Backend `1.0.16` / Android `1.0.16+59`.

Current candidate: **v1.0.16 — Device Communication Dispatch Claim & Late-Evidence Continuity**.

v1.0.16 makes a durable paired-device dispatch claim the cross-device at-most-once boundary for device communication actions. Android must claim the original action before SMS carrier dispatch; the same device may idempotently re-assert a lost claim response, another device is denied, local evidence reconciliation still runs before any send, and the local execution marker is synchronously persisted before the provider boundary. Missing callbacks remain VA-owned with backoff instead of terminalizing after 30 minutes, and historical elapsed-time failures can reopen against the same action when late device evidence arrives. Definitive replay-safe device failures may release the claim for safe retry, while multipart partial-send ambiguity stays VA-owned and non-replayable.

The guarded installer commits this candidate only after backend tests, Ruff gates, Flutter analysis/tests, Android signing checks, and a signed release APK build pass.

Next work after the v1.0.16 gate is green: **v1.x maintenance and real-world hardening**.

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

## v1.0.3 logistics tracking ownership

- `FulfillmentObservation` is an additive carrier-state ledger linked to the real fulfillment/browser action.
- Browser provider-page verification and fulfillment outcome verification are separate. A tracking page reached successfully can still leave the objective waiting.
- `pre_transit`, `in_transit`, `out_for_delivery`, `unknown`, `exception` and `returned` remain VA-owned with scheduled rechecks; ordinary logistics completes only on provider-backed `delivered`.
- `available_for_pickup` surfaces as Needs You because physical collection has no software executor, but VAAPP keeps monitoring it.
- Transient read-only tracking failures retry with bounded backoff rather than killing the objective.
- Secure Browser `observe_text_any` stores state-match booleans/hashes without plaintext matched carrier text.
- Fulfillment provider templates include a conservative bpost Track & Trace starter using a source-backed `tracking_url` and the allowlisted `track.bpost.cloud` portal.


## v1.0.4 execution readiness and setup assistant

- Work → Operations capability rows are interactive and expose the exact setup destination plus capability-specific steps instead of only LIVE/OFFLINE text.
- Setup actions can open Services, Communications, Calls, Fulfillment or the Work → Portals tab directly.
- Gmail push readiness requires Google OAuth, a configured Pub/Sub topic, a Pub/Sub verification token, and a non-expired Gmail watch bound to that topic.
- Gmail push uses `READY` for an accepted watch before real push delivery has been observed; `LIVE` is reserved for a working watch with provider delivery evidence.
- **Activate watch** reuses the authenticated `/api/google/watch` endpoint and refreshes the capability matrix; it does not fabricate Pub/Sub delivery evidence.
- Fulfillment readiness is provider-specific: an enabled provider must actually link to an enabled Secure Browser portal or to a configured support phone while real telephony is live.
- Capability setup metadata contains no secret values. Android builds the Pub/Sub callback guidance from the paired backend URL and tells the user to reuse the private token already stored in Services.
- Readiness/configuration remains distinct from objective completion evidence throughout the UI.

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

## v1.0.4 release gate

1. Overlay the v1.0.4 execution-readiness package at repository root.
2. Commit the extracted files, not the ZIP.
3. Push to `main` and allow the Render backend auto-deploy to reach 1.0.4.
4. Confirm the full GitHub Actions workflow is green and install the signed Android 1.0.4 APK.
5. Tap an OFFLINE/READY capability in Work → Operations and verify the setup sheet shows the correct destination and steps.
6. For Gmail push, activate the watch and confirm READY appears before first delivery and LIVE only after a real Pub/Sub notification has been observed.
7. For fulfillment, verify an unrelated enabled portal no longer makes an unlinked provider appear available.

Suggested commit message: `v1.0.4 — Execution Readiness & Setup Assistant`.
