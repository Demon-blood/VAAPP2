# VAAPP project handoff

Updated: 2026-08-13  
Repository: `Demon-blood/VAAPP2`  
Branch: `main`

## Verified source of truth

Phase 2 is complete on GitHub. Verified baseline commit: `651a2e304c410962b09cd2a26b7867e77ab2fccb`. GitHub Actions run #30 completed successfully: backend tests, Flutter installation, Android signing, Flutter analysis/tests, Android release build, and prerelease publishing all passed.

Verified baseline release: backend `0.9.1` / Android `0.9.1+34`.

## Current local candidate

Backend `0.9.2` / Android `0.9.2+35`.

Current phase: **Phase 3 — Calendar & Scheduling Agent**.  
Status: **implemented as a cumulative overlay from the verified Phase-2 baseline; awaiting GitHub upload and full CI**.

## Product objective

VAAPP must operate as a real autonomous full-time virtual assistant, not a chatbot, approval queue, paper-mode simulator, or placeholder executor.

Operating lifecycle:

`Observe -> Understand -> Own objective -> Check policy/capability -> Plan -> Execute -> Verify -> Follow up/recover -> Reconcile -> Complete -> Learn`

Routine reversible work executes automatically. **Needs You** is reserved for unavoidable authentication/security, genuinely material decisions without a safe pre-authorization, or physical/manual work with no real executor.

## Completed phases

### Phase 1 — Autonomous Core

Durable `VAEvent`, `VAObjective`, steps, evidence, follow-ups, policy, capability checks, workflow dispatch, verification, recovery, Needs You semantics and Android Operations UI.

### Phase 2 — Inbox & Communications Ownership

Durable Gmail history/watch ownership, ambiguity-safe Gmail sends, communication threads, verified Android SMS/RemoteInput evidence and owned follow-ups. Phase-2 CI hotfix is included in baseline commit `651a2e...`; run #30 is green.

## Phase 3 implementation

### Calendar provider mirror

`CalendarSyncState` stores sync health. `CalendarEventMirror` persists Google-observed events, timing, attendees, provider update time, ETag, link and VA objective ownership. A scheduled `calendar.sync` workflow job refreshes the mirror.

### Durable mutation ledger

`CalendarMutation` persists create/update/cancel intent before provider execution. It stores stable idempotency, objective/step correlation, provider event ID, desired and observed state, ETag, attempts, errors and verification state.

### Duplicate-safe event creation

The create path derives a deterministic Google Calendar event ID from the objective-step idempotency key and supplies it on insert. Ambiguous retries therefore address the same provider object. HTTP 409 is reconciled rather than treated as permission to generate another event.

### Conflict policy

Create mutations default to a real Google Calendar free/busy check. A fixed-time conflict becomes `needs_user` because choosing which commitment to move is a material scheduling decision. Missing Calendar OAuth is also user-resolvable authentication. Other provider failures remain VA-owned recovery/system blockers.

### Verification

Calendar mutations remain `verifying` until Google Calendar independently reflects the requested state. Only then does the objective receive `calendar_event_verified` evidence and advance/completely finish.

### Attendee responses and follow-up

For events that explicitly expect an attendee response, the verified event leaves a wait step. Calendar sync watches provider attendee statuses. When all invitees answer, it records `calendar_attendee_response_received`, cancels pending chases and completes the scheduling objective. A bounded email follow-up can be scheduled through the Phase-2 durable communications executor.

### Email-originated scheduling

`email_processor.py` no longer calls Calendar inline. A detected calendar action is persisted as `calendar_event_planned`, then owned by the autonomous core with the same policy/idempotency/verification contract.

### API / Android

New routes:
- `GET /api/calendar/status`
- `GET /api/calendar/events`
- `GET /api/calendar/availability`
- `POST /api/calendar/sync`
- `POST /api/calendar/objectives`

Android Work now has a **Calendar** tab showing mirror health, upcoming events, VA-owned events and attendee-response state.

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
2. Inbox & Communications Ownership — complete, CI green run #30
3. Calendar & Scheduling Agent — current local candidate
4. CRM / Relationship Memory
5. Secure Browser / Portal Operator
6. Documents / Forms / Deadlines
7. Financial Allocation & Forecasting
8. Calls / Telephony
9. Purchasing / Travel / Logistics / Customer Service
10. Professional Product Cleanup / v1.0

## Phase-completion rule

A phase is complete only when real execution, policy, durable state, idempotency, verification/recovery, audit, tests and GitHub Actions are green. No simulated provider behavior counts as a completed capability.

## Exact next action

Apply the v0.9.2 Phase-3 overlay to GitHub `main`, run Actions, fix failures until green, then mark Phase 3 complete and begin Phase 4 — CRM / Relationship Memory.
