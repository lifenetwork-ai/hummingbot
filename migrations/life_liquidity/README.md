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
