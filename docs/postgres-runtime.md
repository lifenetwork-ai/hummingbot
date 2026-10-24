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

## Environment connector credentials and YAML

With PostgreSQL selected, exchange secrets come only from process variables (or
local dotenv input). Names are `HBOT_` plus each existing connector field in
uppercase: OKX spot uses `HBOT_OKX_API_KEY`, `HBOT_OKX_SECRET_KEY`,
`HBOT_OKX_PASSPHRASE`; perpetual uses the `HBOT_OKX_PERPETUAL_` equivalents.
`HBOT_OKX_REGISTRATION_SUB_DOMAIN` retains the existing `www`/`app`/`my` validation.
Only selected connectors require complete credentials; an unconfigured/stopped bot
can bootstrap without exchange secrets. Environment mode requires neither
`HBOT_PASSWORD` nor `.password_verification` nor encrypted connector YAML. It never
writes environment credentials back to files or PostgreSQL.

`hbot connect okx` checks configured environment values through the existing
balance/connection APIs; `hbot connect --fields okx` lists field declarations.
`hbot balance okx --units-only` skips rate-oracle pricing. These commands contact
the selected exchange; unit tests mock that external boundary. `--keys-stdin`,
`--replace` and password input do not update environment credentials. Set values
in the local environment or Secret Manager and restart/deploy the intended version.

Client/strategy/controller/fee YAML formats remain unchanged. Service bootstrap
reads deployed YAML without refreshing templates or writing defaults back. The
controller runner uses a reproducible in-memory wrapper. Existing local YAML
editing remains available; packaging/deploying a new YAML version is the durable
cloud update mechanism. The service's final lifecycle/revision integration remains
part of the implementation plan, not evidence of a completed deployment.

## PostgreSQL CLI control

CLI commands bootstrap the same environment settings once at the CLI entrypoint.
DB-dependent commands validate schema with **no automatic DDL**, even when
`ENABLE_AUTO_MIGRATION=true`; service-start migration and explicit `make migrate`
are separate operations.

`hbot start <deployed-config.yml>` and `hbot stop` change PostgreSQL desired state.
They never spawn a detached process or signal a PID in PostgreSQL mode. Use
`--timeout 0 --json` to receive a persisted **requested** receipt immediately, or a
positive timeout to await the service's matching control-version acknowledgment.
A timeout does not undo the persisted request. Automation should read
`hbot status --json`, pass its `control_version` through `--expected-version`, and
reuse that expected version on retries; a superseded version fails instead of
overwriting another command. `--replace` requests a controlled restart/replacement;
`--force` is rejected because local PIDs cannot control another service instance.
The foreground service host is implemented in the subsequent service phase.

Status uses a PostgreSQL-computed heartbeat age; age >=15s, a missing heartbeat or
missing owner means stale/unknown, even if a prior snapshot said running. Config,
control/ack versions and revision remain visible for diagnosis. Config paths are
stable YAML identities. Selecting an imported file is not proof that it was deployed;
start checks an available service catalogue, and the service must validate its own
configuration before acknowledgment/trading.

History uses exact configuration filters and the existing PerformanceMetrics
accounting formulas with last recorded fill prices and recorded quote fees. It
prints that valuation basis and does not fetch live prices. Missing recorded quote
fees cause an explicit performance error rather than fabricated fee conversion.
Local YAML editing stays local; on Cloud Run, create/config mutation and software
self-update require a new versioned deployment. PostgreSQL `deploy` selects an
already packaged configuration instead of silently editing an ephemeral file.
