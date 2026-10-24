# PostgreSQL / Cloud Run verification evidence

This document separates implemented behavior from the remaining release scope.
The source plan is [POSTGRES_CLOUD_RUN_PORT_PLAN.md](plans/POSTGRES_CLOUD_RUN_PORT_PLAN.md).
All measurements below are local; no Cloud Run staging, cloud IAM/networking or
real exchange connection is claimed.

## Current evidence

- PostgreSQL integration: **59 passed** against disposable PostgreSQL 14, including
  real service processes, migration replay/drift/rollback, recorder transactions,
  CLI control, ownership connection termination and bounded cleanup.
- Selected CLI/config/model/recorder/OKX/engine/REST/V2 regression: **866 passed**.
  Deployment preflight policy tests additionally passed **4 cases**. Existing
  Pydantic/AsyncMock warnings remain; these runs have no failing tests.
- Deployment-image E2E: OKX spot through the existing V1 pure-market-making
  strategy passed on Linux amd64 with PostgreSQL 14, read-only roots and an
  independent HTTP/WebSocket simulator. Both REST and private WebSocket fixture
  authentication are checked. The default public Gate.IO rate-source path is
  simulated too; unhandled simulator endpoints fail the test.
- A pinned-manifest run exercised 12 accepted orders, two unique partial fills
  despite duplicate messages, exact scale-six Decimal amounts/fees, CLI PnL,
  clean shutdown/handover, fresh DML-only startup, SIGKILL, accepted-without-ACK
  effects, paused stale owner/session termination and PostgreSQL outage/restart.
  Read-only reconciliation cancels known recovered orders; prior uncertainty
  keeps HALT and does not automatically permit new risk.
- That local run sampled 310.8 MiB / 2 GiB and 2.13% Docker CPU, startup
  6.8–7.9s, shutdown 1.3–1.6s, and a reused-connection `SELECT 1` p95 of 0.310ms
  across 25 samples. These are workload samples, not peak measurements or a
  Cloud SQL capacity claim. The test caps the app at 1 CPU/2 GiB.

Machine-readable results, exact image/registry-manifest/config hashes, SQL-derived
assertions, history and sanitized logs/ledger are generated under
`build/e2e-offline/RUN_ID`. Those are disposable CI/local artifacts, not checked-in
runtime state. A packaging change requires another passing image run; the deploy
preflight compares its registry digest with the selected passing report.

Final P6 packaging evidence: `build/e2e-offline/20261024-181638-f923f2/result.json`,
registry manifest `sha256:3fdc613a25c51cd3e2d69b3fd0288744495027e4d4ae04e90aa15e057377e376`,
image/config ID `sha256:d8fb4e2feb8a909a0d6db82342df7afb2e9cc1ad2a1609246d7d57bc7c6584bf`.
The retained synthetic service example passes the concrete offline preflight against
that report; it names no provisioned cloud resources.

## Acceptance mapping

`PG` means the actual production SQL/ORM/recorder against real PostgreSQL;
`process` means a real service/CLI subprocess; `image` means the full deployment
image/engine/connectors and independent simulator. Pending entries remain release
work, even where smaller-layer coverage already exists.

| Criterion | Evidence / remaining work |
|---|---|
| A01 | PG `test_bootstrap`: empty DB; image initial service bootstrap |
| A02 | PG populated schema/data replay 100 times |
| A03 | PG concurrent migration lock / bounded wait; image overlap |
| A04 | PG file transaction failure, cancellation and actual backend termination |
| A05 | PG catalog drift cases for wrong definitions/invalid indexes/constraints |
| A06 | Runtime/environment credential units, PG connection failures; image dummy REST/WS authentication |
| A07 | PG all registered models round-trip; original Decimal range/scale retained |
| A08 | PG fill-before-create, duplicate/conflicting fill and actual connection-loss rollback; image duplicate partial fills |
| A09 | Separate CLI processes and image CLI containers, no shared runtime layer |
| A10 | PG concurrent CAS/stale acknowledgment; image stale command rejection |
| A11 | Process/image stopped state survives SIGTERM/handover/restart |
| A12 | Image real PID 1 shutdown and preserved desired state; watchdog process test |
| A13 | Image accepted-without-ACK plus stale-owner pause/session loss; persistent HALT |
| A14 | PG throttle/transport barriers and stale write fencing; image pause/resume/session handover |
| A15 | PG statement/pool budgets and persistence latch; image actual DB outage and restart HALT |
| A16 | V1 image verified; V2/controller/local hot reload regression verified; V2/perpetual image smoke remains pending |
| A17 | Process/image read-only YAML/root; no SQLite, PID, connector files or persistent log/data volumes |
| A18 | Same-code revision overlap verified; distinct compatible old/new-image upgrade remains pending |
| A19 | Runbook forbids down migrations/legacy images; actual image rollback scenario remains pending |
| A20 | Optional SQLite importer is not requested or delivered; not a release prerequisite |
| A21 | PG additive scripts and replay covered; explicit previous-canonical populated upgrade/image compatibility evidence remains to be completed |
| A22 | Dotenv tests cover deterministic path, overrides, explicit missing file and literal secret characters |
| A23 | Real process with `K_SERVICE` and no dotenv; environment-only image startup; Secret Manager itself unverified |
| A24 | PG restricted role plus image fresh startup with DML-only role and auto migration false |
| A25 | PG empty/drift cases; real service process refuses an empty schema without DDL |
| A26 | Runtime settings boolean/default/precedence tests |
| A27 | PG explicit migration executes despite automatic flag false |
| A28 | Environment credential/bootstrap/connect/balance tests; image authentication without keystore/password |
| A29 | Image revision/config hashes and read-only update policy; different deployed YAML/secret version image scenario remains pending |
| A30 | PG canonical naming/catalog/ORM checks and model round-trip |
| A31 | PG explicit legacy cutover preserves rows, keys and sequences, replay and failure rollback |
| A32 | PG old/both-name cases fail without creating parallel canonical tables |
| A33 | V1 PMM spot image prices/amounts/fills/fees/PnL independently asserted; perpetual positions/PnL image scope pending |
| A34 | Image fresh replacement reuses only PostgreSQL, simulator truth, immutable YAML and injected fixture credentials |

## Remaining complete-release verification

- Extend the same simulator/harness with the selected OKX perpetual and existing
  V2 script/controller smoke, including position holds and PnL/risk restoration.
- Add the distinct compatible old/new-image additive upgrade and rollback cases.
- Exercise controlled WebSocket disconnect/reconnect and recorder transaction
  interruption inside the full image, with independent exchange fill truth.
- Bind the final reviewed deployment configuration to the final passing image
  manifest and complete the plan's remaining scope/checklists. Do not relabel
  smaller-layer tests or same-image revision labels as those missing scenarios.

The application still uses the original scale-six integer Decimal representation;
the image assertions above use exactly representable values. This is not proof of
arbitrary sub-micro precision across all exchange markets. No general live-executor
or custom timer reconstruction after crashes is claimed: a persisted runtime marker
forces HALT rather than silently resetting that risk state.
