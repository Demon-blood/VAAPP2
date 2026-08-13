# VAAPP Project Handoff

Updated: 2026-08-13
Repository: `Demon-blood/VAAPP2`
Branch: `main`
GitHub source-of-truth commit at packaging time: `b92ac998359057cfd1daba56576e0da44eef80b1`
Latest verified GitHub Actions baseline: run #27 — SUCCESS
Current local cumulative release candidate: backend `0.9.1` / Android `0.9.1+34`
Current phase: **Phase 2 — Inbox & Communications Ownership**
Phase 2 status: **implemented locally, awaiting upload/full GitHub CI**

## Product objective

VAAPP (“Full-Time VA”) must become a real autonomous full-time virtual assistant, not a chatbot, paper-mode simulator, placeholder UI, or approval-heavy workflow tool.

Operating contract:

`Observe -> Understand -> Own objective -> Check policy/capability -> Plan -> Execute -> Verify -> Follow up/recover -> Reconcile -> Complete -> Learn`

Routine work executes automatically. **Needs You** is reserved for genuinely unavoidable user authentication/security authorization, a materially consequential decision that cannot safely be pre-authorized, or physical/manual work with no connected executor.

No fake executor, fake delivery, placeholder provider, or simulation-only action may be presented as complete.

## Repository / build status

GitHub `main` is still the v0.8.1 commit `b92ac998359057cfd1daba56576e0da44eef80b1`. Run #27 passed backend tests, Flutter analyze/tests, signing, Android release build, and prerelease publishing.

Phase 1 and Phase 2 have not yet been uploaded to GitHub. Therefore the **v0.9.1 Phase-2 upload package is cumulative** and includes the Phase-1 Autonomous Core. The user does not need to upload v0.9.0 first if applying the v0.9.1 cumulative package.

## Phase 1 — Autonomous Core included in v0.9.1

Implemented:

- durable `VAEvent` ingestion with idempotent event keys
- persistent `VAObjective` ownership lifecycle
- persistent ordered `VAObjectiveStep` execution
- `VAOutcomeEvidence` postcondition evidence
- durable `VAFollowUp`
- autonomy metrics
- operation preferences
- central `va_policy` decision layer
- live capability registry based only on real connected executors
- real dispatch through existing `WorkflowRun` / `WorkflowJob` workers
- stable idempotency/correlation keys
- verification before objective completion
- transient failure recovery
- provider-auth recovery after capability becomes healthy
- strict Needs You distinction
- Android Work -> Operations view

Important Phase-1 state semantics:

- provider/bank authorization or material user decision -> `needs_user`
- missing software/provider executor -> `blocked_capability`, remains VA-owned
- unknown application/system defect -> `blocked_system`
- transient provider/network failure -> wait/retry/reconcile automatically
- completed real-world outcome -> completed only with verification evidence

## Phase 2 — Inbox & Communications Ownership implementation

### Gmail event-driven mailbox ownership

New persistent `GmailMailboxState` stores:
- Gmail history cursor
- watch Pub/Sub topic
- watch expiration
- last push timestamp
- last history sync
- last full recovery sync
- last watch renewal
- last error

`POST /api/google/pubsub` validates the configured verification token, decodes the Gmail Pub/Sub payload, **persists a `gmail.history.sync` WorkflowJob before acknowledging**, then returns quickly.

`gmail.history.sync` requests Gmail history additions after the durable cursor and processes newly added INBOX messages. A stale history cursor falls back to full recovery sync and refreshes the current cursor. Periodic full Gmail sync remains the recovery mechanism for delayed/dropped push notifications.

`gmail.watch.ensure` renews the real Gmail push watch through the configured Google Pub/Sub topic.

### Durable ambiguity-safe Gmail sending

New `GmailOutboundMessage` persists outbound intent before provider execution.

Rules:
- deterministic RFC Message-ID derived from the stable idempotency key
- intent committed before Gmail POST
- real Sent-mail reconciliation before any send attempt/retry
- timeout/reset/429/5xx ambiguity -> `creation_uncertain`, never blind duplicate send
- reconciliation searches Gmail Sent using deterministic RFC Message-ID
- SENT label and RFC Message-ID must match before verification evidence is recorded
- real Gmail `threadId` plus `In-Reply-To` / `References` preserve conversation threading

Safe email replies are no longer sent inline by `email_processor`. They become Phase-1 durable objectives with `gmail_send_reply` steps and Gmail verification postconditions.

Legacy queued safe replies are migrated into the durable objective path rather than sent directly.

### Persistent conversation ownership / follow-up

New `VACommunicationThread` records:
- channel/provider/thread identity
- owning objective
- participant/title
- whether VA or counterparty is expected to act
- last inbound/outbound/activity
- next follow-up
- context

After a verified outbound communication, the VA can schedule a durable follow-up while waiting on the counterparty. An inbound response records `counterparty_response` evidence, cancels pending follow-ups, completes the wait postcondition, and closes the objective only if all real non-wait postconditions are satisfied.

A due follow-up continues the **same objective**; it does not create fake unrelated work.

### Android SMS — real execution/evidence

SMS is a real background-initiatable channel through Android `SmsManager`.

`VaSms.kt` uses per-part SENT/DELIVERED PendingIntents. New `SmsStatusReceiver.kt` records carrier evidence and reports:
- `sms_sent`
- `sms_delivered`
- definitive send failure
- delivery failure after send

Local carrier evidence survives a temporary backend outage and is reposted before any future worker may resend. This is the duplicate-prevention boundary for Android/network ambiguity.

### Notification-app replies — real but bounded

For live notification actions, `VaNotificationListenerService` uses the actual Android `RemoteInput` action. After `actionIntent.send`, it records `remote_input_dispatched` evidence.

The app **does not call this delivered** and does not pretend it can arbitrarily initiate a future WhatsApp/Signal/Telegram/Messenger message after the live notification action is gone.

### Background reconciliation worker

New `VaCommunicationPendingWorker.kt` uses Android WorkManager. It:
1. reposts locally stored RemoteInput evidence
2. reposts local SMS carrier evidence
3. fetches pending backend communication actions
4. background-dispatches only real SMS actions

Notification-app actions are not reconstructed/replayed after the live notification action disappears.

### Backend communication evidence

`CommunicationDeliveryEvidence` stores provider/device proof independently of requested actions.

Accepted action result states include dispatched/sent/delivered/completed/failure states. A later delivery failure does not turn an already proven send into an unsafe retryable unsent action.

If an SMS dispatch outcome remains unknown for too long and no evidence exists, the objective becomes `blocked_system` rather than risking a duplicate resend.

### Phase-2 Android UI

Communications now shows:
- Gmail push/watch health
- Gmail history cursor / latest sync/push/error
- owned communication counts
- waiting-on-counterparty count
- conversation ownership list
- existing phone/device communication controls and recent event history

New/used API routes:
- `GET /api/google/mailbox-status`
- `POST /api/google/pubsub`
- `GET /api/communications/actions/pending`
- `GET /api/communications/threads`

## Finance constraints that remain mandatory

- Beobank Personal = operating/salary/critical obligations
- Revolut Personal = personal spending + Revolut investment funding
- Revolut Pro = professional/Uber operating account
- Revolut Pro must never directly fund personal Kraken
- Pro -> Personal = owner draw
- Personal -> Pro = owner contribution
- Personal -> Kraken = investment contribution
- safety/emergency reserve is never investable
- keep bank-managed critical standing orders for now; VA predicts/reserves/verifies
- keep Revolut native scheduled investment contributions; VA pre-funds/reconciles
- Kraken automatic funding/trading defaults OFF until configured
- Kraken withdrawals remain disabled/not implemented
- do not hard-code private Kraken banking details or any secret/PII

## Full roadmap

1. Autonomous Core — implemented locally; cumulative in v0.9.1
2. Inbox & Communications Ownership — **current v0.9.1 candidate, awaiting GitHub CI**
3. Calendar & Scheduling Agent
4. CRM / Relationship Memory
5. Secure Browser / Portal Operator
6. Documents / Forms / Deadlines
7. Financial Allocation & Forecasting
8. Calls / Telephony
9. Purchasing / Travel / Logistics / Customer Service
10. Professional Product Cleanup / v1.0 readiness

Later scope also includes subscription/household administration, project/business administration, bookkeeping-grade reconciliation, renewals/forms, administrative health workflows, file/knowledge management, proactive anomaly/deadline detection, negotiation within policy, external physical-service delegation where available, credential/session health, provider capability health, and autonomy-quality metrics.

## Phase completion rule

A phase is not considered complete merely because code exists. It needs:

1. real execution path
2. policy enforcement
3. durable state
4. idempotency
5. outcome verification
6. retry/recovery semantics
7. audit trail
8. automated tests
9. GitHub Actions green
10. no embedded user secrets/private data

Local contract tests for Phase 2 pass. Dependency-backed DB behavior tests are included but this local environment is missing `aiosqlite`; full behavior plus Flutter/Android compilation must therefore be validated by GitHub Actions after upload.

## Exact next action

Upload the cumulative `VAAPP2-v0.9.1-phase2-inbox-communications-upload.zip` to GitHub `main`. Then:

1. inspect the new commit
2. inspect GitHub Actions
3. fix any backend/Flutter/Kotlin/Gradle failure until green
4. only then mark Phase 1 + Phase 2 fully complete
5. begin **Phase 3 — Calendar & Scheduling Agent** from that verified green commit

## Moving to a new conversation

Provide this file and `VAAPP_PROJECT_STATE.json`, then say:

> Continue VAAPP from the current phase in these handoff files. Verify GitHub main before making changes.

The new conversation must verify `Demon-blood/VAAPP2` `main` first and treat GitHub as source of truth rather than assuming the local candidate was uploaded.
