# Full-Time VA v0.8.0 — Structured Cash & Investments

v0.8.0 builds on the verified v0.7.2 banking/history baseline and gives Financial Autopilot a structured model for Personal cash, Pro cash, recurring obligations, Revolut spending/investment funding, Revolut Securities portfolios, and optional Kraken investment funding.

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

Backend `0.8.0` · Android `0.8.0+31` · APK `Full-Time-VA-Android-v0.8.0.apk`.

See `docs/V0.8.0_STRUCTURED_CASH_AND_INVESTMENTS.md` for the new finance architecture. Historical v0.7.1/v0.7.2 validation and importer notes remain in `docs/`.
