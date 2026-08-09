# Full-Time VA v0.5.0 Autopilot — backend reliability tranche

Base inspected: `Demon-blood/VAAPP2@b8fad8c9f185423b2cda6b1349bceff3905f375e`.

This bundle implements the first v0.5.0 backend tranche:

- persistent PostgreSQL/SQLAlchemy workflow jobs;
- idempotency keys;
- durable statuses and result/error storage;
- dependency gating;
- worker leases + heartbeat;
- watchdog recovery for abandoned jobs;
- exponential retries and dead-letter state;
- existing Gmail, banking/autopay/reconciliation, Google Contacts, connector rules and document housekeeping routed through durable handlers;
- APScheduler reduced to a lightweight recurring *enqueue/worker/watchdog* clock, rather than owning business execution state.

The changes are additive to existing production tables. Existing data is not deleted or rewritten.

Apply `patches/v0.5.0-autopilot-backend.patch` from the repository root, then run the backend tests and deploy Render normally.
