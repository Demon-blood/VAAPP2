# Full-Time VA v0.9.0 — Autonomous Core

v0.9.0 keeps the verified v0.8.1 finance/investments baseline and introduces Phase 1 of the full-time-VA architecture: a durable autonomous operator core. Events become owned objectives, every executable step is policy/capability checked, external work is dispatched through the existing real workflow engine with stable idempotency keys, and objectives remain open until their outcome is independently verified.

## Cash structure

- **Beobank Personal** remains the operating/bills hub and salary destination.
- **Revolut Personal** can be auto-seeded as a `spending` role. The VA learns a controlled one-week cash target from recent spending and expected Revolut Securities portfolio funding.
- **Revolut Pro** lives inside the personal Revolut app. When the provider exposes the Pro product/account marker, VAAPP scopes that account as Pro and keeps it `operating` for Uber/business cash; explicit Money → Accounts scope choices are preserved.
- Personal and Pro are never silently rebalanced across scopes. Connected cross-scope own-account transfers are excluded from normal budgets and labelled owner draw / owner contribution when direction is clear.
- Child support, housing contributions, subscriptions, utilities, and insurance can be learned as recurring obligations after sufficient monthly evidence and are protected before surplus is moved.
- Tax allocation can remain a **virtual protected reserve** in the operating account when no dedicated tax bank account exists.

## Investments

- The Money history importer accepts Beobank bank PDFs, Revolut bank PDF/XLSX, and Revolut Securities Account/P&L PDF/XLSX exports.
- Revolut Securities Account XLSX is the canonical transaction ledger; Account PDF supplies current portfolio positions and per-currency summaries.
- P&L XLSX supplies realised FIFO cost basis/proceeds/P&L plus dividends and withholding tax; P&L PDF is validation/export evidence.
- Brokerage and Robo portfolios are stored separately. Robo is identified deterministically from its management-fee activity rather than personal account identifiers.
- Revolut's own scheduled portfolio contribution remains the execution mechanism. The VA forecasts/funds the Revolut Personal cash needed for it and classifies the portfolio movement as `investment_contribution`, not lifestyle spending.
- Optional Kraken Autopilot can fund a configured verified EUR deposit destination from safe Personal surplus through Enable Banking PIS and, when explicitly enabled, place a policy-bounded Spot market purchase after the Kraken EUR deposit is observed.
- Kraken withdrawal execution is deliberately not implemented or required.

## Release identity

Backend `0.9.0` · Android `0.9.0+33` · APK `Full-Time-VA-Android-v0.9.0.apk`.

See `docs/V0.8.0_STRUCTURED_CASH_AND_INVESTMENTS.md` for the new finance architecture. Historical v0.7.1/v0.7.2 validation and importer notes remain in `docs/`.


## v0.8.1 Investments dashboard

Money now includes a dedicated Investments tab for Revolut Brokerage/Robo statements and live Kraken balances, performance, contribution tracking and investment-autopilot status. Budget remains focused on cash flow, obligations and reserves.


## v0.9.0 Autonomous Core — Phase 1

- A unified durable event stream (`VAEvent`) converts currently actionable state into VA-owned objectives.
- Objectives persist goals, state, risk, source context, execution steps, policy/capability decisions, follow-ups, and independently recorded outcome evidence.
- The new core reuses the existing real `WorkflowRun`/`WorkflowJob` engine rather than introducing a parallel or simulated executor.
- Every dispatched objective step has a stable idempotency key. Provider ambiguity is never resolved by blind duplicate execution.
- Workflow completion is verified from durable workflow state; superseded work is accepted only when its replacement job is independently confirmed completed.
- Existing payment/SCA and own-account-transfer blockers are reconciled back to the original real bank operation.
- Provider-auth dead letters automatically resume only after the corresponding real capability is healthy again.
- System/capability gaps remain VA-owned and do **not** appear in **Needs You**. Needs You is reserved for genuine provider authentication or material user decisions.
- A scheduled `va.core.cycle` keeps the operator running automatically, while **Work → Operations** exposes autonomy metrics, live capabilities, Needs You, and the objective ledger.
- Phase 1 intentionally does not claim browser, telephony, or future-domain executors that do not yet exist. Those are implemented in later phases.

See `docs/V0.9.0_AUTONOMOUS_CORE.md` for the Phase-1 contract and validation requirements.
