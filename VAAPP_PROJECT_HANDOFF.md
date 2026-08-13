# VAAPP project handoff

Updated: 2026-08-13  
Repository: `Demon-blood/VAAPP2`  
Branch: `main`

## Verified source of truth

Phases 1–3 are complete on GitHub. Verified Phase-3 commit: `fba21f29c7a6ccc7beb54c2d395c2ef78293ca76`. GitHub Actions run #31 completed successfully, including backend tests, Flutter analysis/tests, Android release build, signing and prerelease publishing.

Verified baseline release: backend `0.9.2` / Android `0.9.2+35`.

## Current local candidate

Backend `0.9.3` / Android `0.9.3+36`.

Current phase: **Phase 4 — CRM / Relationship Memory**.  
Status: **implemented as a cumulative overlay from the verified Phase-3 baseline; awaiting GitHub upload and full CI**.

## Product objective

VAAPP must operate as a real autonomous full-time virtual assistant, not a chatbot, approval queue, paper-mode simulator, or placeholder executor.

Operating lifecycle:

`Observe -> Understand -> Own objective -> Check policy/capability -> Plan -> Execute -> Verify -> Follow up/recover -> Reconcile -> Complete -> Learn`

Routine reversible work executes automatically. **Needs You** is reserved for unavoidable authentication/security, genuinely material decisions without safe pre-authorization, or physical/manual work with no real executor.

## Completed phases

1. **Autonomous Core** — durable objectives, steps, evidence, policy/capability decisions, workflow execution and recovery.
2. **Inbox & Communications Ownership** — durable Gmail ownership, ambiguity-safe sends, SMS/RemoteInput evidence, persistent conversation follow-up.
3. **Calendar & Scheduling Agent** — durable Google Calendar mirror/mutations, conflict checks, deterministic create IDs, provider verification, attendee response ownership. CI green on run #31.

## Phase 4 implementation

### Canonical relationship identity

`RelationshipProfile` is the canonical person record. A profile exists only when VAAPP has a concrete email or phone identity. `RelationshipIdentity` normalizes those identities and enforces global uniqueness. Similar display names never merge people. When multiple verified identities prove two existing profiles refer to the same person, Phase 4 moves their identities/interactions/facts into one profile and writes an audit event.

### Provider-backed interaction timeline

`RelationshipInteraction` is populated by a deterministic reconciliation pass over:
- Google Contacts identities
- Gmail inbound messages
- verified Gmail outbound messages
- Android SMS / supported messaging-app communication events
- Google Calendar attendees and organizers

Each interaction has a stable provider/source reference so repeated reconciliation is idempotent. Protected device communications do not copy their raw body into the relationship summary.

### Source-backed factual memory

`RelationshipFact` stores a fact key/value together with source type, immutable source reference, confidence, and first/last-seen timestamps. Phase 4 records factual Google Contact name/organization data. It deliberately does not infer family status, preferences, sensitive traits, or other personal facts from names or conversational guesses.

### Follow-up / relationship state

Profiles aggregate observed interaction count, last inbound/outbound interaction, preferred channel from actual usage, recent memory topics, an activity score, and the existing VA communication/follow-up state. `waiting_on_counterparty` and `next_follow_up_at` therefore reflect real durable Phase-1/2 follow-up records rather than a separate reminder system.

### Durable reconciliation

`RelationshipMemoryState` records reconciliation health/counts/errors. `relationship.reconcile` is a durable workflow job enqueued on the existing scheduler. The pass is reconstructive and idempotent: it can be safely repeated after restarts and provider sync ordering differences.

### API / Android

New routes:
- `GET /api/relationships/status`
- `GET /api/relationships`
- `GET /api/relationships/{relationship_id}`
- `POST /api/relationships/reconcile`

Manual Google Contacts sync also reconciles relationship memory before returning. Work now has **Relationships** instead of the old directory-only Contacts view, showing memory health, verified identities, source-backed facts, recent interactions and follow-up state.

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
- Keep Revolut native scheduled investment contributions; VA pre-funds/reconciles.
- Kraken auto funding/trading remains off until explicitly configured.
- Kraken withdrawals remain disabled/not implemented.
- Do not commit private banking identifiers, secrets or PII.

## Roadmap

1. Autonomous Core — complete
2. Inbox & Communications Ownership — complete
3. Calendar & Scheduling Agent — complete, CI green run #31
4. CRM / Relationship Memory — current local candidate
5. Secure Browser / Portal Operator
6. Documents / Forms / Deadlines
7. Financial Allocation & Forecasting
8. Calls / Telephony
9. Purchasing / Travel / Logistics / Customer Service
10. Professional Product Cleanup / v1.0

## Phase-completion rule

A phase is complete only when real execution, policy, durable state, idempotency, verification/recovery, audit, tests and GitHub Actions are green. No simulated provider behavior counts as a completed capability.

## Exact next action

Apply the v0.9.3 Phase-4 overlay to GitHub `main`, run Actions, fix failures until green, then mark Phase 4 complete and begin Phase 5 — Secure Browser / Portal Operator.
