# VAAPP project handoff

Updated: 2026-08-13  
Repository: `Demon-blood/VAAPP2`  
Branch: `main`

## Verified source of truth

Phases 1–7 are complete on GitHub. The Phase-7 CI-fix commit is `c00d70ee4dd20f84f05272de89b09faf953c5c3b`. GitHub Actions run #37 completed successfully on 2026-08-13, including the backend suite, Flutter analysis/tests, signed Android release build, and prerelease publishing.

Verified baseline release: backend `0.9.6` / Android `0.9.6+39`.

## Current local candidate

Backend `0.9.7` / Android `0.9.7+40`.

Current phase: **Phase 8 — Calls / Telephony**.  
Status: **implemented as a cumulative delta from the verified Phase-7 baseline; awaiting GitHub upload and full CI**.

Next phase after the Phase-8 gate is green: **Phase 9 — Purchasing / Travel / Logistics / Customer Service**.

## Product objective

VAAPP must operate as a real autonomous full-time virtual assistant, not a chatbot, approval queue, paper-mode simulator, or placeholder executor.

Operating lifecycle:

`Observe -> Understand -> Own objective -> Check policy/capability -> Plan -> Execute -> Verify -> Follow up/recover -> Reconcile -> Complete -> Learn`

Routine reversible work executes automatically. **Needs You** is reserved for unavoidable authentication/security, genuinely material decisions without safe pre-authorization, or physical/manual work with no real executor.

## Completed phases

1. **Autonomous Core** — durable objectives, steps, evidence, policy/capability decisions, workflow execution and recovery.
2. **Inbox & Communications Ownership** — durable Gmail ownership, ambiguity-safe sends, SMS/RemoteInput evidence, persistent conversation follow-up.
3. **Calendar & Scheduling Agent** — durable Google Calendar mirror/mutations, conflict checks, deterministic provider IDs, provider verification, attendee response ownership.
4. **CRM / Relationship Memory** — source-backed people, identity-safe merging, provenance facts, cross-channel interaction timelines and follow-up projection.
5. **Secure Browser / Portal Operator** — allowlisted Chromium execution, encrypted sessions/credentials, MFA/CAPTCHA handoff, ambiguity-safe side effects and provider postcondition evidence.
6. **Documents / Forms / Deadlines** — source-backed document intelligence, exact deadline ownership, encrypted reusable facts, durable form intents and provider-verified completion.
7. **Financial Allocation & Forecasting** — conservative 30–180 day cash forecasts, protected floors, same-scope surplus allocation plans, durable allocation actions, and real bank-transfer verification. CI green on run #37 after the Phase-7 Flutter-analysis hotfix.

## Phase 8 implementation

### Real PSTN executor

Android `CallScreeningService` remains the inbound phone-screening/logging layer. It does not pretend to conduct calls. Real autonomous conversations use Twilio Programmable Voice.

Outbound call creation is a real Twilio Call-resource POST authenticated with the configured Twilio Account SID/Auth Token. Voice and status control use per-call callback URLs. The system requests initiated/ringing/answered/completed provider callbacks and never enables call recording.

### Durable telephony ledger

Phase 8 introduces additive `TelephonyCall`, `TelephonyTurn`, and `TelephonyEvidence` tables. Call intents carry stable idempotency keys and attempt-series ownership. Phone numbers, purposes, expected outcomes, webhook tokens, summaries, and transcripts are encrypted or hashed at rest as appropriate.

Every outbound intent is persisted before the provider POST. If provider creation becomes ambiguous, the call is `creation_uncertain`, automatic redial is suppressed, and later signed provider callbacks may recover the `CallSid`. Clear busy/no-answer outcomes may schedule a bounded child attempt; an ambiguous create outcome never does.

### Signed call control

The Twilio incoming, voice, speech-turn, and status endpoints validate `X-Twilio-Signature` before mutating durable state. Paired-device call management remains bearer-authenticated.

Speech turns use Twilio speech gathering and dynamic TTS responses. The configured turn count and call duration are hard bounded. A one-minute server reconciliation loop repairs stale intents, refreshes active provider calls, and starts only already-scheduled bounded retries.

### Voice safety and disclosure

The assistant explicitly identifies itself as an automated virtual assistant. Routine information gathering, message taking, status chasing, reference collection, availability checks, and low-risk logistics can be handled autonomously.

Payments, binding contracts/commitments, debt/legal settlements, medical decisions, employment commitments, credential/security changes, identity/authentication steps, and sensitive authentication/card/bank credentials are not accepted by the voice engine. Reaching such a step ends the call and creates a `needs_user` handoff. Later provider-completed callbacks cannot erase that handoff.

### Verification contract

Provider completion and objective completion are separate facts. A Twilio `completed` state does not by itself complete the VA objective. Without source-backed counterparty confirmation, the call remains `provider_completed_unverified` and the objective remains waiting/verifying.

Only explicit counterparty evidence supporting the expected outcome writes `telephony_counterparty_confirmation` evidence and completes the objective.

### Android

A dedicated **Calls** destination shows provider readiness, active/recent calls, masked target numbers, provider and verification states, bounded attempt/retry status, detailed transcript/evidence review, and manual provider reconciliation.

The Android call form persists its draft idempotency key in secure local storage across transport errors and app restarts so retrying after an uncertain app/server response cannot silently create a second call intent.

## Finance constraints remain mandatory

- Beobank Personal = operating/salary/critical obligations.
- Revolut Personal = personal spending + Revolut investment funding.
- Revolut Pro = pro/Uber operating.
- Revolut Pro never directly funds personal Kraken.
- Pro -> Personal = owner draw.
- Personal -> Pro = owner contribution.
- Personal -> Kraken = investment contribution.
- Safety/emergency reserve is never investable.
- Keep critical Beobank standing orders; VA predicts/reserves/verifies.
- Keep Revolut native scheduled investment contributions; VA prefunds/reconciles.
- Kraken auto funding/trading remains off until explicitly configured.
- Kraken withdrawals remain disabled/not implemented.

## Phase 8 release gate

Before Phase 9 source changes:

1. overlay the Phase-8 package at repository root;
2. commit the extracted files, not the ZIP;
3. push to `main`;
4. confirm the full GitHub Actions workflow is green;
5. if CI fails, fix Phase 8 first and do not start Phase 9 source modifications.

Suggested commit message: `Phase 8 — Calls / Telephony`.
