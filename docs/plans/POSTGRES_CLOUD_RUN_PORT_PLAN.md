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

New image `96368970…`, build `8544488ef`, packages subsequent recorder/quote/
anchor allocation work. The unchanged full gate **fails burst main/auxiliary p99**
at 1,084.01/1,151.07 ms against 1,000 ms. Warmup/mixed/soak3600/five faults pass;
4,884 simulated fills reconcile and retained growth is 26.09 MiB/64 MiB. The older missing-credential fixture is fixed; corrected `all` later fails
inventory shutdown, with remaining individual targets passing; upgrade remains unrun without a compatible prior image.

The previous accepted `a6fe88c0…` gate remains in Git `8544488ef` at current release
report/evidence paths. See the [current image report](../../reports/release/mm-runtime-followup-load.md)
and [source verification](../../reports/implementation/MM_RUNTIME_FOLLOWUP.md).
This does not imply real cloud deployment or unlimited uptime. Known external
acceptance and attribution limits remain in the [register](README.md).

Shutdown follow-up: corrected `all` E2E passes fourteen groups before a nonzero
inventory-skew shutdown; all thirteen remaining/targeted groups pass, covering twenty-seven unique
groups across attempts. The failed `all` remains failed. A real-signal
TDD reproducer confirms an Event-lock hazard, fixed in source `1916e048e` (75 related
passes). This is a possible mechanism, not proof of the old exit's unique cause.
`96368970…` predates the fix; new-image qualification remains open. See the
[runtime checklist](MM_RUNTIME_FOLLOWUP_REMEDIATION_TDD_PLAN.md).
