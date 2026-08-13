# VAAPP project handoff

Updated: 2026-08-13  
Repository: `Demon-blood/VAAPP2`  
Branch: `main`

## Verified source of truth

Phases 1–4 are complete on GitHub. Verified Phase-4 commit: `bf57e7bdc59e2e72534d2a8fa11a35c5fed0cde9`. GitHub Actions run #32 completed successfully, including backend tests, Flutter analysis/tests, Android release build, signing and prerelease publishing.

Verified baseline release: backend `0.9.3` / Android `0.9.3+36`.

## Current local candidate

Backend `0.9.4` / Android `0.9.4+37`.

Current phase: **Phase 5 — Secure Browser / Portal Operator**.  
Status: **implemented as a cumulative overlay from the verified Phase-4 baseline; awaiting GitHub upload and full CI**.

## Product objective

VAAPP must operate as a real autonomous full-time virtual assistant, not a chatbot, approval queue, paper-mode simulator, or placeholder executor.

Operating lifecycle:

`Observe -> Understand -> Own objective -> Check policy/capability -> Plan -> Execute -> Verify -> Follow up/recover -> Reconcile -> Complete -> Learn`

Routine reversible work executes automatically. **Needs You** is reserved for unavoidable authentication/security, genuinely material decisions without safe pre-authorization, or physical/manual work with no real executor.

## Completed phases

1. **Autonomous Core** — durable objectives, steps, evidence, policy/capability decisions, workflow execution and recovery.
2. **Inbox & Communications Ownership** — durable Gmail ownership, ambiguity-safe sends, SMS/RemoteInput evidence, persistent conversation follow-up.
3. **Calendar & Scheduling Agent** — durable Google Calendar mirror/mutations, conflict checks, deterministic create IDs, provider verification, attendee response ownership. CI green on run #31.
4. **CRM / Relationship Memory** — canonical source-backed people, identity-safe merging, provenance facts, cross-channel interaction timelines and follow-up projection. CI green on run #32.

## Phase 5 implementation

### Real Chromium executor

`browser.operation.run` executes inside the existing durable workflow worker with Playwright Chromium. Portal work is not marked complete because a click returned successfully: the operation has an explicit provider postcondition and remains unfinished until that postcondition is observed.

### Durable portal state

Additive tables persist portal definitions, encrypted credentials, encrypted browser storage state, durable operations and encrypted evidence. The exact operation plan and verification values are encrypted at rest; public summaries avoid reproducing secrets. Stable operation idempotency keys prevent duplicate ownership records.

### Navigation / SSRF boundary

Portal base/login URLs must be HTTPS. Every portal has an explicit host allowlist. Direct localhost/private/link-local targets are rejected and the executor additionally resolves request hostnames at runtime so an allowlisted-looking hostname cannot resolve into a private network target. Main-frame redirects outside the portal allowlist are blocked.

### Side-effect ambiguity

Potentially mutating clicks/Enter submissions are persisted as `dispatching` before execution. If the worker/provider outcome becomes ambiguous, the operation reconciles the explicit postcondition first and does not blindly replay the side effect. An unverified ambiguous outcome becomes `creation_uncertain` / system-blocked for investigation rather than a duplicate submission.

### Authentication and material decisions

CAPTCHA is detected and never bypassed. OTP/MFA and external approval challenges become **Needs You** while preserving the encrypted browser session for resume. A submitted OTP is encrypted as a one-time resume value and cleared after use. Portal actions that pay, purchase, sign/accept a contract, change security credentials, close/delete accounts, or perform comparable material commitments require a specific one-time user approval before execution.

### Autonomous Core / evidence

A browser request is recorded as `browser_portal_operation_planned`, producing a normal VA objective with a `browser_operation` step. The objective can complete only when the underlying `BrowserOperation` reaches `verified`; at that point the core records `browser_postcondition_verified` evidence.

### API / Android

New browser APIs expose status, portal configuration, encrypted credential updates, operation list/detail, OTP submission, authentication resume, material approval, and authenticated screenshot evidence. Work now includes **Portals**, where the user can configure allowlisted portals and handle only the genuinely unavoidable MFA/CAPTCHA/material-decision handoffs.

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
4. CRM / Relationship Memory — complete, CI green run #32
5. Secure Browser / Portal Operator — current local candidate
6. Documents / Forms / Deadlines
7. Financial Allocation & Forecasting
8. Calls / Telephony
9. Purchasing / Travel / Logistics / Customer Service
10. Professional Product Cleanup / v1.0

## Phase-completion rule

A phase is complete only when real execution, policy, durable state, idempotency, verification/recovery, audit, tests and GitHub Actions are green. No simulated provider behavior counts as a completed capability.

## Exact next action

Apply the v0.9.4 Phase-5 overlay to GitHub `main`, run Actions, fix failures until green, then mark Phase 5 complete and begin Phase 6 — Documents / Forms / Deadlines.
