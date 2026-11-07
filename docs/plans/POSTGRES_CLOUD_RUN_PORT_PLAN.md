# PostgreSQL / Cloud Run port — delivery status

Implementation complete. Exact phase checklists, Red/Green evidence, rejected
experiments and original image scopes are retained in the
[original plan](../../reports/README.md#historical-records) (Git: `docs/plans/POSTGRES_CLOUD_RUN_PORT_PLAN.md`). This file summarizes current delivery; the
[PostgreSQL guide](../postgres-runtime.md) and [Cloud Run guide](../cloudrun.md)
are the configuration/operations references.

## Delivered

- [x] Reuse the Hummingbot engine, connectors, recorder and V2 executors.
- [x] PostgreSQL durable orders, fills, fees, positions, sessions, commands, audit
  and recovery; snake_case schema, UTC storage and preserved Decimal precision.
- [x] Replayable SQL and startup migration gated by `ENABLE_AUTO_MIGRATION`, before
  trading permission; preserve migration/ownership/durable-before-send fences.
- [x] Central defaults and optional local `.env`, process-env precedence and
  injected Cloud Run environment/secrets. Keep strategy/profile YAML deployment-owned.
- [x] Ownership, terminal proof, duplicate-fill protection, restart recovery,
  bounded PostgreSQL workers/pools and reporting isolation.
- [x] Independent supervised API/engine processes in one deployment container/service.
- [x] Explicit compatible file/PostgreSQL configuration paths use the existing engine;
  no duplicated strategy implementation or required new external cache.
- [x] Native/PostgreSQL/source checks and image-local integration/load/fault evidence.

## Latest verification

Source `d9488143d` passes native2,780 + 11 subtests and full PG547. Image
`e00cef38…` from `ff6b85ded` separately passes the full local baseline and 60-minute
soak, five faults, four targeted E2E groups on first attempts and enhancement90.
Soak p99 410.20ms, zero unexpected healthy errors, 28.59MiB retained growth;
five soak probes plus mixed API1/transition engine1 recover. See the
[current image report](../../reports/release/mm-runtime-followup-load.md).
This does not imply real cloud deployment or unlimited uptime. Known external
acceptance and attribution limits are in the [remaining-work register](README.md).
