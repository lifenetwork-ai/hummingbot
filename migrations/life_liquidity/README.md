# LIFE PostgreSQL migrations

This is the single schema migration entry point for the PostgreSQL backend.
It installs **storage lineage, an empty typed financial schema with guarded transaction functions, and isolated legacy recorder reporting tables**. It does not import file
state, create account balances, grant ownership, enable trading, or connect the
LIFE controller to PostgreSQL. The spike SQL under `test/` remains a synthetic
fixture and is deliberately excluded.

## Numbered schema and runtime access

- `000001_storage_lineage.sql`: original lineage metadata; unchanged applied checksum.
- `000002_financial_schema.sql`: typed account/session/attempt/hold/ledger/budget/product/outbox tables,
  finite exact decimals, scoped permanent identities, immutable history and active indexes.
- `000003_guarded_transactions.sql`: account-scoped access/RLS, expected owner/version and
  DB-time guards, deferred COMMIT checks, legal state transitions and one allocation primitive.
- `000004_spot_preparation.sql`: immutable approved standalone SPOT risk/budget policy
  and preparation records, stable snapshot and atomic prepare/arm commands. No policy,
  session, balance or owner is seeded; approved import/access facts remain mandatory.
- `000005_spot_observations.sql`: explicit fill policy, immutable fill/fee provenance,
  accounting progress and pending observations; late-observation COMMIT guards,
  atomic ACK/fill/fee/loss/subsidy accounting and derived funding deficit.
  Unsupported negative-wallet facts persist as pending/HALT, retaining money/holds.
  No source policy, capital, owner, release or activation is automatically created.
- `000006_recorder_projection.sql`: thirteen empty legacy ORM reporting tables in
  `life_recorder`, with a schema version marker. No runtime role is granted DDL or
  metadata-write access. Projection tables omit FKs to support fill-before-order
  callbacks; financial FKs are unchanged. This is reporting compatibility, not
  financial authority or full LIFE recorder/outbox integration.
- `000007_spot_capital.sql`: immutable imported opening-capital policy, approved
  cashflow provenance/pending facts and NAV/highwater observations. Reuses bill
  approvals, cashflows and financial events; validates approved economic identity,
  preserves signed asset valuation and cashflow-adjusted performance, and retains
  holds/spent budgets. No capital/approval/owner is seeded. Invalid causal replay
  or an explicitly configured replay bound preserves pending facts/reasons and
  HALT. Pending resolution, provider/approval authentication and portfolio modes
  remain separate requirements.

- `000008_spot_sessions.sql`: three empty session-policy/progress/event tables.
  Explicit privileged imports establish the per-session policy and initial
  watermark; runtime cannot bootstrap them. Atomic ticks persist pause, expiry
  and irreversible clock-rollback state. Verified ACTIVE state gates prepare/arm
  at entry and COMMIT. Expiry retains holds and never activates a successor.
  Reconciliation/activation and order cancellation/terminal release remain
  separate ports. The migration revokes inherited access to renamed old
  entrypoints; existing principals require approved access provisioning for the
  new entrypoints. No extra `.env` switch or manual SQL execution is required.

- `000009_spot_order_lifecycle.sql`: four empty order policy/cancel progress/
  lifecycle event/terminal fact tables and indexes on reservation intent and
  budget-event windows. Atomic abort/cancel/terminal commands retain original IDs
  and spending. Proven unsent/qualified terminal commands release order holds;
  filled quote subsidy remains for economic settlement. Freshness/generation
  guards repeat at COMMIT. Runtime cannot create its own limits/opening anchor.
  The existing auto-migration switch applies this script; approved provisioning
  grants the new command entrypoint. Provider authentication, external cancel,
  quote actions and product settlement remain separate requirements.

- `000010_spot_quote_actions.sql`: three empty imported action-policy,
  immutable descriptor and operation-linked event tables over the existing typed
  `quote_actions` rows. An account-wide unique index prevents active slot reuse.
  Atomic batch claim, dispatch bookkeeping and pre-acceptance rejection preserve
  exact identity, runner-fact freshness and original writer/lease constraints.
  COMMIT rechecks freshness/ownership; pending dispatch never expires into a free
  slot. No executor/attempt/hold is created. Pending actions retain the existing
  financial barrier; action-to-prepare/runner and qualified terminal reconciliation
  remain separate work. Approved provisioning grants the new fixed entrypoints;
  the automatic migration configuration and scripts 1–9 remain unchanged.

- `000011_spot_action_preparation.sql`: mandatory imported quote-admission
  policy with separately explicit observation/reference/fee/capture ages,
  immutable exact captured-payload financial binding and atomic prepare holds.
  Qualified publication state is separated from financial corruption so late
  accounting remains available. Generic preparation cannot adopt a claimed
  ID/slot; only the fixed bound entrypoint can invoke its private kernel context.
  Source authenticity, actual runner/transport and action terminal reconciliation
  remain separate gates. No owner, money or live threshold is bootstrapped.

- `000012_spot_action_reconciliation.sql`: private closing-time/hold proofs and
  qualified PREPARED → RECONCILED slot closure after financial abort/terminal and
  fresh post-closure runner absence. It changes no wallet/hold/spending state,
  adds no runtime privilege/table/default, and retains original writer and
  permanent identities. Unprepared dispatch retirement and actual runner/source
  authenticity remain separate work. Existing automatic migration applies it.

- `000013_spot_action_abandonment.sql`: distinct fixed-reason retirement before
  financial acceptance, with original-writer/fresh post-dispatch runner-scope
  checks and no attempt/hold. Account locking serializes retirement against
  preparation; the rejected ID stays a permanent tombstone. It changes no money,
  adds no table/default/runtime grant and never treats timeout or missing receipt
  as proof. Actual runner retirement/source authentication remain integration work.

- `000014_spot_quote_send_boundary.sql`: exact captured cash/post-only request
  JSON and fresh compatible admission commit with one PREPARED → SEND_UNKNOWN
  operation. Shared arithmetic preserves exact initial subsidy reservation and
  checks conservative existing coverage at arm/COMMIT, without refund or new
  claim. Existing immutable operations retain wire/request checksums; a partial
  index and current-cut proofs verify retained metadata. Runtime gets only the
  fixed command through the existing access provisioner; private helpers/old
  snapshot entrypoint stay private and reporting cannot write. No table, policy
  default, account, capital or owner is added. Readback never rearms, and actual
  sender/source/REST authorization remains unqualified. Automatic migration uses
  the same version/checksum/lock contract; scripts `000001`–`000013` are unchanged.

- `000015_spot_prepare_receipt_proof.sql`: canonical generic/action-bound
  preparation receipts must match their original identity, command, version and
  state. Current-state and deferred COMMIT checks reject inconsistent history;
  historical Python lookup separately checks the target receipt. This is a
  diagnostic proof under database-owner trust, not an independent anchor. No
  table, financial value or owner is created. Automatic upgrade preserves only
  existing current-snapshot EXECUTE rights and revokes the old entrypoint;
  proof helpers remain private. A real 14→15/repeated-startup test verifies that
  previously provisioned runtime/reporting readers need no manual regrant.
  Earlier scripts remain immutable; the central runner skips applied versions.

- `000016_recorder_executor_outbox.sql`: fixed initial/progress reporting commands
  reuse `recorder_outbox`, `projection_receipts`, `operations` and legacy
  `Executors`. Original config/account/wire provenance, immutable sequence,
  ordered delivery, current-owner/cut and deferred COMMIT checks apply. Reporting
  delivery never authorizes exchange submission or changes money. Valid pending
  reports block new risk while leaving late ACK/fill observations available;
  inconsistent reporting blocks financial commits. No new table, role or account
  is created. The current snapshot ACL survives upgrade; private/old functions
  stay unavailable. New approved runtime/read-only command rights are provisioned
  through `storage/postgres/access.py`, never inferred from an upgrade. Actual
  15→16 and repeated-startup tests qualify central-runner idempotency. Other
  reporting streams and configured PostgreSQL service integration remain open.

- `000017_recorder_configuration.sql`: sequenced configuration audit over the
  existing outbox and legacy Controllers table. Immutable registered controller
  metadata must match the report; reporting cannot register its own baseline or
  activate trading. Initial/revision/outcome/time checks, exact replay, scoped
  completeness and deferred owner guards apply. Rejections/startup/manual kills
  latch HALT and cannot clear it through reporting. Executor/configuration streams
  retain global recovery barriers and late accounting availability. A real 16→17
  upgrade/repeat-startup test preserves existing reader ACLs without account or
  command-right bootstrap; approved provisioning grants new fixed command rights.

The runner applies each immutable version once. A second service startup validates
the same history and skips applied scripts. DDL is deliberately forward-only:
unregistered pre-existing objects fail instead of being silently accepted by
`IF NOT EXISTS`. Add a new numbered migration for a schema change; never edit an
applied file. This preserves repeatable automatic startup with drift detection.

Schema creation inserts no account, money, owner or activation grant. The schema
owner can call `storage.postgres.access.provision_access()` for an **existing**
ordinary LOGIN role and an **existing approved account**. It grants account-scoped
runtime/reporting reads and, for runtime, only whitelisted transaction functions.
It creates no roles and grants no direct financial DML/DDL. Runtime and reporting
credentials must be distinct from migration-owner credentials.

The PG2 runtime remains separate from the bot's backend selector. It provides
bounded primary/TLS-validated pools, operation readback and a test-qualified hold
primitive, not production trading repositories. See the
[storage guide](../../hummingbot/strategy_v2/life_liquidity/storage/README.md#postgresql-pg2-foundation).
Root `.env.example` documents separate opt-in `LIFE_PG_*` runtime settings; merely
enabling migrations never selects PostgreSQL financial authority or enables trading.

## Commands

Install the optional pinned driver into the environment that runs migrations:

```sh
conda run -n hummingbot python -m pip install -r setup/life_postgres_requirements.txt
```

A tracked [`.env.example`](../../.env.example) lists all migration settings.
Copy it to `.env` if you do not already have one, then fill `LIFE_MIGRATION_DSN`
with the **migration role** URL. The local `.env` is gitignored and excluded from
Docker build context; never commit its credentials. OS environment/secret
injection overrides file values. Then, from the repository root:

```sh
make life-migration-status
make life-migrate
make life-migration-status
```

The underlying CLI is suitable for a separate job without Conda:

```sh
python -m hummingbot.strategy_v2.life_liquidity.storage.postgres.migrations status
python -m hummingbot.strategy_v2.life_liquidity.storage.postgres.migrations up
```

CLI exit codes: `0` = successful `up` / fully applied `status`; `2` = pending versions
in `status`; `1` = failure. Make reports a nonzero CLI result as a failed target. JSON output contains filenames and safe error type /
SQLSTATE, never a DSN or driver error text. `status` performs no DDL or ledger
writes. It obtains the same advisory lock for a coherent view.

TLS with certificate and hostname verification is the default. Supply
`--ssl-ca-file /path/to/ca.pem` for a private CA. Only disposable local databases
may use `--local-insecure`, and only with a literal loopback IP in the URL.
URL query parameters are rejected so they cannot override the host/TLS policy.
For a local test DB: `make life-migrate LIFE_MIGRATION_ARGS=--local-insecure`.
Cloud SQL connection/authentication and Linux image packaging still require
PG0.4/PG7/PG9 qualification; this CLI does not implement a Cloud SQL connector.

Docker source packaging copies `migrations/`. Build with
`--build-arg LIFE_POSTGRES=true` to include pinned asyncpg and psycopg drivers;
the default `false` omits both extras. The digest-pinned Linux ARM64 image and
standalone SQLAlchemy recorder seam are now qualified locally; full LIFE
recorder integration, selected release architecture and deployed identity/TLS
remain open. See the [PG2 local checkpoint](../../docs/plans/LIFE_POSTGRES_CLOUD_RUN_IMPLEMENTATION_PLAN_TDD.md#pg2-local-recorder-and-linux-qualification-2026-10-16).

For real database tests without using a configured DSN or `.env`:

```sh
make life-postgres-test LIFE_PG_TEST_ARGS="--output /tmp/life-pg2-run"
```

This starts the pinned disposable local PostgreSQL container, uses synthetic roles
and accounts, runs schema/concurrency/pool/TLS/fault/crash tests and removes it.
The output directory must be fresh and under `/tmp`. It does not qualify managed
failover, disk/host loss, backups or PITR.

For the recorder subset, add `--suite recorder`. To qualify an actual local Linux
artifact, build it and obtain its immutable ID:

```sh
docker build --platform linux/arm64 --build-arg LIFE_POSTGRES=true -t life-pg2-linux:local .
docker image inspect life-pg2-linux:local --format '{{.Id}}'
# Substitute the actual immutable sha256 ID printed above; never a mutable tag.
make life-postgres-test LIFE_PG_TEST_ARGS="--suite recorder --linux-image sha256:<image-id> --output /tmp/life-pg2-image-run"
```

The image runner mounts test code and temporary results only, verifies source
hashes/compiled Linux modules and runs against a new disposable primary. It does
not mount the host source, `.env`, credentials or configured data. No `make build`
or Cloud Run provisioning is involved.

The PostgreSQL SQLAlchemy recorder uses `postgresql+psycopg` and an explicit
`life_recorder` namespace. Its separate approved role needs schema USAGE, table
SELECT, projection INSERT/UPDATE and sequence USAGE/SELECT; it must not have
schema CREATE or INSERT/UPDATE/DELETE on `Metadata`. The financial-role
`provision_access()` helper does not grant these reporting privileges. Recorder
startup only validates schema/types/constraints/indexes/version: missing or
incompatible state fails, without `create_all`, FK removal, version stamping,
SQLite migration or fallback. TLS defaults to `verify-full`; the disposable
loopback fixtures explicitly disable TLS. Deployed identity/TLS and operational
recorder configuration require PG5/PG9 qualification.

Migration `000006` preserves legacy reporting representations, including scaled
six-decimal integer and float fields. They are not the checked exact-decimal
financial ledger. Scoped provenance, precision, bounded asynchronous projection
and full outbox/recovery remain PG5 work. The existing `LIFE_AUTO_MIGRATE=true`
startup flow applies this migration automatically; no additional migration env
flag or manual script execution is needed.

The same startup runner also includes `000007` automatically. Its required
opening policy, replay bound and preverified bill approvals must come from an
approved privileged import; runtime cannot create them or grant itself access.
The signature bytes in the approved bill table are provenance retained from that
import, not a signature-verification implementation. Existing runtime/reporting
roles require the approved provisioning stage for the new capital command/read
functions. This does not activate the PostgreSQL financial backend or trading.

## Environment and automatic startup

The default env file is `<repository>/.env`, independent of the working directory.
Use `LIFE_ENV_FILE=/path/config.env` or CLI `--env-file /path/config.env` to select
another file. Set `LIFE_ENV_FILE=` or use CLI `--no-env-file` to disable file
loading for managed secret injection. Missing explicitly selected files fail.
The loader consumes only listed LIFE migration keys and leaves the process
environment untouched. It supports single-line `KEY=value`, optional `export`,
quotes and comments, with **no shell execution or variable interpolation**.
Quote values containing spaces or `#`; URL-encode credentials in a DSN.

| Setting | Default | Use |
| --- | --- | --- |
| `LIFE_AUTO_MIGRATE` | `false` | Await pending migrations before bot application creation |
| `LIFE_MIGRATION_DSN` | empty | Required for manual commands and enabled auto migration |
| `LIFE_MIGRATION_DIRECTORY` | bundled SQL directory | Relative paths resolve against env file directory |
| `LIFE_MIGRATION_LOCAL_INSECURE` | `false` | Explicit loopback-only test TLS bypass |
| `LIFE_MIGRATION_SSL_CA_FILE` | empty | Optional CA certificate file |
| `LIFE_MIGRATION_LOCK_TIMEOUT_SECONDS` | `10` | Bounded advisory lock wait |
| `LIFE_MIGRATION_STATEMENT_TIMEOUT_MS` | `30000` | SQL timeout |
| `LIFE_MIGRATION_DDL_LOCK_TIMEOUT_MS` | `5000` | DDL lock wait, at most SQL timeout |

Boolean values must be `true` or `false`. Invalid settings block startup before
SQL/application creation. CLI flags override validated file/environment values;
`--require-tls` can override an insecure local setting. Manual `up` still runs
when `LIFE_AUTO_MIGRATE=false`.

For automatic local startup, configure a valid migration DSN and set:

```dotenv
LIFE_AUTO_MIGRATE=true
LIFE_MIGRATION_DSN=postgresql://migration_user:URL_ENCODED_PASSWORD@DB_HOST:5432/DB_NAME
```

With those settings, start the service normally; no manual `make life-migrate`
step is required. The sequence is `.env`/environment configuration → acquire
migration lock → verify applied versions → apply pending scripts transactionally
→ release the connection → construct the bot application. Every restart uses
the same sequence, including when no scripts are pending. A failed migration
blocks application startup; `LIFE_AUTO_MIGRATE=false` skips the migration stage.

`make run` / quickstart, the detached `hbot` engine and the legacy interactive
launcher await the same runner before constructing the application/connectors.
Repeated startups verify checksums and skip applied scripts. Failure or
cancellation blocks that startup. An inherited `LIFE_MIGRATION_DSN` is removed
after the enabled migration stage before application children can inherit it;
file secrets are never promoted into `os.environ`. This does not select the
application/recorder PostgreSQL backend or provide permission to trade.

The migration driver must be installed in the startup environment. The current
Docker/Cloud image still needs the planned dependency/SQL-bundle packaging and
qualification. The requested deployment flow uses config-enabled startup
migration. Package the SQL bundle and driver, and provide the migration role
only to the startup migration stage; subsequent application database access
uses the runtime role. A separate migration Job remains an optional deployment
mode with `LIFE_AUTO_MIGRATE=false`. Workers must be drained before incompatible
schema changes in either mode. Removing the inherited DSN is not secret
isolation: a colocated process may still read its mounted `.env` or secret;
deployment isolation remains a PG7 qualification requirement.

## Script and retry contract

- Add `NNNNNN_lowercase_description.sql` with a positive, unique, increasing
  version. Files are read and SHA-256 hashed before work starts. The applied
  `(version, filename, exact-byte checksum)` prefix must match the entire bundle.
  Missing, edited, renamed, inserted earlier or unknown applied versions fail.
- One dedicated primary connection holds a database-wide session advisory lock.
  Cooperative runners serialize even if they use different script directories.
  There is no pooled migration session or down/reset command. Optional startup
  migration uses the same lock/ledger and is disabled by default. The lock is **not trading ownership or exchange fencing**.
- Each script and its ledger row commit in one transaction. Re-running `up`
  verifies history and skips applied versions. Ordinary `CREATE TABLE` / `INSERT`
  statements need not hide errors with `IF NOT EXISTS` or conflict suppression.
- Script failure rolls back that script's DDL, data and ledger entry; preceding
  versions stay committed. Fix only an **unapplied** script. For an applied
  change, add a new forward version. Restoring an old bundle does not downgrade.
- Top-level transaction/session commands are rejected; the runner owns them.
  Scripts are trusted, reviewed administrative SQL, not an untrusted SQL sandbox.
  Use schema-qualified names and transactional SQL. Operations such as
  `CREATE INDEX CONCURRENTLY`, external side effects and long historical
  backfills require a separate reviewed operational workflow; they do not belong
  in this transactional runner. Keep migrations at or below 1 MiB.
- On connection loss or an ambiguous COMMIT, do not automatically retry a SQL
  command or delete ledger rows. Open a fresh connection, run `status`, then `up`
  with the same immutable bundle. PostgreSQL's script/ledger transaction lets
  that new run skip a committed version or apply a rolled-back version.
- Defaults: connect 10 s, advisory lock wait 10 s, SQL 30,000 ms, DDL lock wait
  5,000 ms. Override bounded values with `LIFE_MIGRATION_ARGS` or CLI flags.
  These are migration operation defaults, not approved live trading thresholds.
- `life_migrations.schema_migrations` is private to its owner. `000001` creates
  a private `life_storage.storage_metadata` row with a stable lineage UUID.
  Existing unregistered schemas cause an error, not adoption/overwrite. Migration
  history checks validate the bundle; they do not attest all table contents or
  defend against a privileged database administrator modifying history.

## Roles and deployment order

Provision the database and a dedicated non-superuser migration role through
reviewed infrastructure. It needs `CONNECT`, `CREATE` on the target database and
ownership of its schemas. Runtime must have no database/schema `CREATE`, no
membership in the migration role, and no writes to migration history. Reporting
may receive only `USAGE` on `life_migrations` and `SELECT` on its ledger. Full
financial table grants are still a PG2 requirement; the runner never silently
creates or promotes production roles.

For Cloud Run, the planned order is:

1. Provision DB, roles, network/TLS and a migration-only secret/service identity.
2. Package the pinned release bundle and enable `LIFE_AUTO_MIGRATE=true` through
   managed configuration. Startup awaits the central runner before application
   construction. Alternatively, run a separate migration Job with one task,
   parallelism one and automatic retries disabled, then keep worker auto migration off.
3. Check matching schema/bundle compatibility before starting an **unarmed**
   worker with the runtime role. Stop deployment if migrations fail.
4. Follow the independent ownership, demo and trading release gates before rearm.

The database lock serializes concurrent startup runners and duplicate jobs. A task-count
setting alone is insufficient. Future incompatible migrations require an explicit
worker drain and schema compatibility policy; this runner cannot guarantee a safe
rolling upgrade. Cloud resources/Job definitions have not been deployed. The planned task/retry
settings are documented in [Cloud Run Jobs](https://docs.cloud.google.com/run/docs/create-jobs)
and [job retries](https://docs.cloud.google.com/run/docs/configuring/max-retries).

## Tests

Validation unit tests need no PostgreSQL driver:

```sh
conda run -n hummingbot python -m pytest -q \
  test/hummingbot/strategy_v2/life_liquidity/storage/test_migration_files.py
```

With Docker running and the optional driver installed, integration tests create
and remove their own pinned PostgreSQL 16 container and isolated databases:

```sh
conda run -n hummingbot python -m \
  test.hummingbot.strategy_v2.life_liquidity.storage.run_migrations \
  --output /tmp/life-migrations-unique-run
```

They use synthetic test roles without superuser privileges for migration/runtime/
reporting, transactional failures, concurrent runners, timeout/cancellation,
committed-but-lost reply, metadata-only lineage and real CLI exit/redaction cases.
The test administrator provisions fixtures; these roles are not production IAM.
Logs/XML stay in the requested `/tmp` directory; no evidence file is generated.

## Reference

Organization follows `life-wallet-backend`'s `make migrate`,
`cmd/migration/main.go` and `internal/adapters/postgres/{migrate.go,scripts/}`.
That runner sorts and re-executes every SQL file, relying on each script's
idempotency. LIFE adds an applied-version/checksum ledger, concurrent-run lock
and one transaction per script. Manual migration stays available independently of startup. The reference
backend's optional auto-migration pattern is now supported through
`LIFE_AUTO_MIGRATE`, with default-off, awaited startup and failure blocking.

- `000018_recorder_events.sql`: registered-source event commands, immutable
  relational reporting effects/heads and atomic order/status/trade/funding/state
  projections. Original economic identities, predecessor/native completeness,
  scoped read access and physical-COMMIT observation guards remain mandatory.
  No financial balance, activation, provider capture or SDK dedup authority is
  inferred from these reporting records. Existing scripts remain unchanged.

- `000019_recorder_positions.sql`: registered-controller exact held-position
  snapshots over existing outbox/effects/heads/Position tables. Stage/apply are
  atomic, ordered and replay-safe. Current reporting rows never authorize an
  exchange send, prove a financial position or release capital. Existing scripts
  remain unchanged; the version/checksum runner qualifies 18→19 and repeat startup.

- `000020_spot_operator_approvals.sql`: empty immutable operator audit linked to
  bill approvals, metadata consistency and physical-COMMIT capture/expiry guards.
  The separate privileged importer verifies Ed25519 signatures using externally
  supplied keys pinned to the registered policy. Runtime has no approval DML or
  private-helper rights; migration never seeds an operator, bill, money or grant.
  Existing scripts remain byte-for-byte unchanged. Actual 19→20 and repeat startup
  qualify the same central version/checksum runner.

- `000021_spot_ack_ingress.sql`: immutable money-neutral received ACK capture and
  delivery receipts. Capture survives expired ownership, HALT and unrelated
  financial inconsistency; it grants no send/accounting permission. Pending
  captures deny new risk, including generic allocation. Only the exact original
  financial ACK and captured request hash qualify delivery. Primary snapshot
  digests include the append count even when the account version does not change.
  Existing scripts remain unchanged; the central runner qualifies 20→21 and
  repeated startup without financial bootstrap.

- `000022_spot_observation_context.sql`: one scoped, bounded, stable-statement
  read of original SPOT commands, fills/fees, applied cashflows, imported policies
  and pending received ACKs with its primary cut. Monetary JSON fields stay
  decimal strings. Known unresolved financial observations deny rather than
  disappearing from scope. Preparation proof now also checks reverse origins
  from retained attempts/receipts, including aborted and terminal identities.
  Migration changes no financial row, owner or grant; existing script bytes and
  current snapshot/private-helper ACLs remain unchanged.

- `000023_spot_financial_ingress.sql`: untrusted original FILL/CASHFLOW intake,
  immutable financial-delivery links and monotone diagnostic progress. Capture
  changes no money or account version. Receipt qualification requires actual
  APPLIED financial facts with original identity/units/valuation; a covering
  cumulative receipt may acknowledge an older received snapshot without replaying
  its debit. Pending intake blocks new risk. Progress detects missing ACK/fill
  intake rows, even before a financial operation exists. Upgrade establishes only
  diagnostic counters from the verified prior ACK baseline; no financial amount,
  owner or activation is imported. Prior script bytes and existing function ACLs
  remain unchanged. Raw provider authentication/collection is separate work.

### 000024: standalone SPOT inventory settlement

Adds explicit imported settlement policy plus immutable, scoped cycle/member
facts. Closed terminal cycles must net to zero LIFE including signed LIFE fees.
SQL derives actual subsidy spend from permanent original fills and fees; callers
supply no refund amount. Original holds move to actual incurred spend in their
original session/day/campaign buckets. Wallets and gross execution-loss counters
never receive a credit. Permanent member/cycle identities prevent reuse.

Forward lifecycle/physical-COMMIT proofs retain earlier guards and recognize
only verified settlement overlays. Existing filled terminal receipts are not
rewritten. New tables/functions create no account, policy, owner, session or
financial amount; the central ledger still serializes and skips applied scripts.
Runtime provisioning grants the fixed settlement command only. Private evidence,
canonical proof and DML remain unavailable to runtime/read-only roles.

### 000025: standalone SPOT horizon observations

Adds explicit imported markout policy and immutable scoped horizon facts, linked
to original native fills and attribution. Policy and wire shapes are checked in
SQL; canonical command fingerprints and exact original prices cannot change on
replay. Scoped bounded cohort reads return no trading permission. Current proof
checks reverse operation/attribution origins and physical COMMIT rechecks original
owner generation/fact age. Existing snapshot OID/ACL and prior guards are retained.
An account/fill-time index supports cohort window selection. The central migration
ledger supplies repeat-startup idempotency; no financial/policy/owner/bootstrap or
risk activation is added, and previous script checksums remain unchanged.

### 000026: precise capture time denials

Forward-replaces the existing private quote admission guard while preserving
its OID/ACL, amounts and all age/clock comparisons. Payload shape/identity still
returns `QUOTE_CAPTURE_MISMATCH`; future and aged capture timestamps return
`QUOTE_CAPTURE_FUTURE` and `QUOTE_CAPTURE_STALE`. Each denial still rolls back
preparation and grants no sender. No table, financial value, default, owner or
activation is added. Previously applied scripts remain unchanged; repeated
startup uses the central checksum/version ledger.

### 000027: decoded provider response diagnostics

Adds empty account-scoped `spot_provider_heads`/`spot_provider_pages` with
immutable original bodies, primary timestamps and retained sequence/hash tips.
The fixed private GET route/query command accepts bounded decoded JSON only;
headers, credentials and arbitrary URLs are not command fields. Collection
limits are 512 pages/16 MiB, with explicit bounded verified readback. Existing
snapshot functions and financial state are unchanged. A diagnostic record is
untrusted and grants no financial application, completeness or send authority.
Privileged `provision_access` grants the new fixed capture/read functions;
runtime gets no table DML or private helper permission. Central startup migration
version/checksum handling makes repeated runs a no-op, without reapplying SQL.


### 000028: native shared SPOT/SWAP capital

- `000028_shared_capital.sql`: empty immutable shared model/observations, typed
  permanent SPOT/SWAP claims and monotone heads. One account transaction computes
  independent fill endpoints (including SELL base fees), cash/fees and
  tiered/stressed collateral. Imported
  capacity is included in the model hash. Native SPOT preparation/arm additionally
  requires its exact original claim/config/risk epoch and fee coverage. Bounded
  native current/original-operation proofs qualify readback and physical COMMIT
  rechecks primary generation/time. Exact rational margin rounds upward only at
  the final 18-decimal quote requirement. No claim release, policy/account import,
  SWAP sender/accounting, financial bootstrap or configured backend is installed.
  Existing snapshot/private-core OIDs and ACLs survive upgrade; the existing
  automatic migration switch applies this forward script once, then checksums
  and skips it on repeat startup. Approved provisioning grants the new fixed
  entrypoints; runtime receives no direct financial-table writes.

- `000029_carry_streams.sql`: imported carry policy, independent monotone stream checks, permanent funding-payment evidence, native forecast/shared-capital decisions, bounded diagnostic reads and carry readiness at SPOT preparation/arm and physical COMMIT. No wallet credit, automatic source qualification or SWAP sender is added. Preserves the snapshot OID/ACL and all prior financial guards.

### 000030: native SWAP intents

`000030_swap_intents.sql` imports empty contract/capture policy storage and adds
immutable SWAP preparations/arm/ACK events. The current shared claim, carry,
account/session/config/risk, exact LIFE/contract units, captured config age and
account budgets gate preparation/arm and physical COMMIT. ACK is observation-only
and retains all exposure after HALT/expiry. The common snapshot recognizes only
verified native SWAP intents while retaining every SPOT accounting guard and its
existing OID/ACL. Runtime roles cannot mutate the tables or invoke private proofs.
No source, SWAP fill/funding cash accounting, final transport or release is added.

### 000031: private causal SWAP arithmetic

`000031_swap_position_replay.sql` supplies the private derivative replay used by
the pending native joint accounting port. It validates complete trade/payment
units and coverage, permanent IDs, collector-qualified order within a shared
millisecond, long/short weighted entry, exact reported close PnL, ONEWAY rules,
signed fees and paid funding as distinct cash events. Integer quotient/remainder
implements the existing File decimal28 half-even rule without SQL division-scale
or logarithm guesses. Work bounds are explicit. PUBLIC and runtime have no
EXECUTE grant; no table, account, wallet writer, claim release or sender is added.
Full native joint financial settlement remains required. Decimal28 parity does
not establish the venue's live settlement rounding contract.

### 000032: native cumulative SWAP fill accounting

`000032_swap_fill_accounting.sql` adds explicitly imported opening position,
reference and work policy, permanent original fill/pending facts and accounting
progress. The guarded account transaction updates only signed realized PnL plus
actual USDT fees, retains causal weighted basis and charges original session/day/
campaign gross loss buckets. It never spends derivative principal as SPOT cash.
Invalid/conflicting/incomplete/unpayable facts retain durable pending/HALT after
rolling back every provisional monetary effect. Original readback never reapplies
fees. Common SPOT, operator approval and reporting/ingress proofs stay in place.
Current derivative facts gate later risk snapshots even for zero-fee opens.
Old SPOT-only NAV cannot be current portfolio NAV; new publication is denied for
an enabled SWAP-accounting account. Explicit fill/observation/read bounds apply.
Runtime receives only the scoped fill command/read endpoints, never direct DML,
policy import, private arithmetic/proof, terminal authority or claim release.
Paid funding cash, full joint portfolio accounting/NAV, source qualification and
configured application assembly remain required. Repeat startup uses the same
central version/checksum migration runner and adds no accounts or money.

### 000033: settled funding cash

`000033_paid_funding.sql` adds explicit payment/observation/retained-byte budgets,
immutable complete qualified source bundles and permanent numeric paid-bill IDs.
The account transaction applies only new settled USDT at/after the opening wallet
cut; earlier paid bills remain cost evidence already included in opening cash.
Forecast funding and expected income never credit wallets. Gross debit cost
never nets against later receipts and exhaustion latches HALT. Pending invalid,
changed, missing or unpayable facts preserve original bundles and unchanged money.
Funding is neither an external cashflow nor execution loss. Original replay,
causal wallet/leg proofs, current carry bill coverage and COMMIT ownership apply.
Common proof OIDs/ACLs are preserved. Runtime receives only scoped command/read
endpoints, never direct DML, policy import or private proofs. Repeat migration
startup creates no accounts or money. Native joint batching/portfolio NAV,
terminal/release, pending repair and authenticated source assembly remain required.

`000034_portfolio_arithmetic.sql` is a private deterministic decimal28 NAV
kernel. `000035_portfolio_observations.sql` retains immutable policy and
measurements over original versioned native money/leg/source cuts. High-water
and drawdown replay use approved external-flow valuations, not caller NAV.
Collateral never becomes cash; measurement never releases claims. Original
source freshness is verified historically and again at physical COMMIT.
Common proof/snapshot OIDs and ACLs are preserved on upgrade. Repeated startup
creates no account, opening policy, source, money or live threshold. Coupled
financial batches, actual collateral reconciliation and joint terminal/recovery
remain separate required PG3 implementation.

`000036_swap_terminal_observations.sql` retains bounded immutable native SWAP
terminal/pending observations, against original ACK/order, complete fill IDs and
version-bound NAV/account source evidence. Freshness and ownership are checked
at physical COMMIT; original readback never refunds or reapplies money. Invalid
terminal evidence and new post-terminal financial observations preserve HALT
and original pending facts. Shared claims remain held. Source labels are not
authenticated provider evidence; coupled joint settlement/release and configured
source installation remain separate required PG3 work. Original proof OIDs/ACLs
and repeated-startup idempotency are preserved without account/money bootstrap.

`000037_shared_claim_release.sql` adds explicit bounded retirement policy and
permanent original-claim tombstones. Complete matching native terminal and
versioned portfolio/account evidence, no pending observations and physical
COMMIT freshness/ownership are mandatory. Active allocation excludes retired
claims; original decision replay still uses its own historical claim set.
Permanent identity counts and all balances, exposure and gross spending remain.
Retired IDs cannot allocate/prepare again; a new current carry check is required.
Repeated startup preserves OIDs/ACLs and creates no accounts or money. This is
standalone retirement, not coupled financial batch or configured service proof.

`000038_joint_financial_batches.sql` adds explicit bounded joint observation
policy and immutable original batch/member receipts. Private transaction-local
stage kernels preserve original fact validation, IDs, reserve progress and gross
costs; one complete original-time wallet/position replay publishes money only
when the unchanged final funding/account source agrees. Failure retains the
whole original pending batch and HALT without partial monetary effects. Stage
capabilities are private and cannot commit unfinished. Retained parent byte
bounds include original member requests/receipts and both cuts. Original
ownership/source freshness apply at physical COMMIT; cold readback grants no
send/release/activation. Public standalone guards/OIDs/ACLs remain intact;
repeat startup bootstraps no money/account. Signed pending repair, collateral
qualification, legacy interrupted import and full service assembly remain PG3.

`000039_late_capital_observations.sql` preserves existing command/owner/proof
OIDs and ACLs while allowing only original current-owner OBSERVE to record fresh
complete account facts after HALT, draining/recovery mode or actual expiry.
RESERVE and all prepare/arm/send checks stay active-risk guards. Source/money/leg
proofs, monotone observations, bounds and physical COMMIT freshness remain.
No table, money, owner, deadline, budget, claim or HALT is bootstrapped/reset;
repeated startup is a no-op. Observation never grants trading or clears pending.


### 000040 joint source repair

Adds explicitly imported immutable repair policies, signed approval history,
resolution links and a private transaction capability. A separate privileged
importer verifies public-key Ed25519 RECOVER approval over the exact command,
current native cut and complete account/product/bill facts. SQL consistency
checks retain the existing trusted schema-owner boundary. There is no automatic
operator, policy, balance or activation bootstrap.

Only account/funding sources of a raw pending joint batch may be replaced;
original monetary members and settled bill terms stay unchanged. Native accounting
still decides whether publication is possible. Accepted repairs retain original
RECOVERY_REQUIRED receipts and HALT; other pending parents need separate approval.
Retired unused member IDs cannot be reused. Private staging must be gone at COMMIT
and receives no runtime/reporter access. Existing public OIDs/ACLs and source,
owner, gross-budget and final-send guards are preserved. Apply/reapply via the
central checksum/lock migration runner, including enabled startup auto migration.
Direct native pending resolution, legacy import and configured assembly remain
open PG3 work.

## 000041: native hedge coordination

Hedge policy and explicit count/byte limits are privileged imports. Immutable
versioned targets, plans and settlements bind actual native wallet/leg positions,
original depth/fee/funding evidence and native SWAP intent identities. The existing
`hedge_plans` table is a verified projection, not a second financial wallet.
A private transaction context composes shared allocation, carry qualification and
SWAP preparation atomically; it cannot survive COMMIT or be written by runtime.
Refused attempts retain their cost hold, retry count and retired identities.

Installed hedge policy also gates generic SPOT/SWAP preparation and arming.
Physical COMMIT rechecks ownership, account and independent carry/funding deadlines,
as well as the hedge market deadline. Settlements consume actual native fill and
terminal facts; partial or pending hedges are never completed inventory. Cost
breaches retain actual accounting and HALT. No claim refund, automatic activation,
provider authentication or SWAP HTTP transport is introduced. Central migration
tracking applies this file once; repeated enabled startup verifies its checksum.


## 000042: exact SWAP request boundary and received ACK

An imported account policy supplies explicit write-window and retained wire
count/byte limits. The exact post-authentication HTTP JSON, its UTF-8 checksum,
original carry/hedge context and irreversible native arm publish atomically.
Physical COMMIT rechecks the original earliest owner/session/source/write
expiry. Current snapshot digests include the policy and retained requests;
original readback never supplies another send capability.

Received ACK diagnostics and their financial delivery receipts occupy separate
immutable tables. Capture changes no money or ownership and survives HALT or
expiry. Delivery must match the original wire and native ACK operation. Pending
received responses block new risk while observation/recovery remains available.
Runtime/reporter roles receive scoped entrypoints; private proof functions and
all direct table writes remain denied. The central migration runner tracks this
file once and verifies the checksum at repeated enabled startup. It creates no
account, policy, balance, activation or configured PostgreSQL sender.

- `000043_joint_collateral_reconciliation.sql`: private arithmetic and existing
  entrypoint gates for the dedicated, non-borrowing account-mode-2 USDT cross
  profile. Collateral cannot exceed the same native settled USDT cash plus
  replayed mark-price UPL; LIFE NAV and prospective funding do not increase this
  ceiling. Conservative collateral haircuts remain valid. Joint batch failure
  retains the whole original pending command/HALT and rolls back all provisional
  money. Historical applied-batch proofs and portfolio/terminal/shared-release
  proofs use the same bound. Installed portfolio policies also require the bound
  and zero native liabilities at current shared/carry admission. Existing
  entrypoint OIDs/ACLs and all prior COMMIT fences are preserved; the new helper
  remains private. No table, default capital, approval or account is created.
  Previously inconsistent source history becomes unqualified; startup does not
  rewrite it or release its claims. Provider currency-equity/liability capture,
  other collateral models and configured service eligibility remain PG3 work.

- `000044_direct_funding_repair.sql`: extends the existing signed RECOVER path
  to one retained standalone funding observation. Resolution links reference the
  original native operation; no pending parent, account, policy or money is
  seeded. Complete fresh sources must preserve original bill identities, amounts
  and times and pass the existing causal/collateral/liability replay. Original
  pending rows/receipts remain immutable. Only committed resolution links change
  unresolved predicates; historical terminal/release proofs use their original
  version. The private staging capability binds the exact repair and pending
  IDs, preventing a second approval with identical replacement JSON from widening
  the transaction. Other direct pending facts still block publication. Existing
  entrypoint OIDs/ACLs, expiry/owner COMMIT fences and HALT remain; new helpers
  stay private. Automatic enabled startup uses the same migration lock/checksum
  runner; repeated startup skips this applied script. Earlier scripts are unchanged.

- `000045_funding_repair_cohorts.sql`: extends RECOVER with explicitly selected
  direct-funding pending IDs. Each selected original source is hashed and every
  original bill identity/amount/time must match the final complete source.
  One immutable membership table retains secondary resolution links; replay
  consumes each origin once across primary/secondary positions. Other pending
  observations still block; source readiness, terminal/release and activation
  remain separate. Original single-parent signed JSON/receipts and public
  OIDs/ACLs are preserved. Imported member/byte limits bound the whole group,
  including membership bytes; no financial default or trading grant is seeded.
  Runtime/reporting roles cannot access membership or new private helpers.
  Existing automatic migration locking/checksums and repeat-startup behavior apply.

- `000046_swap_fill_repair.sql`: extends the same signed RECOVER command to
  retained direct SWAP fill observations, alone or with selected direct funding
  observations. Replacement application IDs are new; original trade, fee, PnL,
  time, cumulative quantity, wire and source scope must match exactly. Original
  pending facts/receipts remain immutable. Only exact selected current-transaction
  monetary kernels can proceed; unlisted pending still blocks. Committed links
  govern current flags and historical terminal/release proofs. A pending joint
  batch at the terminal/release cut also blocks that transition; a later batch
  cannot invalidate an earlier terminal. Existing native causal cash/position,
  gross-loss/funding, collateral and zero-liability gates remain. Private helpers
  and entrypoint OIDs/ACLs, auto-migration lock/checksum and repeated startup are
  preserved. There is no new table, account, capital, policy or activation.

- `000047_spot_fill_repair.sql`: extends exact signed recovery to retained native
  SPOT fill snapshots, including selected SPOT/SWAP/funding groups. All original
  fill fields except the new application operation ID remain unchanged. Native
  principal, LIFE/USDT fee, remaining reservation, gross loss and subsidy-floor
  accounting still execute through the existing joint monetary kernel. A private
  pending proof binds raw commands, source hashes, original receipts and their
  historical loss totals, including orphan detection; raw records are retained.
  Only committed resolution links clear current barriers. Ordinary observation,
  bounded context, inventory, terminal and historical release gates honor those
  links. SPOT terminal also rejects other unresolved joint financial facts;
  cancellation stays available. Old OIDs/ACLs and numbered script checksums are
  preserved; no account, table, policy, capital or trading grant is created.

- `000048_cashflow_repair.sql`: appends one private immutable application per
  original pending external bill, through the existing signed joint repair.
  Original pending commands, reasons, receipts and hashes remain untouched.
  The exact direct-source profile also accepts selected SPOT/SWAP/funding groups;
  a replacement may change only the application operation ID. Effective readers,
  causal money replay, historical NAV and terminal/release proofs use the new
  application without counting the raw pending bill as a second cash event.
  Provenance binds the application to the actual committed repair root or its
  exact private current-transaction capability. Deferred COMMIT checks reject
  incomplete history; valid pending history retains HALT and cannot admit money
  through ordinary observation routes. New helpers and the application table
  are private. Existing function OIDs/ACLs and numbered checksums are preserved;
  repeat startup applies this version once. No capital or trading grant is made.

- `000049_terminal_repair.sql`: separately bounded signed reconciliation of
  selected retained SWAP terminal pending observations. Original raw facts and
  receipts remain immutable. Append-only approvals, roots and unique membership
  bind the complete fresh terminal/account proof to the exact executed recovery;
  unused approvals do not resolve anything. Versioned predicates preserve old
  denial/terminal/release proofs. Current-owner OBSERVE/carry/NAV may refresh
  sources only when valid terminal pending is the sole financial blocker; no
  monetary pending or corrupt history receives that exception. Physical COMMIT
  rechecks owner, expiry and source age. Reconciliation never marks an order
  terminal, changes money, retires claims or activates trading; native terminal
  and independent release remain separate. Existing function OIDs/ACLs and old
  script checksums are retained; repeated automatic startup applies this version
  once. Policy/import/access provisioning remain explicit privileged operations.

- `000050_legacy_applying_import.sql`: private bounded interrupted-source
  approvals and an exact runtime pending publication. IMPORT creates no money,
  activation or claim release; the existing signed RECOVER route applies the
  original primitives separately. Stored source/target key policies, original
  source digest/cut/signature and target original opening basis are retained.
  Physical COMMIT rechecks freshness, original owner and HALT. An orphan source
  approval blocks ordinary financial writers before its diagnostic root exists.
  Corrupt/missing import provenance invalidates common batch/account proofs.
  Existing gate OIDs/ACLs and prior numbered checksums remain unchanged; no
  accounts, capital or policy defaults are seeded. Runtime/reporting cannot
  edit/read the private source approvals or invoke private proof helpers.

- `000051_legacy_joint_basis.sql`: private original SWAP basis/claim/order/ACK
  proofs for retired outer joint imports. One initial retired source per account
  namespace prevents competing orphan approvals. Explicit signed successors may
  renew expired unpublished grants without changing original history; partial
  unique indexes/FK and private proofs prevent forks, source replacement and
  supersession of a published pending root. A superseded grant cannot publish
  later. Existing duplicate initial origins abort migration without discarding
  signed history. Existing target opening quantities and
  entry prices must agree with the retained source; all original claims at the
  signed cut must be represented. Source import never creates that original
  basis or order skeleton. Native accounting still owns quantities, realized
  PNL, fees, gross budgets and paid funding; terminal/shared release and readiness
  remain separate. Cold common verification rejects missing or repriced
  provenance. Existing OIDs/ACLs and script checksums are preserved, startup
  repeats without bootstrap, and the new proof functions stay private.

- `000052_legacy_cashflow_basis.sql`: match original retired-source transfer
  approvals to independently qualified native bills without inserting approvals.
  Preserve original asset, signed amount, transfer time and historical LIFE
  valuation through mixed transfer/SWAP/funding pending and signed RECOVER.
  Later independent bill approvals do not invalidate earlier imported history;
  changing an original approval does. Direct and staged cashflow admission use
  the same millisecond opening boundary as File/native causal replay, including
  refusal of a previous-millisecond bill. Preserve existing function OIDs/ACLs
  and public error codes; failed signed replacement diagnostics retain only a
  bounded kernel reason code. No capital, policy, account or activation bootstrap.

- `000053_canonical_scalar_fast_path.sql`: avoid recursive function calls for
  scalar JSON children while retaining canonical v1 byte order, UTF-8 escaping,
  numeric rendering and every historical hash. Objects/arrays still recurse;
  object keys keep C-collation sorting and arrays keep their original order.
  The existing function OID, private ACL, strictness and immutability remain.
  No proof cache, rewritten receipt, financial bootstrap or deadline change.

- `000054_snapshot_carry_proof_reuse.sql`: the current snapshot already computes
  `carry_verified`; reuse that result only inside the same stable function call
  when checking carry readiness. A private helper retains the original current
  source, collateral, funding, position and age checks. Every other caller,
  including physical COMMIT guards, still uses the unchanged independent
  `carry_current_ready`. All old OIDs/ACLs and historical output bytes remain.
  Revoke every nonowner helper grant, including inherited default function
  privileges; do not grant the runtime a boolean proof shortcut. No session
  cache, financial bootstrap, receipt rewrite or timeout/freshness change.

- `000055_snapshot_body_pipeline.sql`: move the sixteen existing snapshot layer
  bodies into private `spot_body*` functions; their original entrypoints retain
  their OIDs, ACLs, attributes and complete body/digest/capture-time contract.
  Body layers consume their predecessor's body directly. A public snapshot
  serializes its snapshot digest once, while all historical/financial hashes
  and proofs still run. The entire transformation is transactional and refuses
  unrecognized signatures, return expressions or predecessor calls. Default
  nonowner grants are removed from every body helper. No account, financial
  record, authorization, risk limit or freshness budget is created or changed.
  Future snapshot field/proof changes belong in the corresponding body layer;
  the retained wrappers serialize only. Existing numbered scripts stay immutable.

- `000056_legacy_spot_basis.sql`: independently match a retired core SPOT source
  to already committed native preparation JSON and the original pre-import arm.
  Require exact source membership, the canonical original `arm:<prepare ID>`
  primary key and original binding/writer/version order.
  Link the private proof into legacy financial integrity without changing old
  function OIDs/ACLs; remove inherited nonowner helper grants. Ordinary reads,
  repair and cold readback retain the original order provenance. No order,
  claim, wallet, approval, policy or activation is bootstrapped.

- `000057_collateral_source_context.sql`: expose one account-scoped, bounded,
  read-only native cut for explicit signed balance/position reconciliation. It
  retains HALT, rejects unresolved financial facts/native liabilities/corruption,
  and returns original settled money, replayed position basis and imported
  contract policy. Extend the existing decoded-diagnostic whitelist only for
  unfiltered account configuration and account positions GETs. No new table or
  financial/approval/send/release authority is created. Existing function OIDs
  and ACLs remain; unexpected predecessor route shape rolls back the entire
  migration. New function privileges are private until explicit migration-owner
  access provisioning. Repeated migration startup is a no-op.

- `000058_legacy_spot_prefix.sql`: permit an independently booked native SPOT
  prefix only when every original fact agrees with the signed File cumulative
  snapshots at the original import cut. Preserve original event/receipt IDs,
  fees, holds and gross spending; repair applies the missing leaves once.
  Other pre-import financial families still require their own basis profiles.
  Money-neutral pending receipts retain the existing joint proof's numeric
  spelling for fractional balances. Existing function OIDs/ACLs remain; the new
  helper is private. No table, money, policy or permission is created. Automatic
  numbered startup migration and checksum/idempotency rules remain unchanged.

- `000059_legacy_cashflow_prefix.sql`: extend historical prefix verification to
  independently approved native transfers already APPLIED before import. Match
  the complete original signed File bill history, native command/source hashes
  and original receipt/version; retain the original callback stamp. No transfer
  is credited by IMPORT. Missing SPOT source operations now fail the historical
  proof instead of disappearing through an inner join. Preserve predecessor
  OIDs/ACLs; private helper defaults and atomic unexpected-shape rollback remain.
  No table, account, money, approval, budget or permission is bootstrapped.

- `000060_legacy_derivative_prefix.sql`: match independently booked original
  SWAP fills and paid-funding bills against the complete signed File target at
  its original import cut. Preserve original hashes, observations, receipts,
  fees/PnL, positions and gross costs; IMPORT changes no money. Orphan sources,
  changed facts and pending originals fail closed. Private helper defaults,
  unchanged predecessor OIDs/ACLs, strict atomic upgrade and repeat-startup
  checksum/idempotency rules apply. No tables, policy defaults or trading,
  activation or release permission are created. Prior joint-batch and other
  unsupported histories still require their separate recovery contracts.

- `000061_legacy_parent_prefix.sql`: admit original APPLIED native joint parents
  only with their unchanged member requests/receipts, child operations and
  complete economic leaves in the signed retired File target. Check original
  versions/hashes, cash deltas and funding-bill coverage; missing operations,
  pending parents or changed members fail closed. Historical cut filtering
  excludes later repair parents from the original prefix. Existing independent
  native cash/position/budget proofs remain mandatory. The new helper is private;
  predecessor OIDs/ACLs, atomic unknown-shape rollback and automatic numbered
  startup/checksum/idempotency rules remain. No table, money, policy default,
  activation, release or trading capability is created.

- `000062_legacy_unsent_spot.sql`: retain original PREPARED-only SPOT holds
  during retired File funding recovery. A private historical-cut proof requires
  the original preparation fingerprint/receipt and excludes any prior generic
  or wire-bound quote ARM, ACK, fill, cancellation or terminal evidence. Later
  lifecycle events cannot rewrite the original import cut. Existing sent-order
  proofs remain mandatory when the explicit unsent marker is absent. No ARM,
  release, money, table or policy default is created. Predecessor OIDs/ACLs and
  strict atomic unknown-shape rollback are preserved; numbered startup remains
  checksum-protected and idempotent.

- `000063_legacy_spot_ack_prefix.sql`: verify original SPOT ACK operations and
  zero-money ACK events at the retired File import cut. Require original
  preparation/order scope, hashes, receipts and economic times; late duplicate
  callbacks retain the first event and every original receipt. Missing/orphaned
  or changed evidence fails cold proof. Existing imported bodies without the
  optional ACK projection retain their original bytes. Atomic batch refusals
  expose only a bounded member-kind/reason enum. No table, money, order, release
  or permission is created. Predecessor OIDs/ACLs, private helper defaults,
  checksum/idempotency and atomic unexpected-shape rollback remain.

- `000064_legacy_repaired_parent_prefix.sql`: retain a repaired original joint
  parent's immutable RECOVERY_REQUIRED request, receipt, zero money and empty
  members. Require the signed repair proof and an actual committed resolution
  at the historical import revision; a later root or unused approval cannot
  clear an old barrier. Verify every APPLIED replacement's original children
  and economic leaves as before. No table, helper, money, claim or permission
  is created. Existing OIDs/ACLs remain; unexpected predecessor shape rolls
  back atomically, and checksum-protected repeated startup is a no-op.

- `000065_legacy_cancel_prefix.sql`: verify original SPOT cancel requests and
  attempts at the File import revision, including exact native operations,
  receipts, prepared order scope, contiguous retry counts and retained callback
  times. Missing/orphaned history or changed retry evidence denies cold proof.
  No cancel is sent, counter reset, terminal inferred or money/hold released.
  The proof helper is private even under permissive owner defaults; existing
  OIDs/ACLs remain. No table or policy default is added. Unexpected predecessor
  shape rolls back atomically and checksum-protected repeat startup is a no-op.

- `000066_legacy_terminal_prefix.sql`: retain already completed original SPOT
  terminals during retired File money recovery. Exact native operations,
  fingerprints, receipts, order identity, full original facts and subsidy-release
  status must match at the original import cut. Zero-fill terminals preserve the
  original zero settlement; filled terminals retain their economic-cost hold.
  Missing, changed, duplicate or later native evidence fails cold proof. Import
  does not confirm an order, release money/subsidy or retire a shared claim.
  The proof uses indexed original identities and one account-scoped count,
  rather than scanning the source array again for every terminal. Existing
  OIDs/ACLs remain; the helper is private, no table/default is added, unexpected
  predecessor shape rolls back, and checksum-protected repeat startup is a no-op.

- `000067_legacy_settlement_prefix.sql`: retain already settled original SPOT
  inventory cycles in retired File monetary recovery. Native operation IDs,
  command/facts, fingerprints, receipts, proof digests, member costs and original
  committed amounts must match the signed File cycle at the original import cut.
  File cost is replayed from original member identities/fills, including fees,
  gross execution loss and physical inventory flatness. Missing native settlement
  provenance cannot refund subsidy or create an import. An independently named
  native fill must be selected explicitly in the signed target; the File source
  keeps its original ID. No table, settlement/refund, money, claim retirement or
  permission is created. The helper is private, predecessor OIDs/ACLs remain,
  unexpected predecessor shape rolls back and repeated startup is a no-op.

- `000068_legacy_abort_nav_history.sql`: retain original completed no-send aborts
  and pre-SWAP SPOT NAV observations during retired File monetary recovery.
  Exact native request/receipt, original preparation and pre-abort unsent state
  must prove each abort. NAV measurement/highwater and the native monetary
  fingerprint are verified at the original NAV revision, excluding later fills
  and transfers. Coverage is exact at the import cut, including missing/orphaned
  originals. These historical observations grant no current portfolio valuation,
  new send, release or claim retirement. Two private helpers are added; existing
  function OIDs/ACLs and tables remain unchanged. Unknown predecessor definitions
  roll back atomically; repeated checksum-verified startup is a no-op.

- `000069_legacy_joint_spot_claims.sql`: qualify independently admitted native
  SPOT claims/preparations together with SWAP claims in a retired outer account
  recovery. Original shared-capital receipts/arithmetic are reverified, including
  on cold import readback. SPOT preparation and ARM fingerprints are recomputed
  using their original identity formats and cuts; an unrelated current-state
  diagnostic is not sufficient proof of the original import. The SPOT cohort
  proof runs once per outer basis, not once per claim. Both existing private
  function OIDs/ACLs remain, no table/policy/money/claim or grant is created,
  unexpected predecessor definitions roll back and checksum-verified repeat
  startup is a no-op. Original independent terminal/inventory completions can
  explain interrupted File side effects without executing another release.

- `000070_repair_view_reuse.sql`: a signed monetary repair passes its already
  verified, account-locked pre-write snapshot to one owner-only child helper.
  The helper requires the exact transaction-local staging row, signed replacement,
  original account/version/cut, complete snapshot digest and verified proof flags.
  A standalone view/approval cannot call it, and it cannot span a financial
  version change. Post-write and physical COMMIT proofs remain independent.
  Fresh capital/carry commands consume the proof flags from their complete
  snapshot rather than recomputing those same proofs; original-operation replay
  retains independent full verification. The audited predecessor program and
  pre-write region are checked before installing reuse. Existing public function
  OIDs/ACLs remain; there is no table, money/claim/policy change, source refresh,
  new send permission, session cache or deadline increase. Unexpected predecessor
  code rolls back the entire migration. Repeated checksum-verified startup is a
  no-op.

- `000071_required_ack_completion.sql`: newly explained interrupted ACKs and
  newly supported ACK-bearing core commands require their exact original native
  ACK operation, command, receipt and zero-money event at the original import
  cut. The same private proof runs during accounting and cold verification.
  Existing File-only diagnostic ACKs are not promoted to native ACKs by upgrade;
  their original bodies and `SEND_UNKNOWN` state remain binding. Required IDs
  are unique, indexed native lookups; the File projection is decoded once.
  No table, monetary write, source renewal, grant or public OID/ACL changes.
  Unexpected predecessor code rolls back atomically; checksum-verified repeat
  startup remains a no-op.

- `000072_unfilled_swap_prefix.sql`: retired outer funding recovery may retain
  an original ACKed SWAP with zero fills. The private basis proof resolves its
  unique independently admitted native preparation by intent, verifies the
  original ARM/ACK and refuses booked or pending fills at the historical cut.
  The File source supplies no new preparation, trade, ACK or release. Upgrade
  requires the complete audited predecessor function body and security settings;
  it preserves function identity/ACLs and creates no tables or financial facts.
  Repeated startup is a migration-runner no-op. All 113 migration cases pass,
  including unknown-body/search-path/security refusal and actual 71→72 upgrade.

- `000073_joint_provider_routes.sql`: extend the audited provider request grammar
  for pinned LIFE SWAP order/fill reads and unfiltered account-wide pending-order
  reads, including SWAP chase algos. Existing route function identity/ACLs remain;
  unexpected predecessor code/settings abort the upgrade. No grants or money.
- `000074_joint_source_deadline.sql`: add a private exact-operation source
  deadline checker and a last deferred account-COMMIT constraint for the explicit
  authenticated joint release application. Its transaction-local GUC supplies a
  deadline, not source authentication or new repository authority. An absent GUC
  preserves existing low-level bookkeeping contracts; configured installation
  remains separately gated. No policy values or financial rows are initialized.
- `000075_joint_trade_id_sets.sql`: audited replacements of existing SPOT
  terminal/inventory functions make their flat account trade-ID sets distinct.
  Original product-scoped financial facts remain independent when SPOT and SWAP
  use the same trade ID. Existing function OIDs/ACLs remain; unknown predecessor
  bodies/settings abort atomically. These three migrations share the existing
  checksum/version/advisory-lock runner; repeated startup is a no-op. The actual
  124-case migration gate passes, including upgrades and negative checks.

## 000076–000077: signed logical control and session successors

`000076_signed_native_control.sql` adds empty, immutable, bounded control policy
and approval history. `postgres.control.PgControlRepository` verifies externally
supplied Ed25519 public keys against the registered configuration, then commits
the original signature, facts, cut, ownership effect and receipt together. The
runtime role cannot insert approval/grant rows or invoke private helpers. ACTIVE
requires a separately signed, unused generation and complete quiescent native
facts; HALT, money, spent budgets and history remain intact. Logical UNARMED
keeps the native observation writer in RECOVERY_ONLY so late ACK/fill accounting
is possible. Control results and readback never enable local submission.

`000077_reconciled_session_successors.sql` adds empty parent/child links. A signed
OBSERVE_SESSION operation can reconcile an expired, transitioning, clock-qualified
parent using fresh complete facts and an independent anchor. The deterministic
child starts at the original parent expiry and ends after the explicitly imported
successor duration. Late reconciliation cannot extend that deadline. The old
writer remains current until a separate signed ACTIVATE. Parent clock-only
observations persist rollback checks without claiming market readiness.

Both upgrades preserve original function OIDs, ACLs and prior financial checks.
Unexpected predecessor body, security, volatility or search path rolls back the
whole upgrade. Timestamp comparisons use instants while retaining the original
signed timestamp strings. These migrations do not seed accounts, capital,
approvals, grants or live thresholds. They implement logical control; process,
credential and transport exclusion remain PG4. The migration owner remains
trusted under PG0; SQL checksums do not attest a malicious schema owner.

## 000078: native safety from accounted NAV

`000078_native_safety.sql` creates empty explicit risk policies and immutable
bounded risk events. SQL computes PAUSED/DEGRADED/NORMAL and permanent HALTED from
accounted SPOT NAV or qualified shared portfolio/carry margin. Caller readiness
flags cannot substitute drawdown, wallet or collateral. Permission epochs change
on state/generation/runtime-observer changes; observation versions still retain
each original refresh. This permits a reserved claim to survive a healthy refresh
without accepting a proposal from an earlier permission epoch.

The mandatory deferred financial guard checks current risk, source age/value,
owner/generation, permission epoch and local runtime observer at physical COMMIT.
DEGRADED SPOT recovery probes require explicit opt-in campaign capacity and count
gross actual filled base. Every enabled shared/SWAP allocation requires NORMAL.
HALT never refunds a hold or blocks accounting of original late fill/fee/ACK truth.
An original receipt is diagnostic and cannot heal process admission.

Audited replacements preserve financial function OIDs/ACLs and reject unexpected
predecessor body, security, volatility or search path atomically. New calculators
and mutation functions remain private until explicit scoped provisioning. Existing
mapped readers/writers gain only a scoped `risk_enabled` read on upgrade; an
account with risk enabled needs explicit risk command/read grants on every primary
admission. A migrated account without risk retains its existing profile. There is
no old-schema or File fallback in the production runtime.

The runtime observer GUC binds the adapter lifetime; it is not authentication
against a caller controlling its database credential, or a PG4 physical sender
fence. Stress and inventory/EXIT gates remain R3 work. Native markout is described
below. Configured PostgreSQL
selection, source assembly, scaling and deployment/demo/live gates remain open.


## 000079: native markout permission and capped bootstrap

`000079_native_markout_gate.sql` creates an empty immutable bootstrap policy and
adds a markout check to the existing native risk COMMIT guard. It consumes verified
accounted fills and original independent horizon facts, with explicit bounded
reads. Only insufficient samples can opt into sequential SPOT bootstrap under
per-quote and gross campaign caps. Pending/missing/adverse cohorts cannot use that
exception; other products cannot borrow it. Terminal completion and cohort window
expiry never refund gross actual filled volume. Financial COMMIT recomputes the
current cohort instead of trusting an earlier diagnostic ready read.

The exact audited predecessor body, security, search path and volatility must
match. The upgrade preserves existing function OIDs/ACLs and financial checks;
new helpers remain private, and scoped diagnostics need explicit provisioning.
No account, live threshold, capital or activation is seeded. Framework migration
history makes repeated startup/up calls a no-op. Stress/rolling capacity,
inventory/EXIT, configured assembly and deployment/demo/live gates remain open.


## 000080: native stress and rolling capacity

`000080_native_stress.sql` creates empty immutable stress policies and original
bounded observations. Private SQL calculators use native wallet/fill/reservation
truth, all-one-side stress and actual rolling same-side fills. A deferred guard
rechecks every new SPOT prepare/ARM/quote-ARM at physical COMMIT. Source age,
qualified reference, owner/generation, permission epoch and the local runtime
observer remain mandatory. Reads and original-ID replay grant no permission.

The audited observation-guard extension preserves financial OIDs/ACLs and rejects
unexpected predecessor shape atomically. New helpers remain private; enabled
accounts need explicit scoped stress command/read grants. Existing mapped roles
receive only a scoped enablement read on upgrade. No account, capital, activation
or live default is seeded. Framework migration history makes repeated up/startup
calls a no-op. Inventory/EXIT and configured service installation remain open.


## 000081: signed native execution mode control

`000081_native_execution_modes.sql` creates an empty explicit execution policy and
extends the original signed control journal without replacing its authority.
Enabled accounts need a separately signed initial mode. Complete quiescent facts,
original owner, observer, policy digest and physical-COMMIT approval age are
retained. New mode history cannot reset owner, spent cost or permission.

Native aggregate subsidy limits must equal explicit QUOTE plus EXIT ceilings.
MM independently respects its QUOTE share. Inventory/EXIT child gateways and
settlement remain unqualified and their new-risk admission remains refused.
Audited predecessor checks preserve control OIDs/ACLs. New helpers stay private;
no account, capital, default mode or live threshold is seeded. Framework migration
history makes repeated up/startup calls a no-op. R3 is still open.


## 000082: native inventory/EXIT financial gates

`000082_native_execution_children.sql` creates empty immutable bounded execution
observations, original child membership and exact EXIT settlement proofs. It
requires signed original mode/policy context and recomputes inventory/EXIT
admission at physical financial COMMIT, retaining original gross budgets and
identities. QUOTE/EXIT have separate explicit ceilings; their sum is the existing
native aggregate subsidy ceiling. Fresh complete terminal/account facts reconcile
EXIT holds to actual independent cost once; reads/replay never restore permission.

Audited body/security/path/volatility checks refuse unexpected predecessors before
publishing an upgrade, preserving existing function OIDs/ACLs. New helpers stay
private; explicitly provision scoped writers/readers after upgrading. No account,
capital, approval, default mode or live threshold is seeded. The migration ledger
makes repeated enabled startup/up calls a no-op. All **38 audited 75→82 profiles**
pass with the final source. Native R3 is closed; configured source/service/runner
installation, PG4 fencing, scaling, clean cutover and deployment/live gates remain.

## 000083: fixed hedge observation routes

`000083_hedge_provider_routes.sql` extends only the original diagnostic route
grammar: fixed LIFE SWAP metadata, book, mark/index, funding schedule, position
tiers, account fee group and signed leverage information. Extra parameters,
foreign instruments and arbitrary URLs remain refused in both Python and SQL.
Diagnostics grant no financial, terminal or HTTP submission authority.

The original immutable function body/security/path/volatility is audited before
replacement. Its OID, ACL and callers survive; no table, account, policy, grant,
capital or live threshold is created. Repeated enabled migration startup/up is
a ledger no-op. Configured PostgreSQL service installation retains the PG3 R4 gate.

## 000084: bounded provider diagnostic batch

`000084_provider_page_batch.sql` adds an invoker wrapper for at most eight pages
and 16 MiB of encoded input. It calls the original account-scoped page function
in order within one transaction. Original identity, route, sequence, individual
and cumulative byte limits remain mandatory. A bad tail rolls back every new
page; exact replay retains the original page receipts. This reduces commits for
the fixed hedge market capture while keeping the source age limits unchanged.

The entry point has no implicit PUBLIC/runtime/reporting grant. The privileged
provisioning stage must update the scoped runtime profile; existing roles lacking
the new entry point fail runtime validation. No table, account, policy, money,
approval or send permission is created. Repeated enabled startup/up is a ledger
no-op. This is diagnostic ingestion, not a batch of financial commands.

## 000085: portfolio snapshot carry proof reuse

`000085_snapshot_portfolio_proof_reuse.sql` removes a repeated carry-integrity
proof from the portfolio layer of one STABLE snapshot. The caller has just built
the original carry verdict in the same snapshot; an owner-only helper consumes
that verdict. The independently callable `portfolio_verified`, mutation paths
and COMMIT verifiers are unchanged. No result survives across calls/commands and
no stored receipt, GUC or process cache supplies the verdict.

Both predecessor function bodies, security modes, volatility and search paths
are audited before modification. The original wrapper retains its OID and ACL;
the helper has no PUBLIC/runtime/reporting/default-role grant. No table, policy,
funds, activation or source/SQL timeout changes are introduced. The migration
ledger makes repeated enabled startup/up a no-op.


## 000086: native SWAP event origins

`000086_joint_recorder_events.sql` extends diagnostic create/fill/status reporting
to original SWAP preparations. Registered source, account, product, controller,
wire/order ID, size, side, native leverage and OPEN/CLOSE must match. SPOT retains
its captured-action descriptor requirement. Funding remains diagnostic; only the
signed native accounting path changes money.

The original validator body/security/path/volatility is audited. Replacement
preserves its OID and ACL, adds no helper, grant or table and bootstraps no funds,
policy or activation. The migration ledger makes repeated enabled startup/up a
no-op; unexpected predecessor drift aborts the upgrade.

## 000087: fixed SPOT market diagnostic routes

`000087_spot_market_routes.sql` adds only exact LIFE-USDT SPOT instrument, book
(`sz=64`) and account trade-fee GET queries to the existing provider-page route
validator. Other instruments, extra parameters and query widening stay denied.
These are decoded diagnostics, not authenticated financial facts or send grants.

The predecessor body/security/path/volatility is audited before replacement;
the original OID and ACL are preserved. There is no new table, grant, policy,
balance or activation. Repeated enabled startup/up remains a migration-ledger
no-op; drift aborts atomically. Previously applied scripts are unchanged.

## 000088: conservative subsidy at native monetary precision

`000088_quote_subsidy_quantum.sql` rounds required prospective subsidy UP to
the existing scale-18 monetary quantum at preparation and final send. Decimal
fee/quantity products can require finer precision than a currency hold; they
must neither be rounded down nor make every such quote unrepresentable.
Preparation requires exactly that conservative rounded hold; final send still
cannot enlarge/refund it. Prices, fees, net economics, budgets, freshness and
all original financial checks remain exact and unchanged.

Both predecessor function bodies/security/path/volatility are audited. OIDs,
ACLs, domains, tables and existing financial rows remain unchanged; no funds,
policy or activation are created. Existing valid scale-18 requirements produce
the same result. The original migration ledger handles repeated startup/up;
predecessor drift rolls back the whole migration.

## 000089: reuse the release command's own snapshot proof

`000089_release_snapshot_proof_reuse.sql` removes a second portfolio verification
between the command's complete `spot_state` check and its first financial write.
It reads the verified portfolio flag from that exact local snapshot, after the
original account lock and cut/recovery checks. A missing or false flag refuses
release. Source freshness is still checked at the original time, and all deferred
COMMIT proofs and source deadlines remain independent and unchanged.

The exact predecessor SHA, security, search path and volatility must match.
Existing OID/ACLs, tables, financial history, limits and roles are retained; no
new helper or grant is installed. Repeated startup/up is a ledger no-op. Drift
aborts atomically before version 89 is recorded.

## 000090–000091: query plans and persistent state cuts

`000090_native_query_plans.sql` keeps the exact lifecycle, action and accounting
verification queries, changing their implementation from SQL to PL/pgSQL so a
backend can retain the query plan. Every invocation still reads and verifies its
current MVCC snapshot; no verdict, account balance or permission is cached.
Function OIDs, ACLs, owners, STABLE semantics and independent COMMIT guards remain.
The migration refuses unexpected predecessor bodies or security metadata.

`000091_persistent_state_cut.sql` fixes a cut that previously changed at the same
account version when carry data expired. The public digest now has the explicit
`life-persistent-cut-v2:` domain and hashes the full verified body except the
computed `carry.ready` flag and redundant top-level `recovery_required` summary.
All persisted carry/head inputs, money, verification and pending flags remain
hashed. The returned snapshot still includes both live flags. Original native
prepare/ARM/COMMIT freshness checks are unchanged and independently deny expired
carry; a stable cut cannot extend a feed lifetime or grant trading permission.

This is a cut-format upgrade. A new runtime must load the complete pinned
migration bundle and read a fresh cut. In-flight captures from the old runtime
are not reusable after migration. Original operation command bytes, historical
cuts and exact replay arguments are retained; no historical cut is rewritten.
The selected File cutover still requires stopped, reconciled state.

## 000092: shared proofs within one primary snapshot

`000092_snapshot_shared_proof_reuse.sql` uses the carry/shared proofs already
built inside the same STABLE snapshot when checking portfolio history. The new
helper is owner-only, including when deployment default privileges grant other
roles access. The later release layer can reuse a true shared verdict with an
existing shared head; no-head/no-model early returns retain independent release
verification. All returned fields and persistent cut bytes stay the same.

No standalone verifier, financial command, trigger or COMMIT proof is changed.
There is no stored or cross-statement verdict, clock extension, new permission,
money/history rewrite or policy default. Exact predecessor bodies and security
metadata must match, including the shared verifier whose final release check
justifies reuse. Drift rolls back the complete migration. Repeated startup/up
uses the existing ledger and performs no further DDL.

## 000093–000094: fresh proof reuse at COMMIT and within primary reads

`000093_commit_proof_group.sql` replaces four account-version constraint triggers
with one deferred trigger, preserving their original version-change condition.
The new owner-only STABLE checker builds fresh post-write carry/shared/NAV/release
proofs once per invocation. All original predicates and real-clock current-source
checks remain. Original standalone trigger functions and verifier bodies, OIDs,
owners and grants are unchanged. No pre-write or persistent verdict is reused.

`000094_snapshot_financial_proof_reuse.sql` uses a strictly false `unverified`
flag from the same primary read to avoid repeated successful actions, SWAP,
accounting, funding and capital checks in later body layers. Invalid or NULL
flags still invoke the full independent verifier. Portfolio fallback preserves
the complete original helper and its short-circuit order. A private portfolio helper
accepts only the verdicts established inside that read. Nested current-source
checks, financial writes and deferred COMMIT remain independent. Public snapshot
fields, negative flags and persistent cut bytes are preserved.

Both migrations audit exact predecessor bodies/security metadata; 93 also
checks the original trigger definitions. Unexpected drift refuses the upgrade
atomically. New helpers revoke every non-owner grant, including grants inherited
from deployment default privileges. Startup/up is repeatable through the existing
migration ledger. These changes introduce no table, history rewrite, financial
policy default or runtime permission. They do not by themselves qualify PG3.

## 000095: recorder verification reads and ASCII identity strings

`000095_recorder_verification_reads.sql` joins the executor stream with its stage
operation and preceding stream row in the existing verification query. The
event verifier is unchanged; its experimental join showed no clear benefit
and was discarded. Original expected effects, transition, contiguous
sequence, parent/head, receipt, hash, native-row and orphan checks remain; no
history is discarded or verdict saved between statements.

Printable ASCII JSON strings return their existing JSON representation directly
from `recorder_identity_text`. Unicode/DEL escaping, object ordering and type/
numeric rejection keep the predecessor behavior and permanent identity bytes.
The fast path removes a per-character concatenation timeout on a valid 512-KiB
ASCII input under the existing one-second statement deadline.

The migration audits exact predecessor sources and function security metadata,
preserves existing OIDs/owners/ACLs and adds no function, table, index or grant.
Unexpected drift rolls back atomically; repeated startup uses the migration
ledger without reapplying DDL. Financial commands, COMMIT guards, source clocks,
cut format and policy limits are unchanged. Focused checks and configured maker
qualification are recorded separately in the PostgreSQL plan.

## 000096: repeated predecessor reads and status-history index

`000096_recorder_history_lookups.sql` removes two duplicate predecessor lookups
inside each `recorder_event_effects` call. ORDER and STATE reuse the exact row
already read with the same account, key and version in that STABLE invocation;
STATUS still performs its own lookup. Subsequent invocations read again. Source
validation, effect bytes, hashes, history and all financial/COMMIT checks remain.

The partial B-tree index `recorder_status_by_wire_version` covers
`(account_id, after_row->>'order_id', source_version)` for STATUS effects. The
existing history index starts with a key hash absent from this query, so it
cannot efficiently select one wire across growing unrelated histories. The new
index leaves the original timestamp/version sorting intact. It deliberately
indexes text rather than casting JSON timestamps, preserving malformed-value
handling and avoiding eager timestamp casts during index maintenance. It adds
storage and write maintenance for STATUS rows only.

Exact helper bodies/security metadata are audited before changing the effect
builder. Existing function OIDs, owners, ACLs, tables and data are preserved.
An existing object with the new index name refuses the upgrade atomically;
ordinary repeated startup/up uses the migration ledger without reapplying DDL.
No financial verdict or permission cache is introduced. Existing backend query
plans and asyncpg prepared-statement caching remain; no external cache is needed
for this change. Focused query-shape tests are not PG3 latency qualification.

## 000097: atomic single-action metadata dispatch

`000097_atomic_metadata_dispatch.sql` captures the verified DISPATCH cut under
its original account lock, avoiding the runner's separate full cut read. The
single-action runner supplies the confirmed CLAIM's successor version; an
intervening writer conflicts. Multi-action publication and predecessor schemas
retain the original explicit-cut path. Original action bytes/timestamps, owner,
session, fresh full-state audit and physical-COMMIT guards remain. This operation
records executor acceptance only; it grants no PREPARE, ARM or HTTP permission.

The audited predecessor command is unchanged. A private core accepts only the
view constructed inside this command; runtime and reporter cannot call it.
Automatic upgrade copies explicit EXECUTE grants from the old metadata command
to the equivalent public entrypoint, excluding PUBLIC/default privileges.
`provision_access` grants it on fresh installations, and migration-97 runtime
admission verifies its permission on every read/write admission. No financial
verdict, source observation or permission is cached across commands. Ordinary
repeat startup uses the existing migration ledger and does not repeat DDL.
