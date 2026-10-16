# LIFE PostgreSQL migrations

This is the single schema migration entry point for the PostgreSQL backend.
It installs **storage lineage and an empty typed financial schema with guarded transaction functions**. It does not import file
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
`--build-arg LIFE_POSTGRES=true` to include the pinned optional asyncpg driver;
the default `false` retains the file-only dependency set. The full Linux image,
SQLAlchemy recorder and deployed identity/TLS are still unqualified.

For real database tests without using a configured DSN or `.env`:

```sh
make life-postgres-test LIFE_PG_TEST_ARGS="--output /tmp/life-pg2-run"
```

This starts the pinned disposable local PostgreSQL container, uses synthetic roles
and accounts, runs schema/concurrency/pool/TLS/fault/crash tests and removes it.
The output directory must be fresh and under `/tmp`. It does not qualify managed
failover, disk/host loss, backups or PITR.

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
