# Full-Time VA v0.9.4 — Secure Browser / Portal Operator

v0.9.4 is the cumulative Phase 1–5 release candidate. It keeps the autonomous core, communications ownership, Calendar agent, and relationship memory, and adds a real secure Chromium portal operator with encrypted sessions, allowlisted navigation, MFA/CAPTCHA handoff, ambiguity-safe submissions, provider postcondition verification, and an Android Portals workspace.

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

Backend `0.9.4` · Android `0.9.4+37` · APK `Full-Time-VA-Android-v0.9.4.apk`.

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


## v0.9.1 Inbox & Communications Ownership — Phase 2

- Gmail mailbox state persists the current history cursor, push-watch topic/expiration, last push, last history sync, last full recovery sync, and last watch renewal/error.
- Gmail Pub/Sub callbacks persist a `gmail.history.sync` workflow job before acknowledging the notification. Periodic full sync remains a recovery path if push delivery is delayed or a cursor becomes stale.
- Safe automatic replies are no longer sent inline from the email processor. They become durable VA objectives whose Gmail intent is persisted and committed before the external send call.
- Each outbound Gmail action uses a deterministic RFC Message-ID and reconciles the Sent mailbox before any retry, preventing blind duplicate sends after timeout/connection ambiguity.
- Reply threading preserves Gmail `threadId` plus standard `In-Reply-To`/`References` headers. A successful send is recorded only after the sent message can be independently found and verified.
- Email/SMS/messaging conversations are owned as persistent communication threads. When the VA is waiting on the other party it schedules a durable follow-up and cancels that follow-up automatically when a response arrives.
- Android SMS execution uses `SmsManager` plus carrier SENT/DELIVERED callbacks. Local carrier evidence is retained and reposted to the backend after temporary network failure before any resend is considered.
- Notification-based app replies use the live Android `RemoteInput` action only while that real notification action exists. The app records provider handoff (`dispatched`) evidence; it does not claim arbitrary background initiation or recipient delivery for WhatsApp/Signal/Telegram/Messenger.
- A WorkManager reconciliation worker replays locally proven communication evidence and background-dispatches only real SMS actions. Unknown delivery outcomes fail closed instead of causing duplicate messages.
- The Communications screen now shows Gmail watch/cursor health plus the persistent conversation-ownership ledger.

See `docs/V0.9.1_INBOX_COMMUNICATIONS_OWNERSHIP.md` for the Phase-2 execution and verification contract.

## v0.9.2 Calendar & Scheduling Agent — Phase 3

- Google Calendar is synchronized into a durable provider mirror instead of being read only at request time.
- Every VA-created calendar change is persisted in `CalendarMutation` before the Google API call.
- Event creation uses a deterministic Google Calendar event ID derived from the VA step idempotency key, so ambiguous retries address the same provider object instead of creating duplicates.
- Updates and cancellations reconcile provider state and use the observed ETag when available.
- Busy-time checks prevent routine autonomous double-booking. A fixed-time conflict becomes a real scheduling decision instead of silently overwriting availability.
- Calendar steps remain `verifying` until Google Calendar independently reflects the requested postcondition; only then is `calendar_event_verified` outcome evidence stored.
- Email-derived calendar actions now become durable `calendar_event_planned` objectives rather than inline side effects.
- Calendar sync observes attendee response state. When invitees answer, pending scheduling follow-ups are cancelled and the waiting objective can complete.
- When an invite genuinely expects a response, the objective can own a bounded follow-up through the existing durable communications engine.
- Work now includes a Calendar tab showing sync health, upcoming mirrored events, VA-owned events, and attendee-response state.

See `docs/V0.9.2_CALENDAR_SCHEDULING_AGENT.md` for the Phase-3 execution and verification contract.

## v0.9.3 CRM / Relationship Memory — Phase 4

- `RelationshipProfile` is the canonical person record. It is created only from concrete email/phone identities; similar names are never enough to merge people.
- `RelationshipIdentity` stores globally unique normalized email/phone identities with the source and first/last-seen timestamps. When a Google Contact proves that two previously separate identities belong to one person, the profiles are merged with an audit event.
- `RelationshipInteraction` creates a durable timeline from Gmail inbound mail, **verified** Gmail outbound mail, Android SMS/messaging events, and Google Calendar attendees/organizers.
- Protected device messages are represented as protected interactions without copying their raw body into relationship memory.
- `RelationshipFact` stores only source-backed facts with provenance. Phase 4 records Google Contacts display name/organization facts and does not invent personal attributes from names or conversational guesses.
- Relationship aggregates track last inbound/outbound contact, preferred channel by observed usage, interaction count, activity score, memory topics, waiting-on-counterparty state, and the next real VA follow-up.
- A durable `relationship.reconcile` workflow job continually rebuilds/repairs relationship memory from existing provider evidence, so restarts and temporary ordering differences do not lose context.
- New APIs expose relationship health, list/detail views, and a manual reconciliation trigger.
- Work → **Relationships** replaces the old address-book-only Contacts view and shows verified identities, source-backed facts, recent interactions, follow-up state, and memory health.

See `docs/V0.9.3_CRM_RELATIONSHIP_MEMORY.md` for the Phase-4 identity, provenance and reconciliation contract.

## v0.9.4 Secure Browser / Portal Operator — Phase 5

- Portal automation runs through real headless Playwright Chromium in the durable workflow worker; there is no paper-mode browser success path.
- Portal definitions require HTTPS and explicit host allowlists. Direct private/local targets and DNS-resolved private-network destinations are blocked, and main-frame redirects cannot escape the portal allowlist.
- Usernames/passwords, browser storage state, exact operation plans, verification values, resume URLs, OTP resume values, and screenshot payloads are encrypted at rest.
- Every operation has a stable idempotency key and an explicit provider postcondition. A successful click is not completion; VAAPP verifies the resulting provider page state first.
- Potentially mutating submissions are persisted before dispatch. If the outcome is ambiguous, VAAPP reconciles the postcondition and refuses to blindly replay the action.
- CAPTCHA is detected but never bypassed. OTP/MFA/external approval challenges become Needs You while the encrypted browser session is preserved for resume.
- Payment, purchase, contract-signing/acceptance, credential/security changes, account closure/deletion, and comparable material commitments require a specific one-time approval.
- Browser work is owned by the Autonomous Core as `browser_operation` steps and completes only with `browser_postcondition_verified` evidence.
- Work → **Portals** exposes secure portal configuration, operation status, MFA resume, material approval, and browser evidence without exposing stored credentials.

See `docs/V0.9.4_SECURE_BROWSER_PORTAL_OPERATOR.md` for the Phase-5 security, execution, ambiguity and verification contract.
