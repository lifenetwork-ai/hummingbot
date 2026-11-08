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

Image `b090430c…`, build `d0791e563`, includes signal-lock fix `1916e048e`.
Canonical native **2,861 + 16 subtests**, all 27 available E2E in one all execution,
unchanged full load/soak3600/five faults all pass. Separate distinct-image
technical upgrade/takeover/rollback also passes using963 as a compatible test
predecessor; it does not endorse that failed artifact as production rollback.

All 97,800 main / 24,450 auxiliary healthy reads return HTTP200 without unexpected
stale responses. Mixed/burst/soak p99 **273.48/297.81/313.00 ms**; 4,885 simulated
fills/fees/positions reconcile, retained growth 30.33 MiB/64 MiB. Three healthy engine
probes recover without replacement. Continuous-quote coverage and exclusive
historical cause remain unproven; no real cloud deployment is implied.

Failed963 evidence stays in Git `d0791e563`; accepted a6 records in `8544488ef`,
at the [current release paths](../../reports/release/mm-runtime-followup-load.md).
Source/TDD and capture limits are in the [source report](../../reports/implementation/MM_RUNTIME_FOLLOWUP.md)
and [remaining-work register](README.md).
