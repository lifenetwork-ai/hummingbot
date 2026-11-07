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

Image `a6fe88c0…` from `745a86e3c` packages the fresh write-fence projection and
reporting-copy follow-ups. It passes the unchanged full local baseline,
60-minute soak/five faults, four targeted E2E groups, enhancement90 and separate
activity300. Soak p99 406.14 ms, zero unexpected healthy errors, 24.26 MiB retained
growth and 4,885 exact simulated fills. Eleven soak engine and three mixed engine
probes recover; probe frequency and burst p99 regress versus rollback `e00cef38…`.
No uniform speedup or continuous-quote proof is claimed. See the
[current image report](../../reports/release/mm-runtime-followup-load.md) and
[separate source verification](../../reports/implementation/MM_RUNTIME_FOLLOWUP.md).
This does not imply real cloud deployment or unlimited uptime. Known external
acceptance and attribution limits are in the [remaining-work register](README.md).
