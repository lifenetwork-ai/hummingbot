# PostgreSQL runtime operations

Implementation is tracked in [the port plan](plans/POSTGRES_CLOUD_RUN_PORT_PLAN.md).
Do not deploy until the required phases and offline verification gates pass.

## Local environment

Install the declared environment dependencies (`psycopg2` and `python-dotenv`), then
copy `.env.example` to `.env` and supply local settings. Process environment takes
precedence, including empty values; literal secret values are not interpolated.
`HBOT_ENV_FILE` selects an explicit local file; a missing explicit file fails.
Cloud Run (`K_SERVICE` present) ignores local `.env` and rejects `HBOT_ENV_FILE`.
Use injected environment values and explicit Secret Manager versions there.

`DATABASE_URL` accepts `postgresql://` or `postgresql+psycopg2://`. Percent-encode
special characters in URL credentials; SSL query settings such as `sslmode=require`
are passed to the driver. Only the synchronous psycopg2 driver is supported.
Connections use `public`, connect timeout 5s, runtime statement timeout 2s, lock timeout 1s,
pool size 3, overflow 2 and pool checkout timeout 2s. Budget additional migration
and ownership sessions and both old/new processes during deployment overlap.
Session advisory locks require direct/session-pooled PostgreSQL connections;
transaction-pooling proxies are unsupported.

## Migration policy

`ENABLE_AUTO_MIGRATION=true` (default) runs packaged SQL in name order at service
startup before connector bootstrap. Each file has one transaction, and one session
advisory lock covers the batch. Failed files roll back; already committed files
are replayable. Lock wait is bounded to 60s and total DB bootstrap to 180s.

`ENABLE_AUTO_MIGRATION=false` performs read-only schema validation without DDL.
A role with schema usage, table DML and sequence usage/select can run an existing
compatible schema. Missing objects or incompatible definitions fail before trading.
Migrations require object ownership/appropriate DDL privileges in the dedicated
database, not superuser privileges.

Explicitly apply SQL regardless of the automatic flag:

```sh
make migrate
# Or with a selected environment's interpreter:
make migrate PYTHON=/path/to/python
```

Connection errors expose safe error types/SQLSTATE, not driver messages or DSNs.
SQL names/checksums are logged. Correct the reported configuration/privileges/schema
issue and restart; do not delete/reset the DB. Historical deployed scripts are
immutable. Add compatible forward scripts; large backfills/index builds belong
in an explicit maintenance path.

## Existing databases

Normal startup refuses mixed-case legacy tables even if canonical tables also
exist. For a legacy PostgreSQL database, stop **all** old writers and revisions,
take an operator-managed backup, and run the explicit maintenance operation:

```sh
python -m hummingbot.model.db_migration.postgres --legacy-cutover --writers-stopped
```

The operator flag records the prerequisite; the tool cannot discover every
potential external legacy writer. The SQL runner uses the same migration lock and
executes normalization, canonical SQL and validation in one transaction. It renames
known tables/indexes/constraints/sequences without copying business rows. Both
names present, unexpected definitions, orphan FKs or duplicate market-state keys
fail and roll back for explicit reconciliation. Replay preserves sequence values.
Only compatible canonical-name application images can be rolled back afterward.
No automatic down migration/rename reversal is provided.

SQLite import is optional and has not been implemented; keep any original DB
read-only and do not point the PostgreSQL runtime at a SQLite file. Existing local
SQLite tooling is transitional and is not a fallback when `DATABASE_URL` is present.

## Disposable PostgreSQL verification

```sh
make test-postgres PYTHON=/path/to/hummingbot/python
```

The target starts PostgreSQL 14 on an ephemeral loopback port with a random test
password and no host volume, runs production migrations/tests, and removes only
its own container. It never loads `.env`. Docker/image availability is required;
failure to provide test infrastructure fails the command instead of skipping tests.
Direct integration invocation requires `HBOT_TEST_POSTGRES_URL` and a database
name starting `hbot_test_`; **that test database's public schema is erased**.
