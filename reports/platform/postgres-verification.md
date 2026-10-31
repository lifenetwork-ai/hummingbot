# PostgreSQL / Cloud Run verification evidence

This document records the completed local verification and its deployment limits.
The source plan is [POSTGRES_CLOUD_RUN_PORT_PLAN.md](../../docs/plans/POSTGRES_CLOUD_RUN_PORT_PLAN.md).
All measurements below are local; no Cloud Run staging, cloud IAM/networking or
real exchange connection is claimed.

## Optimized Cloud Run runtime release evidence

Verified on 2026-10-25. The selected image is **1,363,640,076 bytes (1.36 GB)**,
down from 4,637,764,625 bytes (4.64 GB), a **70.60% reduction**. The builder and
runtime dependency versions are reused; compiler/test tooling and base Conda are
excluded from the final image. Valid runtime bytecode caches are retained.

- **950 regression tests passed:** 118 configuration/storage, 11 performance and
  821 CLI/model/connector/runner/engine/V2/packaging tests. Packager coverage is
  **90.18%**. Existing Pydantic/AsyncMock warnings remain.
- **Runtime smoke passed** in a network-disabled, read-only container: native
  imports, Numba JIT, TA-Lib, TLS trust, 63 connector settings, CLI and nine SQL
  scripts. The current-image size gate is 1.5 GiB.
- **All five E2E scenarios passed**, with `passed=true` and
  `release_scope_complete=true`:
  `build/e2e-offline/20261025-011532-ec2194/result.json`.
  Schema/migration policy, V1 spot fault recovery, V2 spot/perpetual positions/PnL,
  and compatible upgrade/rollback passed using the actual slim deployment image.
- Tested registry manifest:
  `sha256:e9c8edf0cfbdef064f3c5c1f39868f79457eadf2342c4c1b136efb6714f20313`.
  Image/config ID:
  `sha256:20d8a0f2e8d53b12cb50565188e490f74e71c1c8b6fe5245996bcd164ecbc00c`.
- The compatible full-image predecessor was rebuilt from source base `e9a9fe49f`
  using its original Dockerfile. Image/config ID:
  `sha256:c8633fd7f09ebb32ae112b391090ec70f406ee0119ca380d0683c997bdc9a831`.
  Its tested manifest is
  `sha256:18da0ab394e9d1caf951dcf4d63dfe65ea4a2f3d835c6e16af5665785ef06a56`.
- The synthetic `deployment-review.example.yaml` beside the report passes
  preflight against this exact manifest. It names no real cloud resources.
- **1,053 application Python/native/SQL/YAML assets match the full image
  byte-for-byte.** All retained Python distribution versions match; inventories,
  hashes, test logs and size measurements are in
  `build/cloudrun-image-optimization/`.
- The previous release's **72 native PostgreSQL integration tests** remain
  historical evidence; they were not rerun for this packaging change. The new
  image E2E uses real PostgreSQL 14, the production driver and migrations/recovery.
  SQL/storage implementation is unchanged.

Local final-image startup samples are **6.884–8.407s**, clean shutdown
**0.995–1.415s**. In the upgrade scenario the new image starts in **7.636s**,
versus **8.912s** for the full predecessor and **7.608s** on rollback. Spot's
steady sample is **235.2 MiB / 2 GiB**, Docker CPU **1.93%**, and reused-connection
`SELECT 1` p95 **0.339ms**. These are local workload samples, not peaks or cloud
measurements. The 1.19 GB no-bytecode-cache trial was rejected because startup
rose to 15.3–17.2s despite passing correctness gates. See
[image optimization](cloudrun-image-optimization.md) for the packaging tradeoff.

## Earlier environment/backend and unqualified SQL release evidence

Follow-up verified on 2026-10-25: centralized defaults, preferred `ENV_FILE`,
explicit `STORAGE_BACKEND=postgres|file`, preserved original local SQLite/JSON
behavior, and removal of `public.` qualifiers from all nine migration/cutover SQL
files. The runner still targets the canonical schema through its search path.

- **944 selected regression tests passed:** 116 configuration/storage, 11
  performance and 817 CLI/model/connector/runner/engine/V2 tests. This includes
  seven deployment-policy tests and local SQLite/JSON round-trips with an ignored
  PostgreSQL URL in explicit file mode.
- **72 PostgreSQL tests passed:** fresh and populated replay, 100 replays,
  legacy cutover, DML-only startup, drift/rollback and the new non-default
  session-search-path regression. No new tables or renamed catalog objects are
  introduced by removing the SQL qualifiers.
- **All five image E2E scenarios passed**, `passed=true` and
  `release_scope_complete=true`:
  `build/e2e-offline/20261025-001145-0d9d95/result.json`.
- Manifest for that earlier release:
  `sha256:7fda2b9596c8ac985ecc2578d77cebf6dd99016570fc9085ac4ab127fb8d603d`.
  Image/config ID:
  `sha256:98fb93cb91ee111782e1cb882b2e2e58fea02905082fde37a951359f3c46b7b3`.
- Compatible predecessor rebuilt from `git archive 2301ef6a6`:
  `sha256:90adabf8e82bfc31abf49fb09a11e37e397156db7007fc322f7ce0d685ca1d40`.
  This is a new build of the prior source; the old historical images disappeared
  from local Docker during preparation. The current image was reloaded from cache
  and temporary containers retained both images for the successful retry; those
  containers have been removed.
- E2E explicitly selects PostgreSQL and additionally proves file-mode foreground
  startup fails before creating `bot_state` or accepting any exchange order.
  Schema policy, V1 fault recovery, V2 spot/perpetual positions/PnL, and compatible
  upgrade/rollback preserve the same economic assertions as the previous release.
- The concrete synthetic deployment example beside the report passes preflight
  against this exact manifest and requires PostgreSQL. It names no real cloud
  resources. No cloud deployment or real exchange operation was performed.

Sanitized source/Red/Green/regression/PG validation artifacts are in
`build/env-storage-validation/`. Fourteen Python/SQL/config image assets match
source byte-for-byte. The ignored local `.env` had eight `LIFE_` keys renamed with
their values preserved; their old migration-tool meanings are distinguished from
current runtime settings in [runtime operations](../../docs/postgres-runtime.md).

Local image samples: startup 6.605–8.032s, shutdown 0.929–1.617s, spot steady
236.3 MiB / 2 GiB and 2.31% Docker CPU; reused-connection `SELECT 1` p95 0.309ms.
These are workload samples, not peak or Cloud Run/Cloud SQL measurements.

## Earlier post-port review release evidence

Performance/recovery changes are reviewed in
[postgres-performance-review.md](postgres-performance-review.md). **926 regression
tests, 71 PostgreSQL integration tests and all five rebuilt-image E2E scenarios
passed**. Current report: `build/e2e-offline/20261024-220352-d8f55f/result.json`,
with `passed=true` and `release_scope_complete=true`.

- Registry manifest: `sha256:70b97bea620732d53f228cb16dde63128b9177fc4e08d8c634e466c662a7879b`.
- Image/config ID: `sha256:eb91e773e563377562797303740fdf8baf6b889bce14b8e04bcda7f5d38386af`.
- Compatible predecessor rebuilt from `git archive ddf77c4a7`:
  `sha256:a505ebdc1e40d435be5920f7b5f3fbdf14aa877df18d2286dc99290013990fff`.
  This is a new build of the prior source, not the historical image digest below.
- Schema policy, V1 spot fault recovery, V2 spot/perpetual economics and distinct
  image upgrade/rollback all passed. The new image applies packaged index script
  `008` while the previous image owns the database; the previous application also
  passes migration-disabled startup on the expanded schema during rollback.
- Spot: 14 accepted identities, three unique fills, reconnect and interrupted
  recorder recovery. Both V2 paths preserve the 0.1-base position and restored
  PnL peak 12.345678. Upgrade/rollback preserves recorded economics.
- Local startup samples 6.936–8.195s, shutdown 0.837–1.493s; spot steady sample
  237.1 MiB / 2 GiB and 2.29% Docker CPU. Reused-connection `SELECT 1` p95 0.273ms.
  These are sampled local workloads, not peak or cloud performance measurements.

The synthetic `deployment-review.example.yaml` beside the report passes the
concrete offline deployment preflight against this exact manifest. It references
no real cloud resources. All five modified production assets match the built
image byte-for-byte; hashes are in
`build/postgres-performance/image-source-sha256.json`.

Only the local image-push preparation timeout was extended from 240s to 600s after
Docker Desktop exceeded it while compressing large layers. Application startup,
shutdown, ownership and recovery test deadlines were not relaxed.

## Current evidence

- PostgreSQL integration: **72 passed** in the preceding env/SQL release against disposable PostgreSQL 14, including
  real service processes, migration replay/drift/rollback, recorder transactions,
  CLI control, ownership connection termination, populated prior-schema upgrade,
  rejection of all later recorder writes after rollback and bounded cleanup.
  The post-port review adds 10 cases for large-history query budgets, exact PnL,
  position selection/overrides, invalid-history cursor cleanup, recent per-market
  recorder warmup, additive indexes and the fully fenced fill transaction.
  The env/SQL follow-up adds the non-default search-path case.
- Selected runner/CLI/config/model/recorder/OKX/engine/REST/V2 regression: **950 passed**
  (`make test-cloudrun-regression`: 118 configuration/storage, 11 performance, 821 other
  selected tests, including 7 deployment policy tests). Existing
  Pydantic/AsyncMock warnings remain; these runs have no failing tests.
- P6 baseline deployment-image E2E: OKX spot through the existing V1 pure-market-making
  strategy passed on Linux amd64 with PostgreSQL 14, read-only roots and an
  independent HTTP/WebSocket simulator. Both REST and private WebSocket fixture
  authentication are checked. The default public Gate.IO rate-source path is
  simulated too; unhandled simulator endpoints fail the test.
- A pinned-manifest run exercised 12 accepted orders, two unique partial fills
  despite duplicate messages, exact scale-six Decimal amounts/fees, CLI PnL,
  clean shutdown/handover, fresh DML-only startup, SIGKILL, accepted-without-ACK
  effects, paused stale owner/session termination and PostgreSQL outage/restart.
  Startup reconciliation cancels known recovered orders; prior uncertainty
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

Historical P6 packaging evidence: `build/e2e-offline/20261024-181638-f923f2/result.json`,
registry manifest `sha256:3fdc613a25c51cd3e2d69b3fd0288744495027e4d4ae04e90aa15e057377e376`,
image/config ID `sha256:d8fb4e2feb8a909a0d6db82342df7afb2e9cc1ad2a1609246d7d57bc7c6584bf`.
The retained synthetic service example passes the concrete offline preflight against
that report; it names no provisioned cloud resources.

Pre-review release evidence: `build/e2e-offline/20261024-190244-08c3ef/result.json`,
registry manifest `sha256:e9f6dc7a5105553723d46c5194c902e47ade792fc7c62d1b0e8bf77d5328c714`,
image/config ID `sha256:02084a71adf593a4205d9a03ad0ad586c31dafaae07cd3b1daeaa9de36687f0f`.
All five scenarios passed and `release_scope_complete=true`. The concrete
synthetic deployment example in that directory passes offline review against
this exact manifest; it is not a provisioned cloud deployment.

- `schema`: auto-migration false refuses empty/drifted schemas before any exchange
  request; repaired compatible schema starts successfully.
- `spot`: 14 unique accepted identities, three fills, duplicate/out-of-order
  delivery, public/private WebSocket reconnect, lost ACK, owner/session loss,
  process pause/SIGKILL, database outage and recorder transaction termination.
  Fresh recovery retains HALT and recovers the rolled-back fill exactly once:
  0.14 total base, 0.014 quote fees, CLI PnL -0.014 at the recorded fill price.
- `v2-spot` and `v2-perpetual`: existing `pmm_simple` controller and
  `v2_with_controllers.py`, exact prices/quantities/fees, one 0.1-base position
  hold, restored PnL peak 12.345678 and no restart of a drawdown-stopped controller.
  The perpetual wire quantity is 10 contracts with fixture contract size 0.01.
- `upgrade`: distinct P6/current images, additive nullable test-column script
  executed by the production runner during overlap, ownership handover, changed
  immutable YAML prices and compatible application rollback preserving old fills
  and the new column's value. No test column ships in production SQL. Packaged
  scripts 006–007 upgrading populated 001–005 have a separate real PG test.

Final local samples: startup 6.77–8.81s, clean shutdown 1.00–1.63s, spot steady
memory 237 MiB / 2 GiB, Docker CPU 1.86%, reused-connection SELECT 1 p95 0.312ms
(25 samples). These are workload samples, not peaks or cloud measurements.

## Acceptance mapping

`PG` means the actual production SQL/ORM/recorder against real PostgreSQL;
`process` means a real service/CLI subprocess; `image` means the full deployment
image/engine/connectors and independent simulator. `scripts/test_e2e_offline.py`
implements the five named image scenarios. Component tests live under
`test/integration/postgres`; the reproducible unit/regression selection is
`make test-cloudrun-regression`. Scope exclusions below are explicit.

| Criterion | Evidence / scope |
|---|---|
| A01 | PG `test_bootstrap`: empty DB; image initial service bootstrap |
| A02 | PG populated schema/data replay 100 times |
| A03 | PG concurrent migration lock / bounded wait; image overlap |
| A04 | PG file transaction failure, cancellation and actual backend termination |
| A05 | PG catalog drift cases for wrong definitions/invalid indexes/constraints |
| A06 | Runtime/environment credential units, PG connection failures; image dummy REST/WS authentication |
| A07 | PG all registered models round-trip; original Decimal range/scale retained |
| A08 | PG `test_recorder`: fill-before-create, duplicate/conflicting fills, rollback and rejection of later snapshots/bulk writes; image `spot` kills actual blocked recorder backend and recovers its fill |
| A09 | Separate CLI processes and image CLI containers, no shared runtime layer |
| A10 | PG concurrent CAS/stale acknowledgment; image stale command rejection |
| A11 | Process/image stopped state survives SIGTERM/handover/restart |
| A12 | Image real PID 1 shutdown and preserved desired state; watchdog process test; PG `test_fill_during_v2_shutdown_requires_position_reconciliation` |
| A13 | Image accepted-without-ACK plus stale-owner pause/session loss; persistent HALT |
| A14 | PG throttle/transport barriers and stale write fencing; image pause/resume/session handover |
| A15 | PG statement/pool budgets and persistence latch; image actual DB outage and restart HALT |
| A16 | Image `spot`, `v2-spot`, `v2-perpetual`; runner/CLI and `test_strategy_v2_base` local hot reload/controller regression |
| A17 | Process/image read-only YAML/root; no SQLite, PID, connector files or persistent log/data volumes |
| A18 | Image `upgrade`: distinct image contents, additive migration, valid standby and ownership handover |
| A19 | Image `upgrade`: compatible P6 rollback preserves new column and prior economics; runbook forbids down migrations/legacy images |
| A20 | Optional SQLite importer is not requested or delivered; not a release prerequisite |
| A21 | PG `test_populated_previous_canonical_schema_adds_recovery_columns_without_data_loss`; image `upgrade` tests compatible nullable-column fixture |
| A22 | Dotenv tests cover deterministic path, overrides, explicit missing file and literal secret characters |
| A23 | Real process with `K_SERVICE` and no dotenv; environment-only image startup; Secret Manager itself unverified |
| A24 | PG restricted role plus image fresh startup with DML-only role and auto migration false |
| A25 | PG empty/drift cases; image `schema` refuses both before any exchange request |
| A26 | Runtime settings boolean/default/precedence tests |
| A27 | PG explicit migration executes despite automatic flag false |
| A28 | Environment credential/bootstrap/connect/balance tests; image authentication without keystore/password |
| A29 | Image `upgrade` changes YAML prices to 98/102 and selects original YAML on rollback; revision/environment/config hashes recorded. Numeric Secret Manager versions are validated offline; Secret Manager itself remains unverified |
| A30 | PG canonical naming/catalog/ORM checks and model round-trip |
| A31 | PG explicit legacy cutover preserves rows, keys and sequences, replay and failure rollback |
| A32 | PG old/both-name cases fail without creating parallel canonical tables |
| A33 | Image `spot`, `v2-spot`, `v2-perpetual`: independent ledger, exact SQL economics, position holds and CLI PnL/risk checkpoints |
| A34 | Image fresh replacement reuses only PostgreSQL, simulator truth, immutable YAML and injected fixture credentials |

## Release contract and limits

The local gates are complete for OKX spot/perpetual, V1 PMM and the named existing
V2 controller/script configurations. CI requires the selected regression suite,
real PostgreSQL and image E2E. A complete release run additionally supplies a
distinct compatible predecessor through `CLOUDRUN_PREVIOUS_IMAGE`; CI may source
it from repository variable `HBOT_PREVIOUS_COMPATIBLE_IMAGE`. The concrete deploy
review rejects partial reports, missing scenarios or a different manifest digest.
Remote CI has not been executed in this workspace and no cloud deployment has
been performed. Actual IAM, Secret Manager, exchange access/allowlisting and
Cloud SQL/Cloud Run behavior require operator deployment inputs and remain
unverified; staging is outside the agreed gates.

The earlier image assertions above used the original scale-six integer Decimal
representation and exactly representable values. The subsequent BTC Demo exposed
truncation; the PostgreSQL precision follow-up now stores the 15 former scaled
fields as unscaled NUMERIC, while preserving the upstream SQLite format. Its new
checks cover 8-decimal partial fills, precise fee replay, ORM values beyond 18
decimal places, natural-unit reporting and exact reconciliation. Previously
discarded digits are not reconstructed. See
[decimal operations](../../docs/postgres-runtime.md#decimal-storage) and the
[follow-up checklist](../../docs/plans/LIFE_OKX_IMPLEMENTATION_PLAN_TDD.md).
No general live-executor
or custom timer reconstruction after crashes is claimed: a persisted runtime marker
forces HALT rather than silently resetting that risk state.

## Decimal precision follow-up

Verification: 1,271 regression tests/nine subtests, 113 PostgreSQL tests and three
extended precision checks pass. Image `1710edfec3fb...` passes all five applicable
fresh-deployment offline scenarios, including nine LIFE profile/market cases.
The spot scenario uses 8-decimal partial fills and 18-decimal fees through
duplicate replay, restart and interrupted-transaction recovery. A 60-second
diagnostic probe with 100,000 historical fills completes 20 reads/s, about 100
market updates/s and one fill/s without budget violations: read p95 120.52ms,
p99 622.44ms; 60 independent fills match the recorder and positions. History
retains at most three queries per orders page and one per fills/performance read.
This is not a new full load/soak, old-schema hot rollback or Cloud Run run.
See [portable precision evidence](postgres-decimal-precision.json). Native local
recorder p95 is 30.97ms over 25 transactions; cold daily statistics over 100,000
fills use six queries, p95 1,289.85ms across 11 samples. Measurements overlapped
other local test/image preparation and do not compare remote Cloud SQL latency.
