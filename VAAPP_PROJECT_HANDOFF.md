# VAAPP project handoff

Updated: 2026-08-13  
Repository: `Demon-blood/VAAPP2`  
Branch: `main`

## Verified source of truth

Phases 1–5 are complete on GitHub. Verified Phase-5 commit: `2271d599bc11e2e4c17e5ca5a1fe6c6a59428df3`. GitHub Actions run #33 completed successfully, including backend tests, Flutter analysis/tests, Android release build, signing and prerelease publishing.

Verified baseline release: backend `0.9.4` / Android `0.9.4+37`.

## Current local candidate

Backend `0.9.5` / Android `0.9.5+38`.

Current phase: **Phase 6 — Documents / Forms / Deadlines**.  
Status: **implemented as a cumulative overlay from the verified Phase-5 baseline; awaiting GitHub upload and full CI**.

## Product objective

VAAPP is a real autonomous full-time virtual assistant, not a chatbot, approval queue, paper-mode simulator, or placeholder executor.

Operating lifecycle:

`Observe -> Understand -> Own objective -> Check policy/capability -> Plan -> Execute -> Verify -> Follow up/recover -> Reconcile -> Complete -> Learn`

Routine reversible work executes automatically. **Needs You** is reserved for unavoidable authentication/security, genuinely material decisions without safe pre-authorization, or information only the account holder can provide. Missing executors/providers remain VA-owned capability blocks.

## Completed phases

1. **Autonomous Core** — durable objectives, steps, evidence, policy/capability decisions, workflow execution and recovery.
2. **Inbox & Communications Ownership** — durable Gmail ownership, ambiguity-safe sends, SMS/RemoteInput evidence, persistent conversation follow-up.
3. **Calendar & Scheduling Agent** — durable Calendar mirror/mutations, conflict checks, deterministic creation and provider verification. CI green run #31.
4. **CRM / Relationship Memory** — canonical source-backed people, provenance facts, cross-channel interaction timelines and follow-up projection. CI green run #32.
5. **Secure Browser / Portal Operator** — real Chromium execution, encrypted sessions/plans/evidence, allowlisted navigation, authentication handoff and no-blind-replay side effects. CI green run #33.

## Phase 6 implementation

### Source-backed document intelligence

VA-managed Drive documents are read through the real Google Drive API. PDF extraction uses PyMuPDF native text; HTML/text documents use deterministic parsing. Gmail-origin documents can also use the real source message body. Extracted document text is encrypted at rest and is not copied into audit payloads.

Only exact dates that appear next to deadline language such as `due`, `submit by`, `uiterlijk`, or `vervaldatum` become durable deadlines. VAAPP deliberately does not invent a year for partial dates.

### Durable obligations

`DocumentObligation` is the ownership record for a form or deadline. It carries the source document/message, issuer, due date, priority, protected/material flags, secure portal link, objective/browser operation link, execution state, error and verified completion timestamp.

A deadline with no safe executable action does not disappear into an informational list: the Autonomous Core receives a `document_obligation_blocked` event and owns a `blocked_capability` objective with the actual deadline attached.

### Forms and verified profile facts

Verified reusable facts such as name/email/phone/address are stored in `UserProfileFact`; values are Fernet-encrypted. Google OAuth seeds verified name/email when available, while facts that only the account holder knows can be added explicitly.

`FormSubmission` persists an encrypted field intent before execution. Stable idempotency keys include a hash of the verified field set so adding missing facts creates a new safe preparation rather than mutating or replaying an old ambiguous submission.

### Safe browser execution

Phase 6 extends the Phase-5 browser operator with two explicit operations:

- `autofill_form` fills recognized fields and verifies required fields **without** a side effect.
- `click_action` performs the mutating submit/continue action and is persisted as a side effect before dispatch.

This separation matters: if required information is missing, VAAPP can request the missing verified data before any submit occurs. A worker failure after the submit step still follows Phase-5 `creation_uncertain` no-blind-replay handling.

Completion requires the provider page to satisfy an explicit postcondition. Form attempts are not counted as success.

### Material/protected forms

The existing central browser policy remains authoritative. Contract/signature/payment/security-changing submissions are marked material and require the one-time material decision before the side-effect step can run. Authentication/MFA/CAPTCHA continues to use the secure browser handoff; CAPTCHA is never bypassed.

### Background ownership and Android

`documents.reconcile` is a durable workflow handler and the existing document housekeeping cycle also reconciles document intelligence/obligations. Work → **Documents** now shows the archive together with owned obligations, deadlines, portal/form status, blocked reasons and verified profile facts.

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
5. Secure Browser / Portal Operator — complete, CI green run #33
6. Documents / Forms / Deadlines — current local candidate
7. Financial Allocation & Forecasting
8. Calls / Telephony
9. Purchasing / Travel / Logistics / Customer Service
10. Professional Product Cleanup / v1.0

## Phase-completion rule

A phase is complete only when real execution, policy, durable state, idempotency, verification/recovery, audit, tests and GitHub Actions are green. No simulated provider behavior counts as a completed capability.

## Exact next action

Apply the v0.9.5 Phase-6 overlay to GitHub `main`, run Actions, fix failures until green, then mark Phase 6 complete and begin Phase 7 — Financial Allocation & Forecasting.
