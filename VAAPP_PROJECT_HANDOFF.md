# VAAPP project handoff

Updated: 2026-08-13  
Repository: `Demon-blood/VAAPP2`  
Branch: `main`

## Verified source of truth

Phases 1–6 are complete on GitHub. The Phase-6 CI-fix commit is `0802b52d68036d7f120ea7e68dbfaf5b4984962b`. GitHub Actions run #35 completed successfully on 2026-08-13, including the backend suite, Flutter analysis/tests, Android release build, signing and prerelease publishing.

Verified baseline release: backend `0.9.5` / Android `0.9.5+38`.

## Current local candidate

Backend `0.9.6` / Android `0.9.6+39`.

Current phase: **Phase 7 — Financial Allocation & Forecasting**.  
Status: **implemented as a cumulative delta from the verified Phase-6 baseline; awaiting GitHub upload and full CI**.

Next phase after the Phase-7 gate is green: **Phase 8 — Calls / Telephony**.

## Product objective

VAAPP must operate as a real autonomous full-time virtual assistant, not a chatbot, approval queue, paper-mode simulator, or placeholder executor.

Operating lifecycle:

`Observe -> Understand -> Own objective -> Check policy/capability -> Plan -> Execute -> Verify -> Follow up/recover -> Reconcile -> Complete -> Learn`

Routine reversible work executes automatically. **Needs You** is reserved for unavoidable authentication/security, genuinely material decisions without safe pre-authorization, or physical/manual work with no real executor.

## Completed phases

1. **Autonomous Core** — durable objectives, steps, evidence, policy/capability decisions, workflow execution and recovery.
2. **Inbox & Communications Ownership** — durable Gmail ownership, ambiguity-safe sends, SMS/RemoteInput evidence, persistent conversation follow-up.
3. **Calendar & Scheduling Agent** — durable Google Calendar mirror/mutations, conflict checks, deterministic create IDs, provider verification, attendee response ownership.
4. **CRM / Relationship Memory** — source-backed people, identity-safe merging, provenance facts, cross-channel interaction timelines and follow-up projection.
5. **Secure Browser / Portal Operator** — allowlisted Chromium execution, encrypted sessions/credentials, MFA/CAPTCHA handoff, ambiguity-safe side effects and provider postcondition evidence.
6. **Documents / Forms / Deadlines** — Drive-backed document intelligence, exact deadline ownership, encrypted reusable profile facts, durable form intents and provider-verified form completion. CI green on run #35 after the release-version assertion hotfix.

## Phase 7 implementation

### Durable forecast evidence

`FinancialForecastRun` stores an encrypted forecast snapshot and an input fingerprint. Forecasts are generated from effective bank balances, account safety floors, learned recurring cashflows, exact open bills, budget envelopes, current-month income and learned investment funding. Unknown future income is not fabricated.

The default forecast horizon is 90 days, configurable from 30–180 days. It stores both base and conservative cash paths. The conservative path discounts uncertain income and raises variable/recurring debit assumptions.

### Protected cash boundary

Cash is allocatable only when the conservative minimum remains above the protected floor. The floor includes account safety reserves, account target floors and the global minimum operating-cash floor. Expected Revolut investment funding is modeled as a protected outflow and therefore cannot become ordinary surplus.

### Allocation ledger and execution

`FinancialAllocationPlan` stores per-scope forecast-safe surplus. `FinancialAllocationAction` is persisted before dispatch and links to the real `OwnAccountTransfer` ledger.

Allocation priority inside one scope is:

1. controlled spending-wallet prefunding when its target has a gap;
2. current-month tax contribution gap;
3. reserve target/floor gap;
4. ordinary savings for remaining surplus.

Only payment-enabled `operating` accounts with own-transfer permission may send. Destinations must be same-scope, same-currency accounts explicitly configured to accept surplus. The existing transfer executor remains authoritative for global per-transfer limits, daily/monthly caps, minimum transfer amount, active-transfer serialization, reserve checks, provider ambiguity, SCA and provider verification.

### Idempotency / ambiguity

Recent unchanged forecast inputs reuse the recent forecast during background banking cycles. Allocation actions are run-scoped and durable. On recovery, actions reconcile against their linked transfer before status changes. `creation_uncertain` is never treated as success and is never blindly replayed. Bank SCA remains a genuine authentication handoff.

### Automation

The existing `banking.autopilot` durable job now performs forecast-aware allocation after bank/transaction/statement/payment/transfer reconciliation. The manual Financial Autopilot endpoint synchronizes current evidence and forces a fresh forecast/allocation pass.

The historical `run_budget_autopilot` implementation remains in source for compatibility/tests, but scheduled/manual product execution is superseded by the forecast-aware allocator.

### API / Android

New authenticated endpoints:

- `GET /api/finance/forecast`
- `POST /api/finance/forecast/run?horizon_days=90`

Android Money now has seven tabs: Bills, Payments, Accounts, Budget, **Forecast**, Investments, Receipts. Forecast displays base/conservative minima, protected floor, forecast-safe surplus, checkpoints, investment-funding protection and provider-linked allocation actions/authorization.

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

## Phase 7 release gate

Before Phase 8 source changes:

1. overlay the Phase-7 package at repository root;
2. commit the extracted files, not the ZIP;
3. push to `main`;
4. confirm the full GitHub Actions workflow is green;
5. if CI fails, fix Phase 7 first and do not start Phase 8 source modifications.

Suggested commit message: `Phase 7 — Financial Allocation & Forecasting`.
