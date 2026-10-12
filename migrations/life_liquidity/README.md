# LIFE PostgreSQL migrations

This is the single schema migration entry point for the PostgreSQL backend.
It currently installs **infrastructure metadata only**. It does not import file
state, create account balances, grant ownership, enable trading, or connect the
LIFE controller to PostgreSQL. The spike SQL under `test/` remains a synthetic
fixture and is deliberately excluded.

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
```

`make run` / quickstart, the detached `hbot` engine and the legacy interactive
launcher await the same runner before constructing the application/connectors.
Repeated startups verify checksums and skip applied scripts. Failure or
cancellation blocks that startup. An inherited `LIFE_MIGRATION_DSN` is removed
after the enabled migration stage before application children can inherit it;
file secrets are never promoted into `os.environ`. This does not select the
application/recorder PostgreSQL backend or provide permission to trade.

The migration driver must be installed in the startup environment. The current
Docker/Cloud image still needs the planned dependency/SQL-bundle packaging and
qualification. For Cloud Run workers, keep auto migration off and run the
separate migration Job with its own role/identity; the application worker
should receive runtime privileges only. Optional startup mode is intended for
controlled local/single-process development, with workers drained before
incompatible schema changes.

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
2. Execute a separate migration Job with the pinned release bundle, one task,
   parallelism one and automatic retries disabled; wait for successful completion.
3. Check matching schema/bundle compatibility before starting an **unarmed**
   worker with the runtime role. Stop deployment if migrations fail.
4. Follow the independent ownership, demo and trading release gates before rearm.

The database lock serializes accidental duplicate job invocations. A task-count
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
