# Full-Time VA v1.0.4 — Execution Readiness & Setup Assistant

Full-Time VA v1.0.4 is the fourth maintenance release on the cumulative Phase 1–10 production baseline. It keeps the existing autonomous executors and adds capability-specific readiness checks plus in-app setup guidance so an OFFLINE badge explains exactly what is missing instead of forcing the user to reverse-engineer configuration.

The v1.0.4 patch does not add a simulated executor. It makes capability reporting stricter: Gmail push requires an active Gmail watch for the configured Pub/Sub topic and verification token, and fulfillment only reports available when an enabled provider is actually linked to an enabled browser portal or to a support phone with live telephony. The v1.0 evidence contract remains unchanged.

**Completion rule:** external work is complete only when the relevant domain ledger contains independent provider/source evidence for the requested postcondition. A local button press, dispatched browser action, initiated payment, placed call, configured provider, or READY capability is not completion evidence.


## v1.0.4 execution readiness and setup assistant

- Work → Operations capability rows are now interactive. Tapping a capability opens its current executor state, exact setup location, and capability-specific configuration steps.
- The setup sheet can open Services, Communications, Calls, Fulfillment, or the Work → Portals tab directly for the relevant executor.
- Gmail push has a safe **Activate watch** action that calls the existing authenticated `/api/google/watch` endpoint, refreshes the capability matrix, and reports the provider watch expiration.
- Gmail push no longer becomes available merely because a topic string exists. It requires Google OAuth, a Pub/Sub topic, a verification token, and a non-expired Gmail watch bound to that topic.
- Gmail push distinguishes `READY` from `LIVE`: an accepted Gmail watch is ready to receive events, while real Pub/Sub delivery is separately marked verified only after VAAPP has actually observed a notification.
- Fulfillment availability no longer combines unrelated providers and portals. At least one enabled provider must be linked to an enabled browser portal, or have a configured support phone while real telephony is live.
- Setup metadata never includes secret values. The Pub/Sub callback shown in guidance is constructed on-device from the paired backend URL and tells the user to reuse the private verification token already stored in Services.
- Capability setup guidance preserves the product evidence rule: executor configuration/readiness is never treated as proof that an external objective completed.

See `docs/V1.0.4_EXECUTION_READINESS_SETUP_ASSISTANT.md` for the maintenance contract and readiness semantics.


## v1.0.3 logistics tracking ownership

- Logistics browser runs now separate **provider-page verification** from the **business outcome**. Reaching a real carrier tracking page is evidence that an observation happened; it is not evidence that a parcel was delivered.
- A new additive `FulfillmentObservation` ledger records provider, normalized tracking state, stalled/terminal flags, encrypted observation details and the source browser operation.
- Supported tracking states include `pre_transit`, `in_transit`, `out_for_delivery`, `available_for_pickup`, `delivered`, `exception`, `returned` and `unknown`. Only a provider-backed delivered state completes an ordinary logistics objective.
- Non-terminal states remain `waiting_provider` and schedule bounded automatic rechecks. Out-for-delivery, pickup, unknown and normal transit intervals are independently configurable in the provider recipe.
- Transient read-only tracking failures use bounded exponential retry rather than failing the whole objective. Ambiguous browser side effects remain blocked; tracking recipes are expected to be read-only.
- A parcel reported ready for pickup becomes `needs_user` because physical collection requires a real-world executor, while VAAPP continues monitoring the carrier state.
- Secure Browser verification can record `observe_text_any` state signals without those signals deciding whether the browser operation itself passed its explicit provider-page postcondition. Raw matched carrier text is not written to plaintext evidence.
- Android Fulfillment shows the latest tracking state, observation time, next check and stalled state.
- Fulfillment provider configuration includes editable starter templates. The built-in bpost starter uses the source-backed `tracking_url` and the allowlisted `track.bpost.cloud` portal; it never invents a barcode or treats navigation alone as delivery.

See `docs/V1.0.3_LOGISTICS_TRACKING_OWNERSHIP.md` for the maintenance contract and bpost setup notes.


## v1.0.2 maintenance reliability

- Android RFC3339 `Z` timestamps are normalized to naive UTC before insertion into the existing timezone-naive SQL communication columns, avoiding the PostgreSQL/asyncpg aware-vs-naive datetime failure that surfaced as HTTP 500.
- Communication batch ingestion rolls back only the failing record and continues with the rest of the real SMS/call evidence instead of aborting the whole catch-up request.
- The paired Android client treats a backend partial failure as unsuccessful, keeps encrypted queued inbound events for retry, and surfaces the failed-record count.
- The v1.0.1 durable inbound outbox, bounded 25-record history chunks, RECEIVE_SMS diagnostics, and supported messaging/RCS notification capture remain in force.
- Configured fulfillment providers are editable in place, including enabled state, scope, support phone, recipe and a real Secure Browser portal dropdown instead of a numeric database ID.
- Configured Secure Browser portals are editable in place; blank credential fields preserve the existing encrypted login rather than clearing it.
- Order/logistics ownership now requires source-backed fulfillment evidence. Google payment/app-store receipts and other payment-only messages without shipping, delivery, pickup or tracking evidence are dismissed from logistics while their Gmail/financial receipt evidence remains preserved.
- False positives can also be corrected manually with **Not an order** from either Work → Orders or Fulfillment.
- No provider action is marked complete merely because ingestion or configuration succeeded; the existing executor/evidence rules are unchanged.

See `docs/V1.0.2_MAINTENANCE_RELIABILITY.md` for the maintenance contract and verification notes.

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

Backend `1.0.4` · Android `1.0.4+46` · APK `Full-Time-VA-Android-v1.0.4.apk`.

Verified maintenance baseline: commit `fedf9a47864f1337c0100c1ed6d9b36daffb4017` (`v1.0.3 — Logistics Tracking Ownership`), GitHub Actions run #44 successful.

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

## v0.9.5 Documents / Forms / Deadlines — Phase 6

- Archived Drive documents are analyzed from their real provider bytes; Gmail-origin records can also reconcile against the real source message. PDF intelligence uses native PDF text extraction and deliberately does not claim OCR.
- Extracted document text and reusable profile facts are encrypted at rest. Audits and user-facing status expose references, states and provenance rather than copying sensitive document bodies.
- Deadlines are promoted only from exact source dates with nearby deadline language. VAAPP does not invent a year or turn every date appearing in a document into an obligation.
- `DocumentIntelligence`, `DocumentObligation`, `FormSubmission`, and `UserProfileFact` form an additive durable ownership ledger. Documents with actionable deadlines remain VA-owned even when no executor is currently available.
- Form automation reuses the Phase-5 secure browser. The VA may only target an already configured HTTPS portal whose allowlist covers the form host; document links never silently expand the browser trust boundary.
- Known verified facts are filled before any submit action. If a required value is missing, the browser stops before side effects and the obligation becomes Needs You for the missing account-holder information.
- Form submission itself is a separately persisted non-replay-safe browser action. Ambiguous provider outcomes are never blindly resubmitted, and material commitments continue to use the Phase-5 one-time approval policy.
- A form or deadline is not complete because VAAPP attempted it. Completion requires the downstream browser operation to reach and verify its provider postcondition; the document obligation then records the provider-verified completion time.
- If a deadline exists but no safe executor can be matched, the Autonomous Core creates a `blocked_capability` objective carrying that exact deadline instead of leaving the obligation unowned.
- Work → **Documents** now combines the archive with obligation state, due dates, blocked reasons and verified profile facts, plus an explicit reconcile action.

See `docs/V0.9.5_DOCUMENTS_FORMS_DEADLINES.md` for the Phase-6 extraction, ownership, execution and completion contract.


## v0.9.6 Financial Allocation & Forecasting — Phase 7

- VAAPP now persists encrypted 30–180 day forecast snapshots using effective bank balances, learned recurring cashflows, exact open bills, budget envelopes, account reserve floors and expected investment funding.
- The default 90-day conservative scenario discounts uncertain income and increases debit assumptions; unknown future income is never invented.
- Cash is allocatable only when the conservative minimum remains above the protected floor. Safety/emergency reserves and expected Revolut investment funding are therefore never classified as free surplus.
- `FinancialForecastRun`, `FinancialAllocationPlan`, and `FinancialAllocationAction` create a durable evidence/intent ledger separate from the real `OwnAccountTransfer` provider ledger.
- Same-scope allocation priority is controlled spending prefunding, tax contribution gap, reserve gap, then ordinary savings. Personal and Pro never rebalance silently across scopes.
- Allocation actions persist before dispatch and use the existing ambiguity-safe Enable Banking own-account transfer executor. `creation_uncertain` is not replayed or treated as success; bank SCA remains Needs You.
- The durable `banking.autopilot` workflow now runs forecast-aware allocation after financial reconciliation. Recent unchanged inputs are reused to prevent repeated work.
- Money now includes a **Forecast** tab showing Personal/Pro protected floors, base/conservative minima, safe allocatable surplus, cash checkpoints, protected investment funding and provider-linked allocation actions.

See `docs/V0.9.6_FINANCIAL_ALLOCATION_FORECASTING.md` for the Phase-7 forecast, allocation, idempotency and verification contract.

## v0.9.7 Calls / Telephony — Phase 8

- Outbound calls now use a real Twilio Programmable Voice PSTN executor. Android `CallScreeningService` remains the separate inbound-device screening and logging layer; screening is never reported as having conducted a conversation.
- `TelephonyCall`, `TelephonyTurn`, and `TelephonyEvidence` provide an additive durable ledger. Phone numbers, webhook tokens, purposes, expected outcomes, call summaries, and transcripts are encrypted or hashed at rest as appropriate.
- Every outbound call intent is persisted before the Twilio create request. Network ambiguity becomes `creation_uncertain`; VAAPP never blindly dials again after an uncertain create outcome. Signed voice/status callbacks can recover the provider `CallSid` when Twilio accepted the request but the REST response was lost.
- Twilio webhooks are accepted only after `X-Twilio-Signature` validation against the exact public callback URL. Call-control turns use speech `<Gather>` and dynamic `<Say>` responses; call recording is disabled.
- The assistant explicitly identifies itself as an automated virtual assistant. The voice decision engine may gather routine information, take messages, chase status, collect reference numbers, and coordinate low-risk logistics, but it stops for payments, binding commitments, authentication/security steps, sensitive credentials, or comparable material decisions.
- Provider lifecycle and objective lifecycle are separate. A Twilio `completed` call is stored as `provider_completed_unverified` unless the counterparty's actual words provide source-backed confirmation of the expected outcome. Only then is `telephony_counterparty_confirmation` outcome evidence written and the VA objective completed.
- Clear `busy` / `no-answer` outcomes can schedule bounded later attempts. Timeouts during call creation never do. A maximum call duration and maximum turn count prevent open-ended autonomous calls.
- A one-minute reconciliation loop refreshes active provider calls, quarantines interrupted create intents, and starts only previously scheduled bounded retries.
- Android now includes a dedicated **Calls** workspace for provider readiness, autonomous call creation, status/verification state, encrypted transcript review, provider evidence, and explicit reconciliation. The client persists the draft idempotency key in secure local storage and reuses it after an uncertain HTTP response instead of silently creating another call intent.

See `docs/V0.9.7_CALLS_TELEPHONY.md` for the Phase-8 execution, safety, recovery and verification contract.


## v0.9.8 Purchasing / Travel / Logistics / Customer Service — Phase 9

- `FulfillmentProvider`, `FulfillmentRequest`, `FulfillmentAction`, and `FulfillmentEvidence` add one durable ownership layer for purchases, travel bookings, order/delivery tracking, returns, refunds, provider cancellations, and customer-service cases.
- Existing Gmail-derived `OrderRecord` and `SupportCase` rows are promoted into owned fulfillment objectives. Terminal order/support ledger state can complete the objective only as source-backed evidence; otherwise VAAPP keeps following up.
- External execution reuses the real Phase-5 Secure Browser and Phase-8 Twilio executors. A provider must be configured with an allowlisted browser portal and provider-specific recipe and/or a verified support phone; absence is `blocked_capability`, not simulated success.
- Fulfillment actions are persisted before provider preparation/dispatch. Browser `creation_uncertain` becomes `blocked_system` and is never blindly repeated.
- Purchases and travel bookings require a known positive EUR amount. Optional standing purchase/travel limits are explicit preauthorization; the amount must remain within both the configured single-transaction and monthly commitment limits. Unknown or over-limit monetary commitments become **Needs You**.
- A one-off payment authorization is stored as explicit fulfillment evidence and is accepted only for a known positive EUR amount. Provider SCA/MFA remains a separate unavoidable authentication handoff.
- Routine logistics, returns/refunds/cancellations, and customer-service work can execute unattended when a real provider recipe exists. Telephone support uses the verified telephony objective contract, so a completed call is not enough without counterparty outcome evidence.
- A five-minute reconciliation loop owns provider state, newly detected orders/support cases, follow-ups, ambiguity quarantine, and terminal evidence.
- Android exposes a dedicated **Fulfillment** workspace from the top bar for provider setup, purchasing/travel objectives, logistics/support ownership, reconciliation, and specific payment authorization.

See `docs/V0.9.8_PURCHASING_TRAVEL_LOGISTICS_CUSTOMER_SERVICE.md` for the Phase-9 execution, spending-authority and verification contract.


## v1.0.0 Professional Product Cleanup — Phase 10

- One semantic release contract now drives Android backend compatibility checks, Render deployment verification, repair flows, Product Status, backend metadata, APK naming, and tests.
- The phone deployment wizard no longer accepts the legacy `0.4.16` backend floor; v1.0 requires backend `1.0.0` or newer.
- **Product Status** reports backend compatibility, Autopilot health, endpoint failures, Needs You count, verified executor availability, and setup/capability gaps.
- Missing credentials/executors remain capability/setup gaps. They are never represented as completed work.
- Release metadata and phone-only setup documentation are aligned to the verified Phase-9 baseline and v1.0 candidate.
- No new paper-mode, mock-money, fake-call, or simulated browser completion path is introduced.

See `docs/V1.0.0_PRODUCT_RELEASE.md` for the v1.0 release and completion contract.
