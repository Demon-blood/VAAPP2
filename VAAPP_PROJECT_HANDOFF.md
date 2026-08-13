# VAAPP project handoff

Updated: 2026-08-13  
Repository: `Demon-blood/VAAPP2`  
Branch: `main`

## Verified source of truth

Phases 1–8 are complete on GitHub. Phase 8 is commit `7432e5dbae1cf4d3b8085931eb491da7d9ca6437`. GitHub Actions run #38 completed successfully on 2026-08-13, including backend tests, Flutter analysis/tests, the signed Android release build, and prerelease publication.

Verified baseline release: backend `0.9.7` / Android `0.9.7+40`.

## Current local candidate

Backend `0.9.8` / Android `0.9.8+41`.

Current phase: **Phase 9 — Purchasing / Travel / Logistics / Customer Service**.  
Status: **implemented as a cumulative delta from the verified Phase-8 baseline; awaiting GitHub upload and full CI**.

Next phase after the Phase-9 gate is green: **Phase 10 — Professional Product Cleanup / v1.0**.

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
8. **Calls / Telephony** — real Twilio PSTN calls, signed callbacks, encrypted call/turn/evidence ledgers, ambiguity-safe creation, bounded voice interaction and counterparty-verified objective completion. CI green on run #38.

## Phase 9 implementation

### One fulfillment ownership layer

Phase 9 introduces additive `FulfillmentProvider`, `FulfillmentRequest`, `FulfillmentAction`, and `FulfillmentEvidence` tables. The same ledger owns purchase, travel, logistics/tracking, return, refund, provider cancellation and customer-service work.

Existing Gmail-derived `OrderRecord` and `SupportCase` rows are reconciled into fulfillment objectives with stable idempotency keys. Terminal order/support state can supply source-backed completion evidence; otherwise the objective remains owned and due for reconciliation/follow-up.

### Real provider execution only

Fulfillment reuses existing executors rather than inventing a paper-mode provider layer:

- Secure Browser for configured allowlisted merchant/travel/carrier/service portals with provider-specific recipes and explicit postconditions.
- Twilio telephony for configured verified support numbers when a browser support recipe is unavailable.

No provider/recipe/executor means `blocked_capability`. A browser click or completed telephone call is not treated as objective completion without downstream verification.

### Payment authority

Purchase/travel browser workflows remain material commitments. Phase 9 adds configurable standing preauthorization:

- purchase enable + maximum single purchase EUR;
- travel enable + maximum single travel EUR;
- combined monthly purchase/travel commitment limit EUR.

Standing authority applies only when the request has a known positive EUR amount and fits both the relevant single limit and the monthly envelope. Unknown/over-limit commitments become `needs_user` before provider dispatch.

A specific one-off payment authorization also requires a known positive EUR amount and is recorded as fulfillment evidence. Provider SCA/MFA/OTP remains a separate `needs_user` authentication step even after payment authority exists.

### Ambiguity-safe execution

`FulfillmentAction` is committed before provider-specific browser preparation or telephony call creation. The linked browser/call ledger is reused across restart. Browser `creation_uncertain` becomes `blocked_system` and is never blindly replayed.

### Continuous logistics/customer-service ownership

A five-minute reconciliation loop imports newly detected orders/support cases, attaches newly configured providers, refreshes active actions, owns follow-up, and records verified terminal evidence. A support call that ends without verified counterparty outcome remains waiting rather than being marked complete.

### Android

The main app bar exposes a **Fulfillment** workspace. It shows ownership/provider status, allows provider recipe configuration and fulfillment-objective creation, triggers reconciliation, and surfaces one-off payment authorization only when required. The UI states explicitly that browser/payment intent is not proof of completion.

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

## Phase 9 release gate

Before Phase 10 source changes:

1. overlay the Phase-9 package at repository root;
2. commit the extracted files, not the ZIP;
3. push to `main`;
4. confirm the full GitHub Actions workflow is green;
5. if CI fails, fix Phase 9 first and do not start Phase 10 source modifications.

Suggested commit message: `Phase 9 — Purchasing / Travel / Logistics / Customer Service`.
