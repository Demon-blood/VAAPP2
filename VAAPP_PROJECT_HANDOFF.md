# VAAPP Project Handoff

Updated: 2026-08-13
Repository: `Demon-blood/VAAPP2`
Branch: `main`
Verified repository commit before Phase 1 upload: `b92ac998359057cfd1daba56576e0da44eef80b1`
Verified repository release before Phase 1 upload: backend `0.8.1` / Android `0.8.1+32`
Latest verified repository CI before Phase 1 upload: GitHub Actions run #27 — SUCCESS
Current local release candidate: backend `0.9.0` / Android `0.9.0+33`
Current phase: **Phase 1 — Autonomous Core**
Phase status: **implemented locally; awaiting full GitHub CI after user upload**

## Product objective

VAAPP (Full-Time VA) must become a persistent autonomous administrative operator, not a chatbot, demo, paper-mode simulator, or approval-heavy assistant. Routine work should execute automatically. **Needs You** is reserved only for unavoidable provider authentication/security, a genuinely material user decision, or a physical-world action with no connected executor.

The shared lifecycle is:

`Observe -> Understand -> Own objective -> Policy/capability -> Plan -> Execute -> Verify -> Follow up/recover -> Reconcile -> Complete -> Learn`

No external action is complete because an API call merely returned success; real/durable postconditions must be verified. No fake/placeholder/simulation executor may be presented as implemented.

## Non-negotiable project rules

- Maximum unattended automation.
- Durable state across restarts.
- Stable idempotency/correlation for external work.
- No blind retry after ambiguous external outcomes.
- Audit evidence for decisions, execution, verification and recovery.
- No secrets, private banking identifiers, or raw private statements in the public source tree.
- Kraken withdrawals remain disabled/not implemented.
- Revolut Pro/business money may not directly fund the personal Kraken account.
- Keep critical Beobank standing orders for now; learn/reserve/forecast/verify them.
- Keep Revolut's own scheduled portfolio contribution; pre-fund/reconcile around it.
- Do not reintroduce global paper/simulation mode.

## Verified v0.8.1 baseline

The repository main is still `b92ac998...` until the Phase-1 upload is applied. Run #27 passed backend tests, Flutter analysis/tests, signing, Android release build, and APK prerelease publishing. v0.8.1 already contains the dedicated **Money -> Investments** dashboard and the finance/investment work from previous phases.

## Phase 1 v0.9.0 implementation

The local v0.9.0 candidate extends the verified v0.8.1 baseline without merging the older experimental all-in-one v0.9 worktree. Phase 1 contains only the shared autonomous core and its Operations UI.

### Durable core tables

- `VAEvent` — idempotent event intake.
- `VAObjective` — durable goal/state/source/risk; includes real user-intervention count.
- `VAObjectiveStep` — ordered execution steps with unique idempotency key, persisted policy/capability decision, workflow reference, retry state and verifier.
- `VAOutcomeEvidence` — independently stored postcondition evidence.
- `VAFollowUp` — durable wait/follow-up state.
- `AutonomyMetricDaily` — event/objective/recovery metrics.

All are additive tables; existing task/payment/workflow data is not destructively migrated.

### Autonomous services

New services:

- `backend/app/services/autonomous_core.py`
- `backend/app/services/autonomy_metrics.py`
- `backend/app/services/capability_registry.py`
- `backend/app/services/va_policy.py`

Phase 1 reuses the existing `WorkflowRun` / `WorkflowJob` worker as the real executor. It does not create a parallel pretend executor.

Implemented behavior:

- idempotent event -> objective conversion;
- deterministic objective/step persistence;
- central fail-closed policy decision;
- registry of currently shipped real executors/connections;
- stable step idempotency key becomes durable workflow correlation key;
- manual/scheduled VA cycles dispatch through existing `dispatch_intent`;
- completed workflow runs require verification evidence;
- superseded workflows count as complete only when replacement work is independently completed;
- bank payment/SCA and own-account transfer state reconcile back into the original objective;
- user-auth dead letters resume automatically after the corresponding real connection becomes available again;
- unknown/unsafe failures become system blockers instead of being blindly replayed;
- routine tasks whose domain executor has not yet migrated remain `blocked_capability`, never falsely `needs_user`;
- due follow-ups create durable work but **do not** fake a sent message before the real communications executor is implemented;
- autonomy percentage is not fabricated as 100% when no completed outcomes exist.

### Automatic execution

- new durable job handler: `va.core.cycle`;
- scheduler enqueues it every minute when global automation and `va_autonomous_core_enabled` are enabled;
- existing workflow worker executes it.

### API

- `GET /api/va/overview`
- `GET /api/va/capabilities`
- `GET /api/va/objectives`
- `GET /api/va/objectives/{objective_id}`
- `POST /api/va/objectives/{objective_id}/recheck`
- `POST /api/va/run`

### Android

**Work** now has an eighth tab: **Operations**. It displays the objective ledger, strict Needs You queue, real capability status, autonomy metrics, verified evidence counts, manual Run VA dispatch, and recheck after unavoidable authorization.

## Current validation

Completed locally:

- Python `compileall` — PASS.
- Phase-1 static/release + selected existing regression contracts — **26 passed**.
- Workflow YAML parse — PASS.
- Android pubspec YAML parse — PASS.
- GitHub workflow shell blocks — **6/6 PASS**.
- modified Dart lexical/bracket structure and Operations wiring — PASS.
- exact private Kraken IBAN/statement identifier scan — PASS.
- raw PDF/XLSX source scan — PASS.

The dependency-backed Phase-1 behavioral tests are included, but this local runtime does not have `aiosqlite`. They therefore must run in GitHub Actions, whose backend CI installs `backend[dev]`. **Do not mark Phase 1 fully complete until the uploaded v0.9.0 commit passes the full GitHub Actions workflow.**

## Phase-1 behavioral tests waiting for GitHub CI

They cover:

- event/objective idempotency;
- real `run_va` durable workflow dispatch and verified completion;
- bank SCA Needs You -> automatic completion reconciliation;
- routine task remains VA-owned instead of falsely becoming Needs You;
- user-auth dead letter vs unknown system failure classification;
- user-auth dead letter automatically resumes after the real Google connection returns;
- due follow-up persists as work without faking delivery;
- release/routes/no-simulation contracts.

## Phase roadmap

1. Autonomous Core — **current v0.9.0 candidate, awaiting GitHub CI**
2. Inbox & Communications Ownership
3. Calendar & Scheduling Agent
4. CRM / Relationship Memory
5. Secure Browser / Portal Operator
6. Documents / Forms / Deadlines
7. Financial Allocation & Forecasting
8. Calls / Telephony
9. Purchasing / Travel / Logistics / Customer Service
10. Professional Product Cleanup / v1.0 Readiness

## Next exact action

1. Upload the Phase-1 v0.9.0 overlay ZIP to `main`.
2. Inspect the resulting GitHub Actions run.
3. Fix any real CI/build/test failure before declaring Phase 1 complete.
4. When CI is green, regenerate this handoff with the new repository commit/run and set Phase 1 to complete.
5. Begin **Phase 2 — Inbox & Communications Ownership** from that green v0.9.0 baseline.

## New-conversation resume procedure

Provide `VAAPP_PROJECT_HANDOFF.md` and `VAAPP_PROJECT_STATE.json`, then say:

> Continue VAAPP from the current phase in these handoff files. Verify GitHub main before making changes.

The repository is always the source of truth; verify `main` before editing rather than assuming the handoff's last SHA is still current.
