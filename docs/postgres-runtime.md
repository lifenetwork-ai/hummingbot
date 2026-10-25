# PostgreSQL runtime operations

Implementation is tracked in [the port plan](plans/POSTGRES_CLOUD_RUN_PORT_PLAN.md).
Do not deploy until the required phases and offline verification gates pass.

## Local environment

Install the declared environment dependencies (`psycopg2` and `python-dotenv`), then
copy `.env.example` to `.env` and supply local settings. Process environment takes
precedence, including empty values; literal secret values are not interpolated.
`ENV_FILE` selects an explicit local file; a missing/empty explicit file fails.
`HBOT_ENV_FILE` remains a compatibility alias; `ENV_FILE` takes precedence.
Cloud Run (`K_SERVICE` present) ignores local `.env` and rejects either explicit file selector.
Use injected environment values and explicit Secret Manager versions there.

Non-secret defaults live in
[defaults.py](../hummingbot/client/config/defaults.py), following the centralized
defaults/process-environment pattern reviewed in `life-wallet-backend/conf`.
New env names use descriptive uppercase keys without a `LIFE_` prefix. Secrets,
the PostgreSQL URL and live trading limits have no fabricated default values.
Old local LIFE migration settings are renamed without the prefix (`AUTO_MIGRATE`,
`MIGRATION_DSN`, `MIGRATION_DIRECTORY`, `MIGRATION_SSL_CA_FILE`,
`MIGRATION_LOCAL_INSECURE`, `MIGRATION_LOCK_TIMEOUT_SECONDS`,
`MIGRATION_STATEMENT_TIMEOUT_MS`, `MIGRATION_DDL_LOCK_TIMEOUT_MS`). They belonged
to the previous LIFE tooling and are not aliases consumed by the current runtime.
Use `DATABASE_URL` (a supported PostgreSQL URL) and `ENABLE_AUTO_MIGRATION` for
the implemented runner. Its packaged script directory and bounded timeout/TLS
policy remain as documented below; renaming an old key does not activate it.

| Variable | Default / behavior |
|---|---|
| `ENV_FILE` | Repository-root `.env` when omitted; explicit relative paths use the working directory |
| `STORAGE_BACKEND` | `file` when neither `DATABASE_URL` nor `K_SERVICE` exists; otherwise `postgres` for compatibility. Explicit `file`/`postgres` wins locally. |
| `ENABLE_AUTO_MIGRATION` | `false` in the current centralized defaults; applies to PostgreSQL service startup |
| `BOT_ID` | `hummingbot` |
| `PORT` | `8080` locally; Cloud Run injects it |

Choose the backend before starting a process:

```dotenv
# PostgreSQL service: hbot serve
STORAGE_BACKEND=postgres
DATABASE_URL=postgresql+psycopg2://hummingbot:replace_me@127.0.0.1:5432/hummingbot
ENABLE_AUTO_MIGRATION=true
```

For the original local runtime, set `STORAGE_BACKEND=file` and use `hbot start`.
It uses the existing SQLite recorder, local JSON/PID state and original credential
flow; an otherwise present `DATABASE_URL` is ignored by that runtime. PostgreSQL
selection with a missing/bad/unavailable URL fails without falling back to files.
Cloud Run and `hbot serve` require PostgreSQL. Changing backends requires stopping
and restarting the process; it does not import or synchronize existing data.
The explicit operator command `make migrate` still targets PostgreSQL regardless
of the selected trading backend or automatic-migration flag.

`DATABASE_URL` accepts `postgresql://` or `postgresql+psycopg2://`. Percent-encode
special characters in URL credentials; SSL query settings such as `sslmode=require`
are passed to the driver. Only the synchronous psycopg2 driver is supported.
Connections use `public`, connect timeout 5s, runtime statement timeout 2s, lock timeout 1s,
pool size 3, overflow 2 and pool checkout timeout 2s. Budget additional migration
and ownership sessions and both old/new processes during deployment overlap.
Session advisory locks require direct/session-pooled PostgreSQL connections;
transaction-pooling proxies are unsupported.

## Migration policy

`ENABLE_AUTO_MIGRATION=true` runs packaged SQL in name order at service
startup before connector bootstrap. Each file has one transaction, and one session
advisory lock covers the batch. Failed files roll back; already committed files
are replayable. Lock wait is bounded to 60s and total DB bootstrap to 180s.
SQL scripts use unqualified table/index/sequence references (no `public.` prefix).
The shared runner sets `SET LOCAL search_path = public` for each script, including
legacy cutover, and connections/validation continue to target the canonical schema.

`ENABLE_AUTO_MIGRATION=false` (the current code default) performs read-only schema validation without DDL.
A role with schema usage, table DML and sequence usage/select can run an existing
compatible schema. Missing objects or incompatible definitions fail before trading.
Migrations require object ownership/appropriate DDL privileges in the dedicated
database, not superuser privileges.

Explicitly apply SQL regardless of the automatic flag or local trading backend:

```sh
make migrate
# Or with a selected environment's interpreter:
make migrate PYTHON=/path/to/python
```

Connection errors expose safe error types/SQLSTATE, not driver messages or DSNs.
SQL names/checksums are logged. Correct the reported configuration/privileges/schema
issue and restart; do not delete/reset the DB. Historical deployed scripts are
normally immutable. The requested removal of `public.` is a formatting-only replay
of existing idempotent scripts: checksums change, catalog names/data do not. Use
compatible forward scripts for future schema changes; large backfills/index builds belong
in an explicit maintenance path.

Performance review script `008_add_recovery_lookup_indexes.sql` adds pending-order
and controller-history indexes. With automatic migration disabled, run `make migrate`
before deploying this revision. For a large active database, stop trading and
schedule the index build in a maintenance window: ordinary `CREATE INDEX` can
wait on/block writers, and the existing bootstrap deadline still applies.
See [the performance review](postgres-performance-review.md) for measurements,
query budgets and the decision to retain the existing in-memory caches.

### Decimal storage

PostgreSQL stores the existing `SqliteDecimal` fields as unscaled `NUMERIC`:
order/fill quantities and prices, converted fill fees, position amounts/PnL and
market-data values. Python reads/writes `Decimal`; there is no fixed database
scale to round away BTC satoshis or derived fee digits. The upstream SQLite
format retains its scaled-integer behavior. Funding and executor summary FLOAT
columns are outside this quantity-storage change.

`013_preserve_decimal_precision.sql` converts the 15 previous BIGINT fields by
dividing their values by 1,000,000 exactly once. It checks the column type before
conversion, so replay preserves already-unscaled values. Millisecond event
timestamps stay BIGINT; the market-data collector's decimal-seconds timestamp
is converted with its original scale. Previously truncated digits are not
reconstructed. Daily reporting now sums natural units, and observational
reconciliation reports any quantity/price difference instead of ignoring up to
0.000001 BTC.

For an existing scaled schema, stop all writers before applying this revision.
An older scaled-integer revision is incompatible with the converted schema;
schema validation must reject it. With auto migration disabled, apply migrations
explicitly first. No existing Demo database is changed merely by editing or
building this source. The scheduled funding observation retains its original
image/schema until its bounded check finishes.

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
No automatic down migration/rename reversal is provided. Removing SQL text's
`public.` qualifiers does not move any existing table to another schema.

SQLite import is optional and has not been implemented; keep any original DB
read-only and do not point the PostgreSQL runtime at a SQLite file. Existing local
file mode is an explicit local backend; it is never an automatic fallback after a
PostgreSQL connection or validation failure.

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
cloud update mechanism. The service publishes active configuration hashes and revision in PostgreSQL.
PostgreSQL controller configurations require unique, nonempty stable `id` values
so restarts select the same persisted position and risk state.
See the [deployment instructions](../deploy/cloudrun/README.md) for packaging and the release contract.

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
Run `hbot serve` for the foreground service host; it observes and acknowledges versioned control state.

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


## Ownership and unresolved exchange effects

The service uses a dedicated PostgreSQL session advisory lock plus owner UUID and
generation. Use direct/session-pooled connections, not transaction pooling. All
order creation requires the installed service owner; calling the old detached
engine directly with PostgreSQL cannot submit orders. Only OKX spot/perpetual
have reviewed mutation paths. Pending client IDs are committed before placement;
a transport failure is never automatically retried as a new logical order.

Startup reconciles restored order IDs, fills and complete account open-order
lists before strategy ticks. Unknown orders or unmatched perpetual positions
block new risk. An unclean runtime exit, unresolved submission/account mutation,
or failed reconciliation persists HALT in `bot_state.recovery_state`. Restarting,
clearing a pending flag, or successfully reconnecting the DB does not clear it.
Read status/recovery details, establish that every prior process is stopped, and
reconcile the dedicated exchange account with persisted orders/fills/positions
before an explicit operator-reviewed database maintenance change. No automatic
recovery-reset command is provided, and changing only `halt` without resolving
its cause is insufficient. Do not delete order history to make startup pass.

A recorder rollback latches that manager unhealthy and blocks subsequent ORM
flushes/bulk writes, including tracking snapshots. A failed fill cannot be followed
by a committed snapshot claiming it was recorded. Exit and reconcile from durable
state and exchange fill truth in a fresh service; do not retry/reset that manager.

Clean shutdown checkpoints known controller drawdown/PnL peaks, preserves existing
V2 position holds, cancels working orders and confirms terminal status before
closing the runtime marker. A false/incomplete cleanup result must terminate the
host while retaining ownership until process death. The service adds a separate 9s process watchdog. Actual SIGTERM/standby behavior
has been verified by the spot/V1 deployment-image harness; V2 spot/perpetual
position/risk restoration uses existing controllers and position holds. A fill
arriving while V2 cleanup snapshots positions retains HALT and requires operator
position reconciliation. Arbitrary custom strategies/executor timers
are not automatically reconstructed after a crash; the persistent HALT prevents
silently resetting their risk state.

## Funding while trading is stopped

The same owner service observes configured and historically traded OKX USDT
perpetual instruments using authenticated account-bill GETs. It continues while
the strategy is stopped, expired, HALTed or spot-only. It never starts a strategy
or sends trading/account mutations. `FUNDING_SYNC_INTERVAL_SECONDS` defaults to
120 (allowed 1–3600); pending catch-up uses bounded batches of two pages with a
one-second scheduling delay. Standby instances do not ingest bills.

`funding_bills` stores exact NUMERIC amounts and exchange bill identity scoped by
account/environment. The existing `funding_payments` event table is preserved;
identified bills take precedence over overlapping legacy event groups in daily
reports. The native last-payment poller/recorder is suppressed only on managed
PostgreSQL perpetual connectors. Original file mode remains unchanged.

Bill pages and their checkpoint in `bot_state.recovery_state.funding_sync` commit
atomically under owner fencing. Restart replays an overlapping one-hour window,
including bills for positions already closed. A conservative 89-day history limit
is reported explicitly when exceeded. The recent/archive readers preserve their
existing authentication, time synchronization and throttling. Historical rates
are null when unavailable; current rates never substitute for settlement rates.

The service must be running to poll. An absent container collects nothing until
restart backfill; no separate watcher container or scheduler is required. The
Cloud Run service needs its configured always-allocated CPU and a retained owner
instance for continuous observation.
