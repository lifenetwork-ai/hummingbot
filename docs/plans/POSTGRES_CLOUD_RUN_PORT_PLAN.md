# Hummingbot PostgreSQL and Cloud Run Port Plan

Created: 2026-10-24 — Asia/Ho_Chi_Minh.

Status: **P0–P6 and required local release verification complete for the declared OKX spot/perpetual and V1 PMM/V2 PMM controller scope**. All required checklists have implementation/review/test evidence below. Optional SQLite/credential import is explicitly deferred because it is not needed. Cloud Run staging is not required; actual cloud provisioning, IAM/networking and live exchange operation remain unverified and have not been performed.

Reviewed sources:

- Hummingbot checkout `34432aadf`.
- Reference repository `life-wallet-backend`, checkout `35c0295`, at `/Users/deeznuts/life-wallet-backend`.

## 1. Purpose and scope

**Port the existing open-source Hummingbot application to Cloud Run with PostgreSQL for durable trading and runtime state. Reuse existing source code wherever possible and add components only when an existing component cannot meet the deployment requirements.**

The agreed configuration split is:

| Concern | Target source |
|---|---|
| Orders, fills, market tracking state, executors, positions and other recorded trading data | PostgreSQL, using existing ORM models and recorder logic |
| Bot selection, desired state, heartbeat and runtime status | PostgreSQL |
| Ordinary client, strategy and controller configuration | Existing YAML format and loaders |
| Database connection, startup options and exchange API credentials | Process environment; optional `.env` locally; Secret Manager environment injection on Cloud Run |
| Logs | stdout/stderr, collected by Cloud Logging |
| Python code, SQL scripts and other immutable application assets | Container image |

Objectives:

- Run the existing engine headlessly and recover durable state on a fresh instance.
- Keep YAML configuration where the original application already supports it. Moving ordinary configuration into PostgreSQL is outside this port.
- Move connector API keys, secrets and passphrases to environment variables for the cloud runtime. Do not introduce a PostgreSQL credential store.
- Run SQL migrations at each service start **when `ENABLE_AUTO_MIGRATION=true`**, including restarts, cold starts and new revisions. The current centralized code default is `false`; deployment examples explicitly select `true`.
- With `ENABLE_AUTO_MIGRATION=false`, skip migration execution and validate the existing schema before allowing trading. Missing or incompatible schema must fail startup.
- Keep SQL simple and idempotent: tables, indexes, constraints and small helper functions where necessary.
- Standardize all application-owned PostgreSQL identifiers to lowercase snake_case as part of this port, including existing tables and their dependent objects.
- Preserve the engine, connectors, strategies, recorder, configuration validation and CLI wherever their behavior remains applicable.

Scope boundaries:

- The first release retains **one logical bot, one dedicated database and one dedicated exchange account/subaccount**. Several processes may overlap during deployment, but only one may own order submission.
- Reuse SQLAlchemy and the existing models. A full async rewrite, a new ORM, Redis, a message broker, a dashboard or a separate orchestration platform is not required.
- Keep the existing credential encryption implementation for explicitly selected legacy compatibility or a one-time import. It is not required when credentials come directly from the environment.
- The [LIFE/OKX implementation plan](LIFE_OKX_IMPLEMENTATION_PLAN_TDD.md) is separate. At the reviewed checkout, the LIFE directories contained caches without corresponding Python/SQL source to reuse.
- Inventory custom strategies and connectors that write their own files. Claim cloud compatibility only for paths that have been reviewed and tested.
- Runtime trading state must not depend on local SQLite, JSON snapshots or PID files. YAML and optional local `.env` are configuration inputs, not substitutes for durable runtime storage. Temporary files may only hold reproducible, disposable data.

### 1.1. One application, two workstreams

The infrastructure port and preservation of trading behavior are two workstreams within **one codebase and one shared trading engine**:

| Workstream | Expected outcome |
|---|---|
| Port storage and service operation | PostgreSQL persistence, configurable startup migration, environment credentials, headless container lifecycle and Cloud Run deployment configuration |
| Preserve existing bot behavior | Reuse strategy decisions, connectors, order tracking, recorder semantics, executor orchestration and performance calculations; prove compatibility with regression and offline E2E tests |

Local development and the Cloud Run target use the same PostgreSQL runtime and trading code. Their configuration delivery differs: local `.env`/YAML versus injected environment variables and deployed YAML. This plan does not require a second trading implementation or continued maintenance of a complete SQLite/file-backed runtime. Existing legacy import/credential helpers remain optional compatibility tools, not an automatic fallback. Adapt shared code only where the port requires it and cover behavior changes with tests.

**Cloud Run staging is not required.** Release verification consists of existing regression tests, real PostgreSQL integration tests and offline E2E tests of the deployment image. These tests establish application behavior and container-contract coverage; actual cloud IAM, Secret Manager access, networking and platform scheduling remain unverified until deployment and must not be reported as tested by the offline suite.

## 2. Existing components to reuse

| Existing component | Reuse | Required adaptation |
|---|---|---|
| [TradingCore](../../hummingbot/core/trading_core.py), HummingbotApplication | Clock, connector lifecycle, strategy lifecycle and performance calculations | PostgreSQL bootstrap and cloud lifecycle integration |
| [Runner](../../hummingbot/client/runner.py), [CLI engine](../../hummingbot/cli/engine.py) | Existing headless bootstrap and engine loop | Early signal handling, health endpoints, environment credential loading, PostgreSQL runtime state and console logging |
| [DBOtherMode](../../hummingbot/client/config/client_config_map.py), [SQLConnectionManager](../../hummingbot/model/sql_connection_manager.py) | SQLAlchemy engine/session API used by the recorder | Environment-based connection bootstrap; remove SQLite paths, automatic ORM DDL and the file migrator from the PostgreSQL path |
| [ORM models](../../hummingbot/model), [MarketsRecorder](../../hummingbot/connector/markets_recorder.py) | Python model classes, serializers and most existing read/write behavior | Canonical snake_case SQL mappings/DDL, PostgreSQL-compatible identities/types, idempotent writes and out-of-order event handling |
| ClientConfigAdapter, Pydantic classes and YAML helpers | Existing defaults, validation, serialization and configuration format | Avoid unnecessary write-back to read-only deployed YAML; keep secrets out of ordinary config serialization |
| [Security](../../hummingbot/client/config/security.py), [config_crypt](../../hummingbot/client/config/config_crypt.py) | Connector field metadata, validation, in-memory configuration and existing legacy cryptography | Add a small environment credential path that bypasses encrypted-file login when selected |
| [CLI commands](../../hummingbot/cli/commands) | Command names, validation, output and exit codes where semantics still apply | Replace PID/file control and SQLite history; distinguish local YAML editing from immutable cloud deployment config |
| [StrategyV2Base](../../hummingbot/strategy/strategy_v2_base.py) | Controller YAML loading, validation, hot reload and executor orchestration | Deploy compatible YAML assets; keep any generated wrapper reproducible across restarts |
| Existing tests, fixtures and connector mocks | Regression coverage | Add real PostgreSQL, bootstrap and lifecycle tests |

Review rule: every new module, table or dependency must explain the gap it fills and why extending existing code is insufficient. Prefer small adapters over a general-purpose storage or configuration framework. SQL naming normalization is explicitly in scope; retain Python model classes and business behavior while updating their database mappings.

## 3. SQL conventions from life-wallet-backend

The following reference files were inspected directly:

| Reference file | Convention to reuse |
|---|---|
| `internal/adapters/postgres/migrate.go` | Discover `.sql` files, sort their names, execute sequentially and stop on the first error |
| `internal/adapters/postgres/scripts/01_create_wallets_table.sql` | `CREATE TABLE IF NOT EXISTS`, named indexes, a small timestamp helper and guarded trigger creation |
| `internal/adapters/postgres/scripts/08_add_wallet_operation_queue_indexes.sql` | Small indexes justified by actual queries; partial indexes when useful |
| `internal/adapters/postgres/scripts/15_add_wallet_operation_status_lease_acquired_at.sql` | `ADD COLUMN IF NOT EXISTS`; evolve schema through additional scripts |
| `internal/adapters/postgres/scripts/27_add_sponsorship_usage_event_identity_constraints.sql` | Short `DO $$` blocks that check for a constraint before adding it |
| `internal/adapters/postgres/migrate_test.go` | Replay migrations against real PostgreSQL and inspect the resulting catalog |
| `cmd/app/dependencies.go` | Run enabled automatic migrations during dependency initialization |
| `requirements/coding_convention.md`, section 3.7 | Ordered, forward-only scripts; historical data changes belong in `manual_scripts/` |

Reuse these conventions, not the wallet schema or Go/GORM implementation. The reference runner does not itself provide migration locking or per-script transactions; those additions address concurrent Cloud Run starts.

### 3.1. Proposed layout

```text
hummingbot/model/db_migration/
  ...                         # existing file migrator: legacy import only if needed
  postgres/
    __init__.py
    runner.py                 # small runner using the existing connection layer
    scripts/
      001_create_core_tables.sql
      002_create_strategy_tables.sql
      003_create_auxiliary_tables.sql
      004_create_bot_state.sql
      005_add_runtime_indexes.sql
    manual_scripts/           # large imports/backfills; never executed automatically
```

This is a proposed layout. Split scripts further when useful for review and use consistent numeric padding. Alembic or another migration framework is not required for this scope. Configuration and credential tables are intentionally unnecessary because YAML and environment variables remain their sources.

### 3.2. Required rules

- [x] SQL scripts define the schema; ORM mappings must match them. Do not let the ORM silently modify schema after migration or when automatic migration is disabled.
- [x] Use lowercase snake_case for every application-owned table, column, index, constraint, sequence, trigger and function, including objects derived from existing models. Target identifiers must not require case-sensitive quoting; quoted legacy names are allowed only in compatibility/import/cutover code.
- [x] Apply one naming convention across the schema: indexes `idx_<table>_<columns>`, primary keys `pk_<table>`, unique constraints/indexes `uq_<table>_<columns>`, foreign keys `fk_<table>_<columns>`, checks `chk_<table>_<rule>`, sequences `seq_<table>_<column>` and triggers `trg_<table>_<action>`. Use short helper names such as `bot_set_updated_at`. Include meaningful suffixes when predicates or definitions differ; keep names within PostgreSQL's identifier length limit with documented deterministic abbreviations rather than relying on truncation.
- [x] Use a dedicated database and the `public` schema. Qualify DDL and catalog checks by schema rather than relying on an arbitrary session `search_path`.
- [x] Use `CREATE TABLE/INDEX IF NOT EXISTS` and `ADD COLUMN IF NOT EXISTS`. Guard constraints and triggers by schema, table and object name.
- [x] Keep helper functions limited to small tasks such as timestamps. Trading logic, retries, migration coordination and order submission authority remain in Python.
- [x] Use `CREATE OR REPLACE FUNCTION` only for helpers whose signature and body remain stable across replay. If semantics must change, add a versioned helper and a compatible binding migration so an older script cannot restore outdated behavior.
- [x] Do not edit deployed scripts. Add the next script; automatic changes must be additive and compatible with the running revision.
- [x] Startup scripts must not delete/reset business data, overwrite configuration, backfill entire tables or call exchange APIs.
- [x] Do not use `CREATE INDEX CONCURRENTLY` inside startup transactions. Large indexes/backfills use a separate maintenance path with completion checks.
- [x] Validate types, nullability, uniqueness, index columns/predicates/validity and function/trigger bindings. `IF NOT EXISTS` alone does not prove that an existing object has the intended definition. [PostgreSQL CREATE INDEX](https://www.postgresql.org/docs/current/sql-createindex.html)

A migration history table is not mandatory initially: when migration is enabled, replay all scripts as the reference runner does. Keep deployed SQL immutable, log script names/checksums/image revision and test replay. Add a ledger only if concrete audit or version-management requirements justify it.

### 3.3. Canonical schema names and legacy cutover

Use plural names for collections of records. Collective names such as `metadata` and `market_data`, and the existing proposed runtime name `bot_state`, remain explicit exceptions. Python class names retain their current casing; this standard applies to database identifiers.

| Existing SQL table | Canonical PostgreSQL table |
|---|---|
| `Order` | `orders` |
| `OrderStatus` | `order_statuses` |
| `TradeFill` | `trade_fills` |
| `MarketState` | `market_states` |
| `Executors` | `executors` |
| `Controllers` | `controllers` |
| `Position` | `positions` |
| `FundingPayment` | `funding_payments` |
| `InventoryCost` | `inventory_costs` |
| `MarketData` | `market_data` |
| `RangePositionUpdate` | `range_position_updates` |
| `RangePositionCollectedFees` | `range_position_collected_fees` |
| `Metadata` | `metadata` |
| New runtime table | `bot_state` |

- [x] N1: Use this map in DDL, SQLAlchemy `__tablename__`, foreign-key targets, index/constraint declarations, raw SQL, fixtures, schema checks and import mappings. For example, change `ForeignKey("Order.id")` to `ForeignKey("orders.id")`; reuse the `Order` Python class and relationships.
- [x] N2: Inventory all column names and dependent objects. Keep already-canonical columns and their meanings; normalize names that do not meet the convention. Match SQLAlchemy naming conventions to explicit SQL DDL and explicitly name any generated sequences or constraints as needed.
- [x] N3: A fresh PostgreSQL database creates canonical names directly. Before automatic DDL, detect legacy mixed-case application tables and fail with cutover instructions rather than silently creating empty snake_case tables alongside populated legacy tables. With automatic migration disabled, schema validation likewise rejects the legacy layout.
- [x] N4: If an existing PostgreSQL database needs conversion, add a separately invoked, idempotent script under `manual_scripts/` and use the same migration-lock/transaction helpers. Stop all legacy writers, including background Cloud Run revisions, and verify quiescence before running it. Table renaming is a one-time breaking cutover, not an additive rolling-startup migration.
- [x] N5: Guard each legacy rename: old-only means rename; canonical-only means validate and no-op; both names present means fail for explicit reconciliation; neither present means leave creation to the canonical schema scripts. Apply equivalent guards to renamed dependent objects. Run the cutover transactionally so failures do not leave partial renames.
- [x] N6: Preserve row identities, values, ownership/grants, foreign-key relationships, defaults, sequence ownership/current values, indexes and trigger/function bindings. Verify that inserts and joins still work after conversion. Avoid copying all rows merely to rename tables.
- [x] N7: **Not applicable to this delivery: no SQLite source/import was requested.** Do not introduce an unnecessary importer. A future explicitly requested SQLite import must use legacy-to-canonical mappings, read-only sources and duplicate-safe replay. The requested PostgreSQL legacy-name cutover is implemented and tested separately.
- [x] N8: After normalization, deploy only images compatible with canonical names. Do not restart an original mixed-case ORM image or automatically reverse names during rollback. Test rollback to a compatible ported revision; recovery to the original application requires a separate planned procedure.

## 4. Storage, YAML and environment configuration

### 4.1. Trading data: retain existing ORM models

The names below refer to existing Python model classes. Their SQL tables use the canonical mapping in section 3.3.

| Existing Python models | Port requirements |
|---|---|
| `Order`, `OrderStatus`, `TradeFill` | Preserve identity, history and relationships; tolerate duplicate and out-of-order events |
| `MarketState` | Preserve `tracking_states` and restore behavior; enforce uniqueness for configuration plus connector |
| `Executors`, `Controllers`, `Position` | Reuse executor/config snapshots, serializers, orchestration and PnL calculations |
| `FundingPayment`, `InventoryCost` | Preserve funding/cost data; verify precision and event identity across markets sharing timestamps |
| `MarketData` | Keep current optional collection behavior; recording every order book tick is not a prerequisite |
| `RangePositionUpdate`, `RangePositionCollectedFees` | Provide equivalent schema; test Gateway/DEX behavior separately before enabling those connectors |
| `Metadata` | Retain useful metadata; do not use `local_db_version` to invoke the file-copy migrator |

- [x] Inventory all registered/imported models; `get_declarative_base()` currently does not explicitly import every model in the directory.
- [x] Resolve demonstrated precision loss: the BTC Demo showed scaled-six quantities being truncated. Reuse `SqliteDecimal` with exact, unscaled PostgreSQL `NUMERIC`, retain upstream SQLite behavior, and explicitly convert the 15 former BIGINT fields once in `013_preserve_decimal_precision.sql`. Derived fee/PnL values have no fixed database scale; historic lost digits cannot be recovered by migration.
- [x] Preserve existing engine timestamp units; use UTC for new timestamps. Do not accidentally convert epoch milliseconds to seconds.
- [x] Test JSON/Decimal/Enum serialization. Use JSONB for new structured state; change existing JSON columns only for specific query/index needs.
- [x] Preserve `config_file_path` as the existing configuration identifier/path. Use stable relative names across revisions and exact query filters so histories from different configurations do not mix.
- [x] Remove the loop that drops all foreign keys in SQLConnectionManager. Handle events arriving before their order through a deliberate placeholder/upsert or a narrowly unenforced relationship, with behavioral tests.
- [x] Prefer existing `Order` and `MarketState` records for pending/unknown state and recovery. Add missing columns only when necessary; do not introduce an event store, outbox or order journal by default.
- [x] Funding follow-up implementation/local verification: reuse `funding_payments`, bot-state checkpoints and the current owner/service; add the narrow `funding_bills` companion with exact NUMERIC amounts, account/environment/bill identity and transactional checkpoints through `014_create_funding_bills.sql`. Legacy deduplication, stopped/closed-position catch-up, API coverage, full image E2E and the bounded local load probe pass. No extra service/container is required. [LIFE/OKX P7](LIFE_OKX_IMPLEMENTATION_PLAN_TDD.md#p7--durable-funding-observation-and-restart-backfill-tdd), [evidence](../../reports/implementation/funding-backfill-evidence.json).
- [x] Funding Demo acceptance: the 2026-10-26 07:00 Vietnam settlement matched exact original amount/time with DB/daily totals, and cold restart verified deduplication without new orders/fills. [Actual Demo evidence](../../reports/demo/okx-demo-funding.json).

### 4.2. Durable bot control state

Only one additional application table is proposed initially:

| Table | Contents | Existing gap |
|---|---|---|
| `bot_state` | One row per `bot_id`: selected YAML config, desired state, control version/ack, run UUID, owner generation, heartbeat and status/error snapshot | Replaces `loaded.json`, `meta.json`, `status.json` and PID-based control across instances |

- [x] Use version checks/compare-and-set for desired-state changes and acknowledgments so retrying an older command cannot overwrite a newer command.
- [x] Store a configuration name and optionally a deployment/configuration digest for diagnostics, not the full YAML configuration as a new authoritative DB copy.
- [x] Keep this table focused on current operational state. Trading history remains in existing tables; comprehensive run/event history is a separate requirement.

### 4.3. Preserve ordinary YAML configuration

The original application uses YAML for more than strategies: client settings, controller/script settings and encrypted connector credentials also have YAML paths. For this port, retain ordinary YAML settings while moving credential values to the environment.

- [x] Keep the existing client, strategy, controller, script and fee-override YAML formats and validation.
- [x] Keep existing local CLI configuration editing where applicable. PostgreSQL does not become a configuration editor or replace the YAML loaders.
- [x] Package non-secret YAML in the image or provide versioned configuration mounts on Cloud Run. The container recipe must explicitly include/provide them; the existing image does not automatically include local configuration.
- [x] Keep exchange API credentials and database passwords out of those YAML assets. Runtime database settings come from the environment and must not fall back to the default YAML SQLite mode.
- [x] Preserve filenames and referenced controller/script paths across deployments. Updating deployed YAML creates a new configuration/image revision; local container edits are not durable configuration changes.
- [x] Avoid unconditional writes to read-only deployed configuration, including `load_client_config_map_from_file()` writing back immediately. Add a narrow read-only startup option rather than rewriting all configuration loading.
- [x] If legacy helpers require generated files, permit a disposable working copy or generated controller wrapper in a designated temporary directory. Rebuild it from deployed inputs on every start; it must not contain the only copy of runtime state or secrets serialized from the environment.
- [x] Cloud CLI config writes must either produce an explicit deployment artifact or report that the deployed configuration is read-only. Do not report success for edits that will disappear on restart. Existing local hot reload may remain; live editing of deployed YAML is not required for this port.

### 4.4. Load optional local `.env` before bootstrap

The earlier plan mentioned environment variables but did not specify `.env` loading. No explicit dotenv bootstrap was found in the reviewed CLI/client/build paths. Add one small shared loader; reuse an existing dependency if available, otherwise add `python-dotenv` rather than a custom parser.

Precedence for bootstrap and credential values: **existing process environment > selected local `.env` > code defaults**. Ordinary strategy/client YAML settings remain a separate configuration domain and are not broadly overridden by arbitrary environment variables. `load_dotenv(..., override=False)` supports preserving existing environment values. [python-dotenv documentation](https://bbc2.github.io/python-dotenv/)

| Variable | Purpose and default |
|---|---|
| `STORAGE_BACKEND` | `postgres` or `file`; explicit selection wins locally. Omitted preserves URL/Cloud Run detection, otherwise defaults to local file mode; service/Cloud Run requires PostgreSQL. |
| `DATABASE_URL` | PostgreSQL connection URL; required for DB-dependent commands/service startup; use an explicitly supported sync driver |
| `DB_POOL_SIZE` | Per-engine connection ceiling, default `5`, validated integer `5`–`50`; no overflow; restart required. Admin DB workers remain capped at two. Budget all instances/revisions and migration engines; see [runtime guide](../postgres-runtime.md). |
| `ENABLE_AUTO_MIGRATION` | `true` or `false`; current code default `false`; deployment examples explicitly use `true`; controls automatic service-start migrations only |
| `BOT_ID` | Stable identity for the logical bot; default `hummingbot` for the initial single-bot deployment |
| `PORT` | Cloud Run supplies it; local service default `8080` |
| `ENV_FILE` | Optional explicit local dotenv path; read before loading a file. Preferred over compatibility alias `HBOT_ENV_FILE`; omitted uses repository-root `.env`. |
| Connector credential variables | Values mapped to existing connector fields, as specified in section 4.5 |
| `HBOT_PASSWORD` | Only needed for an explicitly selected legacy encrypted-keystore flow; not required for environment credentials |

Illustrative `.env.example` content; these values are placeholders, not actual credentials:

```dotenv
STORAGE_BACKEND=postgres
DATABASE_URL=postgresql+psycopg2://hummingbot:replace_me@127.0.0.1:5432/hummingbot
DB_POOL_SIZE=5
ENABLE_AUTO_MIGRATION=true
BOT_ID=hummingbot
PORT=8080
HBOT_OKX_API_KEY=replace_me
HBOT_OKX_SECRET_KEY=replace_me
HBOT_OKX_PASSPHRASE=replace_me
HBOT_OKX_REGISTRATION_SUB_DOMAIN=www
```

The example assumes the selected `psycopg2` driver is installed. Use the validated driver/connection settings for the actual deployment.

- [x] E1: Load local `.env` once before constructing DB settings, connector configuration or client bootstrap. Share this path between the service, DB-dependent CLI commands and migration command; `--help`/`--version` must still work without secrets or a DB.
- [x] E2: Locally use `ENV_FILE`, then compatibility alias `HBOT_ENV_FILE`, otherwise the repository-root `.env`. Resolve paths deterministically without walking arbitrary parent directories. A missing default `.env` is allowed; a missing/empty explicitly requested file is an error. Non-secret defaults are centralized; new keys omit a `LIFE_` prefix.
- [x] E3: Preserve already-set process values, including intentionally empty values; validate required empty values as errors instead of silently replacing them from `.env`. Do not shell-source/evaluate the file. Disable variable interpolation for literal secret values and test dollar signs, hashes, quotes and URL escaping.
- [x] E4: Parse `ENABLE_AUTO_MIGRATION` explicitly as a boolean: accept trimmed, case-insensitive `true`/`false`; reject other values. Never use `bool(string)`, which makes `"false"` truthy. Apply the default only when the variable is absent.
- [x] E5: On Cloud Run Service, detect `K_SERVICE` from the process environment before dotenv loading and skip dotenv discovery/loading. Require direct environment/Secret Manager injection; reject an explicitly requested local dotenv path in that mode.
- [x] E6: Add a documented `.env.example` with placeholders. Keep actual `.env`/secret variants out of Git and the Docker build context; review `.gitignore` and `.dockerignore`, allowing only safe examples where useful.
- [x] E7: Validate only settings needed by the selected command and configured connectors. Never print full connection URLs, environment dumps, API keys or passphrases in logs/errors/status.
- [x] E8: Document local `.env`, local Docker `--env-file`, and Cloud Run environment/Secret Manager setup. Use explicit secret versions for a deployment; credential rotation takes effect through a controlled restart/new revision.

### 4.5. Connector credentials from environment variables

Reuse connector field declarations and Pydantic validation, including `SecretStr`, `is_connect_key` and `is_secure`. Introduce a thin input adapter, not a second connector or encryption implementation.

A proposed deterministic naming rule is `HBOT_` plus the uppercase existing field name. For OKX spot, this maps `HBOT_OKX_API_KEY`, `HBOT_OKX_SECRET_KEY` and `HBOT_OKX_PASSPHRASE` to the existing fields. OKX perpetual has separate fields, so use `HBOT_OKX_PERPETUAL_API_KEY`, `HBOT_OKX_PERPETUAL_SECRET_KEY` and `HBOT_OKX_PERPETUAL_PASSPHRASE`. Non-secret connection fields such as the OKX registration subdomain can use the same mapping.

- [x] C1: Build the existing connector config objects from environment values, validate them, and populate the same in-memory settings consumed by connector factories/UserBalances.
- [x] C2: Validate credentials for the selected connectors; missing or partial credentials fail clearly. Do not silently fall back to encrypted YAML or combine different credential sources for one connector.
- [x] C3: The cloud environment path bypasses `Security.login()` file/password verification and does not require `HBOT_PASSWORD` or `conf/.password_verification`. Preserve any readiness signals/in-memory initialization that downstream code expects.
- [x] C4: Prevent calls that encrypt/decrypt or serialize environment secrets back to YAML, PostgreSQL or generated wrappers. Keep redaction and `SecretStr` behavior; allow plaintext only where connector authentication needs it in memory.
- [x] C5: Reuse connection checks in `hbot connect`/`balance` for environment-supplied credentials. Do not automatically edit `.env` or publish secrets from a service process. Document configuring the variables and redeploying when credentials change.
- [x] C6: Keep existing encrypted YAML support only as an explicit legacy path if required. A one-time import may reuse `ETHKeyFileSecretManger` and password verification to decrypt existing values for operator-managed migration to secrets; it must not become a cloud startup dependency.

The original encrypted-file path derives keys from a keystore password, encrypts individual secret fields and stores ciphertext in connector YAML. With environment credentials, `.env` contains sensitive values directly on the local machine and Secret Manager supplies them to the cloud process. This replaces the storage/input path; it does not add application-level encryption to environment variables.

## 5. Startup migration policy

Target startup sequence:

```text
Process environment + optional local .env
  -> validate ENABLE_AUTO_MIGRATION and bootstrap settings
  -> register SIGTERM; start health listener
  -> connect to PostgreSQL
  -> if ENABLE_AUTO_MIGRATION=true:
       acquire database migration lock
       execute all SQL scripts in order
       release migration lock
     otherwise:
       skip all migration DDL
  -> validate schema in both modes
  -> load ordinary YAML configuration and environment credentials
  -> pass startup probe; process may remain stopped or standby
  -> acquire bot ownership
  -> restore/reconcile orders, fills and positions with existing connectors
  -> run the strategy if desired state permits
```

- [x] P1.1: Bootstrap environment settings before ClientConfigMap/YAML defaults can determine the DB. Inject the validated PostgreSQL connection into the existing connection manager; never silently use SQLite.
- [x] P1.2: Reuse SQLConnectionManager with a synchronous PostgreSQL driver compatible with the supported SQLAlchemy version; declare it in the relevant build/test environments. Do not pair an async-only driver with sync `create_engine()`.
- [x] P1.3: Parse `DATABASE_URL` correctly, including escaped passwords, SSL and timeouts. Use structured URL construction if separate connection fields are needed; do not concatenate unescaped credentials.
- [x] P1.4: Add the small PostgreSQL runner alongside the SQLite migrator; package `.sql` resources and verify their presence in the built image. Resolve resources independently of the working directory.
- [x] P1.5: Evaluate `ENABLE_AUTO_MIGRATION` on every service start. When true, run migrations before trading; when false, perform schema validation only. An unconfigured bot may finish valid bootstrap and await setup without starting a default strategy.
- [x] P1.6: For enabled migrations, use `pg_try_advisory_lock` with a stable application/database key and one dedicated physical connection for the whole batch. Run DDL through that same connection.
- [x] P1.7: Hold the session lock across script transactions; unlock/close in `finally`. Avoid transaction-pooling connections for session locks; connection loss fails bootstrap. PostgreSQL releases session advisory locks when the session ends. [PostgreSQL locking](https://www.postgresql.org/docs/current/explicit-locking.html)
- [x] P1.8: Use one transaction per SQL file: roll back a failing file and replay already-committed files safely on the next enabled start. The runner owns transaction boundaries; scripts do not nest `BEGIN/COMMIT`.
- [x] P1.9: Execute complete scripts through a driver path that supports multiple statements. Do not use `split(';')` because function bodies and `DO $$` blocks contain semicolons. Test with actual scripts.
- [x] P1.10: Bound connection/retry/lock/statement waits and total bootstrap time. Initial proposed budgets: connect 5s, migration lock wait 60s, DDL lock timeout 5s, statement timeout 30s, overall bootstrap 180s; measure before finalizing.
- [x] P1.11: On migration failure, missing privileges, deadline expiry or incompatible schema, log a redacted error, exit nonzero and keep startup unsuccessful. No order-producing work may have started.
- [x] P1.12: Replay must preserve business data. Allow revisions to overlap only when both use canonical names and the schema changes are tested as additive. Legacy name conversion follows the separate stopped-writer cutover in section 3.3; application rollback does not execute down migrations or restore a database file.
- [x] P1.13: With auto-migration enabled, grant the application's migration connection the necessary DDL rights only within its database/schema, without superuser privileges. With it disabled, runtime may use a DML-only role after a separately authorized migration step.
- [x] P1.14: `make migrate` is an explicit operator command using the same runner and locking; it executes migrations regardless of the automatic-start flag. Document this distinction, and keep schema validation free of implicit `create_all()` or other DDL.

Completion criteria: an empty DB boots with migration enabled; repeated migration runs preserve data; simultaneous starts serialize; interrupted migrations recover. With the flag disabled, a compatible DB boots without DDL and an absent/incompatible schema fails before trading.

## 6. Implementation phases

### P0 — Inventory and scope

- [x] P0.1: Inventory SQLite, YAML, credential files, JSON snapshots, PID files and log file call sites across `connect/create/import/config/start/stop/status/history/balance/doctor/logs/deploy`. Classify each as retained configuration, environment secret input or state to port.
- [x] P0.2: Review bootstrap_application, config helpers, HummingbotApplication, V1 loaders, V2 controller loading/hot reload and generated wrappers. Identify the smallest changes needed for deployed YAML and environment credentials.
- [x] P0.3: Select first-release connectors/strategies; use OKX spot/perpetual smoke cases with appropriate fixtures/configuration. Review Gateway/certificate requirements before enabling Gateway-dependent connectors.
- [x] P0.4: Confirm one bot/database/account and stable config paths. Reject unsupported arrangements where multiple bots share an account through separate databases, since one database lock does not protect another database.
- [x] P0.5: Record baseline behavior with existing tests/fixtures, including strategy decisions, order events and accounting expectations for selected connectors. Preserve CLI output and validation where semantics remain unchanged; use this baseline to verify the shared engine after the port.
- [x] P0.6: Justify each new module/table and inventory the naming changes in section 3.3. Schema naming normalization is part of this port; broad architecture refactoring or strategy changes are not prerequisites.

P0 evidence (2026-10-24, checkout `c1c2d9fa0`): existing engine, runner, recorder, OKX spot and OKX perpetual suites passed: **160 tests, 6 warnings, 7.95s**. Initial scope is one bot/dedicated DB/account, PostgreSQL 14, OKX spot/perpetual, existing V1 pure market making and V2 script/controller loading. Gateway/DEX and unaudited custom file-writing strategies remain outside the verified cloud scope.

Inventory: `cli/bot.py` holds JSON/PID state; `cli/data.py` assumes SQLite; start/stop/status/deploy use local processes and signals; recorder retains a legacy CSV export helper; config loaders/bootstrap write YAML/templates; connector login reads encrypted YAML/password verification; file logging and generated wrappers need a cloud-safe path. Ordinary YAML, explicit operator exports and backtesting output remain valid file uses. Reuse the existing engine/ORM/connector APIs and add only the environment adapter, SQL runner/verifier, operational state/ownership, service host and small offline test harness. Progress and verification evidence are maintained directly in this plan.

### P1 — Connection and configurable automatic migration

- [x] Implement and verify the P1.1–P1.14 PostgreSQL/bootstrap primitives in section 5. Environment primitives/documentation are implemented; selected-connector validation (E7) and final DB-dependent CLI wiring (E1) are completed in their P3/P4 phases.
- [x] Complete canonical naming and explicit PostgreSQL cutover (N1–N6/N8), DDL for existing ORM entities, minimal `bot_state` and query-driven indexes. N7 is conditional on delivering an optional SQLite importer; no SQLite source/import requirement has been supplied.
- [x] Remove ORM `create_all()`, blanket foreign-key removal and file-copy migration from the PostgreSQL startup path.
- [x] Add Make targets for migration and PostgreSQL integration tests; reuse the exact production runner.

P1 evidence (2026-10-24): `make test-postgres` passed **21 real PostgreSQL 14 tests** (32.75s), including populated replay 100 times, two concurrent starts, role-limited read-only schema validation, drift rejection, whole-file rollback/DO blocks, statement deadline, backend termination and explicit migration with the auto flag disabled. Explicit legacy PostgreSQL cutover is transactional across normalization plus canonical scripts and validation; data/sequence continuity, replay, conflict rejection and rollback are tested. Existing model/CLI/runner/recorder/OKX suites plus environment tests passed **527 tests**; existing PMM/V2 baseline passed **96 tests**. Remaining warnings originate in existing Pydantic/async test code.

P1 delivery: five packaged canonical SQL files, explicit maintenance SQL, shared environment settings/connection factory, a session-locking migration runner and catalog validator, `make migrate`, disposable `make test-postgres`, required PostgreSQL CI step and [operations instructions](../postgres-runtime.md). The PG connection manager never infers a SQLite path or runs ORM/file migrations. New timestamps retain existing units; funding/market-data keys include the market/pair to avoid same-timestamp collisions. Service lifecycle and final image packaging verification remain in P6; credential/environment CLI integration remains in P3/P4. No cloud deployment or container E2E has been claimed.

### P2 — PostgreSQL recorder and queries

- [x] P2.1: Retain recorder session/transaction interfaces; manage engine/pool lifetime rather than creating unbounded connections per command/tick.
- [x] P2.2: Stop inferring a database from `data/<config>.sqlite`. Use one connection configuration and exact filtering by the existing configuration identity.
- [x] P2.3: Port every ORM model in section 4.1 to the canonical mappings in section 3.3. Update foreign-key strings, relationships, raw SQL, query/import fixtures and schema checks; test identity/uniqueness under replay and equal timestamps across markets without rewriting the model's business logic.
- [x] P2.4: Preserve atomic fill/status/market-state writes. Update any in-memory “persisted fill” cache only after commit, so rollback cannot cause a replayed fill to be lost.
- [x] P2.5: Reuse recorder tests and connector mocks for out-of-order create/fill/cancel events, duplicate WebSocket events, reconnection, partial fills and unknown order states.
- [x] P2.6: Measure remote DB latency on the trading loop. Retain sync SQLAlchemy if it meets the budget. If offloading is necessary, use immutable snapshots, bounded work and separate sessions per worker; propagate commit/failure. Enqueueing is not durable persistence.
- [x] P2.7: **Persistence-failure latch and final connector send-gate integration implemented/tested in P2/P5.** Prevent new risk when the DB is unavailable. Retry transactions only with bounded, idempotent behavior; never blindly retry exchange submissions. Reconcile after connectivity returns.
- [x] P2.8: Configure pool size/overflow, connect/query timeouts and pre-ping. Include deployment overlap, migration and bot-ownership connections in the connection budget.

P2 evidence (2026-10-24): PostgreSQL integration suite passed **31 tests** (34.85s); selected existing regression/strategy suites passed **623 tests** (12.43s). New tests cover real FK-preserving fill-before-create, create/fill replay, partial fills, exact fees/Decimal/timestamps, all 14 registered ORM tables, conflicting duplicate rejection, actual PostgreSQL backend termination before commit, rollback with no cache acknowledgment, market-state restore and same-timestamp funding across markets/pairs. Local synchronous recorder p95 was **15.25ms over 25 fill transactions**, below the initial local 100ms budget; this is not a measurement of remote Cloud SQL latency.

Release verification found an additional recorder failure path: a rolled-back fill could be followed by a later event committing an advanced tracking snapshot. PostgreSQL managers now reject every subsequent ORM flush/bulk write after rollback, preserving the last committed snapshot for fresh-process exchange replay. The expanded real PostgreSQL suite passed **61 tests** (122.38s), including writes refused after rollback and populated previous-canonical schema upgrade. The rebuilt deployment image also passed transaction interruption/restart: the missing exchange fill is recovered once, with exact totals of 0.14 base and 0.014 quote fees, while prior uncertainty retains HALT.

P2 delivery: additive `006_add_order_recovery.sql` marks provisional orders explicitly; duplicate create cannot erase later fill/status state; fill/status/tracking-state writes remain one transaction. Connector caches update after commit. Decimal overflow/non-finite values fail explicitly, and inventory creation preserves Decimal precision. PG runtime queries use a 2s statement / 1s lock / 2s pool-checkout budget; migrations retain their separate DDL budgets. A rollback latches persistence unhealthy and a later successful write does not reopen it. The final fail-closed submission guard/reconciliation is a P5 integration dependency and stays unchecked above. No automatic CSV export call was found during implementation; the legacy helper now explicitly refuses PG runtime file output.

### P3 — Retain YAML and supply credentials through the environment

- [x] P3.1: Complete section 4.3 and C1–C6 in section 4.5. Keep ClientConfigAdapter, existing config classes and YAML formats rather than adding configuration repositories.
- [x] P3.2: Retain ordinary client, fee, V1/V2 and controller YAML. Separate secret loading and DB connection bootstrap from those YAML defaults.
- [x] P3.3: Reuse TradingCore's existing strategy startup/configuration paths; add only the adaptation needed for read-only deployed configuration and environment-backed connector settings.
- [x] P3.4: Preserve V2 controller loading; make generated wrappers reproducible in memory or a temporary working directory. Do not persist environment credentials in generated files.
- [x] P3.5: Initialize validated connector settings in memory without encrypted-file login in the cloud path. Test downstream users of Security state and readiness events so they do not wait indefinitely.
- [x] P3.6: Retain local `create/config/import` YAML behavior. Adapt `connect` and `balance` to validate environment credentials; keep secrets out of argv and normal CLI output.
- [x] P3.7: Startup must not create a new keystore/password file or overwrite configuration. Missing required environment credentials fail clearly; an explicitly unconfigured/stopped bot must not start trading.
- [x] P3.8: Preserve local hot-update rules from existing field metadata. On cloud, apply config/secret changes through a versioned deployment and expose the active config/revision in status; live cloud config editing is not required.
- [x] P3.9: **Deferred optional scope, not needed for the agreed environment-credential/new PostgreSQL deployment.** No SQLite or encrypted credential import is delivered or required at startup. Any future requested import must be explicit, read sources without mutation and verify duplicate-safe counts, Decimal values and timestamps.

P3 evidence (2026-10-24): selected regression suites plus environment credential/configuration tests passed **645 tests** (16.72s). OKX spot/perpetual use their existing Pydantic/SecretStr field declarations. Tests cover missing/partial credentials, secret-safe validation errors, source isolation, no keystore login/serialization, read-only client/SSL/fee YAML loading, shared application bootstrap, in-memory controller wrappers, CLI connect/balance reuse and help/version without DB/secrets.

P3 delivery: environment credentials initialize the existing Security readiness state and are validated when the existing connector manager selects a connector; no encrypted YAML fallback occurs. `connect` checks injected credentials instead of storing them; `balance` supports selected/all environment-configured connectors. Cloud bootstrap uses read-only YAML and stdout logging without first opening file handlers. Local legacy encrypted input remains available through the existing explicit legacy path. Controller wrappers reuse the existing V2 runner/configuration using a dictionary rather than a generated deployed file. Deployment/config revision reporting is integrated in P4/P6; optional SQLite/credential export/import is not requested or delivered.

### P4 — CLI and PostgreSQL runtime control

- [x] P4.1: Replace persistence in `cli/bot.py` with `bot_state`, retaining helper interfaces where useful. Do not use local PID namespaces/psutil to determine whether the cloud bot is running.
- [x] P4.2: `status` reads heartbeat, snapshot and age. An expired heartbeat means stale/unknown, not running merely because a row exists. Remove SIGUSR1/file snapshot coordination.
- [x] P4.3: **Versioned PostgreSQL CLI/backend and the engine observer/ack loop verified in P4/P6.** `start/stop/deploy` update desired state, selected YAML config and control version; the engine observes/acknowledges DB state. User stop survives service restart. A running service may remain stopped while serving health checks.
- [x] P4.4: **Durable user stop and platform signal behavior verified in P4/P6.** Distinguish platform shutdown from user stop. Cloud Run SIGTERM must stop sends and perform cleanup without permanently changing desired state to stopped.
- [x] P4.5: Preserve `--replace`, timeouts, JSON output and exit codes where applicable. Redefine or reject cloud `--force` because it cannot SIGKILL a PID on another instance; do not falsely report completion.
- [x] P4.6: `history` reuses the connection manager and PerformanceMetrics, removing hard-coded `sqlite:///` in `cli/data.py`; cover both running and stopped bots.
- [x] P4.7: `doctor` checks connection/schema/configuration/credentials/heartbeat. `logs` points to Cloud Logging; do not build a PostgreSQL log store merely to emulate file tailing.
- [x] P4.8: Operator commands use the same DB bootstrap without shared runtime directories or `docker exec`. Starting a cloud configuration requires it to be present in the deployed config set; local edits must not be mistaken for a deployed update. Mutating control commands do not independently submit exchange orders.

P4 evidence (2026-10-24): full PostgreSQL suite passed **38 tests** (49.75s); the expanded control suite subsequently passed **8 tests** including an additional undeployed-config/read-only-YAML case. Selected regression suites passed **645 tests** (17.05s). Existing performance tests passed **11 tests** separately; legacy synchronous performance tests need a current event loop and are run separately from CLI `asyncio.run()` tests. CLI/runner regression passed **380 tests** after the new option handling was corrected.

P4 delivery: a small `BotStateStore` implements atomic control-version CAS and owner/version-fenced publishing against the existing single `bot_state` table. PostgreSQL CLI paths bypass runtime JSON/PID directories; separate real CLI processes exercise start/stop/status/history with no shared writable layer. Heartbeat age is computed by PostgreSQL; stale snapshots report unknown/not live. `--expected-version` supports safe automation retries; `--timeout 0` returns **requested**, never a false running/stopped acknowledgment. `--force` cannot signal a remote PID. Startup selection checks the service-advertised config set when present, and cloud YAML mutation/update commands are rejected. History uses the existing accounting formulas with recorded quote fees and last-fill valuation explicitly labeled, without live price requests. Doctor validates PG/schema/heartbeat and environment fields; logs directs operators to stdout/Cloud Logging. The service's actual observer, owner publication and platform shutdown remain P5/P6 integration dependencies, not a completed cloud runtime.

### P5 — Single sender and restart recovery

- [x] P5.1: Keep the single-bot model and add a session advisory lock before starting a strategy. Use a separate namespace/key from the migration lock and a dedicated connection.
- [x] P5.2: Store owner UUID/generation in `bot_state`; condition status/ack writes on ownership to prevent stale processes overwriting a newer owner. `max-instances=1` does not replace ownership control.
- [x] P5.3: **Healthy standby startup/handover verified in P5/P6.** Non-owner instances stay in standby without order-producing tasks. Startup may succeed after valid schema/configuration bootstrap so deployment does not deadlock while the old revision owns the bot.
- [x] P5.4: **Loss latch, final send/recorder fencing and automatic host teardown verified in P5/P6.** Lost lock connection or uncertain ownership closes the send gate and stops the strategy. Reconnecting requires reacquisition and reconciliation before resuming.
- [x] P5.5: Verify ownership at the final submission boundary, including retries, executor resubmission and after throttler waits. Reuse existing connector send paths and audit bypasses.
- [x] P5.6: Reuse MarketState restore and connector order/trade reconciliation. Where required, persist pending identity before an external side effect using existing Order records or small field additions rather than a new journal framework.
- [x] P5.7: Reconcile client order IDs, open orders and fills before resubmission, including accepted requests with lost acknowledgments. V2 spot/perpetual image cases verify persisted position holds and controller PnL/drawdown state. Unclean exits/custom executor or timer uncertainty retain durable HALT rather than silently resetting risk; a fill during V2 position-checkpoint cleanup also requires operator reconciliation.
- [x] P5.8: Database ownership/fencing protects application state; exchanges do not enforce that token. Test in-flight requests and processes paused across lock loss. A successor must reconcile uncertainty before increasing risk; a DB lock alone is not exactly-once delivery or proof of safe hot standby.
- [x] P5.9: Reuse stop/cancel logic with a total shutdown budget below the grace period. Do not release ownership while tasks can still submit. Preserve unresolved state when forced termination interrupts cleanup.

P5 evidence (local, 2026-10-24): the full disposable PostgreSQL suite passed **56 tests**; the expanded ownership subset subsequently passed **18 tests** (39 deselected). The selected CLI/config/model/recorder/OKX/engine/REST/executor regression suite passed **791 tests**. The V2 strategy suite passed **28 tests**, including preservation of restored PnL peaks and refusal to restart controllers stopped by drawdown. PostgreSQL tests terminate the actual owner backend, pause the real REST assistant at throttle/transport barriers, verify durable intent/account-mutation markers, reject stale recorder writes and duplicate physical sends, and prove that uncertain handover remains HALTed across another restart. These are integration/component tests, not container E2E.

P5 delivery: `BotOwnership` uses a dedicated physical advisory-lock session and monotonically increasing generation. SQL `007_add_recovery_state.sql` adds one JSONB field to the existing `bot_state`; pending order identity uses the existing `orders`/`market_states` records. Final authenticated OKX mutations are checked after throttle/authentication; ordinary placements have a bounded transport timeout and the documented OKX `expTime` deadline. Account-setting mutations retain uncertainty until a positive acknowledgment. Only reviewed OKX spot/perpetual connectors can trade in the PG service; unreviewed mutations/connectors fail before transport.

Recovery restores existing tracking state, reads complete paginated account open orders, fetches order status/fills, cancels known restored working orders and reads back terminal states before allowing strategy ticks. Unknown exchange orders/position mismatches stop startup. Shutdown reuses existing V2 position-hold and core cancellation/cleanup paths with an 8s default total budget; ownership is retained if tasks or uncertain effects remain. Known controller drawdown/HALT/PnL peak fields are checkpointed before cleanup mutates them. Stable controller IDs remain required by the existing YAML model.

**Recovery limit:** the source does not reconstruct arbitrary live executors/custom strategy timers after an unclean process exit. A durable `runtime_active` marker therefore forces HALT on that path even when read-only exchange reconciliation resolves an order. It never silently restarts with reset risk limits/deadlines or resubmits an uncertain identity. Automatic resume after a clean stop is limited to reviewed strategy state; full image/process/signal evidence and the hard process watchdog belong to P6/offline verification. No exactly-once exchange guarantee is claimed. [OKX request expiry and order APIs](https://www.okx.com/docs-v5/en/)

### P6 — Container and Cloud Run Service

The primary target is **Cloud Run Service**, with the bot loop running in the background and small HTTP health endpoints. A Worker Pool may reuse the engine later; deploying both resource types is not required for this port.

- [x] P6.1: Add a small service entrypoint, provisionally `hbot serve`, that invokes the existing engine in the foreground/PID 1 and registers SIGTERM early. No daemonization, TTY or idle `tail` process.
- [x] P6.2: Listen on `0.0.0.0:$PORT`. `/startup` succeeds after the selected migration policy, schema validation and config/credential bootstrap, including valid stopped/standby states. `/live` reflects event-loop health; `/ready` reports trading readiness but must not force startup to wait for ownership.
- [x] P6.3: Avoid dependency-driven liveness restart loops; report degraded state and close the send gate when dependencies fail. Do not expose a public order-submission endpoint.
- [x] P6.4: Configure instance-based billing/continuous CPU and minimum instances = 1 so the loop runs outside HTTP requests; set maximum instances = 1 for the initial deployment. [Cloud Run billing](https://docs.cloud.google.com/run/docs/configuring/billing-settings)
- [x] P6.5: Test deployment/maintenance overlap with two real local containers sharing PostgreSQL; maximum instance limits can be temporarily exceeded. This covers application ownership behavior without requiring Cloud Run staging. [Cloud Run autoscaling](https://docs.cloud.google.com/run/docs/about-instance-autoscaling)
- [x] P6.6: Start with an 8s total shutdown budget, leaving process/connection cleanup time. Existing `KILL_TIMEOUT=20s` and the V2 executor hard cap of at least 30s need parameterization. Cloud Run normally sends SIGKILL about 10s after SIGTERM. [Container contract](https://docs.cloud.google.com/run/docs/container-contract)
- [x] P6.7: Reuse Python logging with stdout/stderr and bot/config/owner/migration fields. Remove rotating file handlers and console silencing from this path.
- [x] P6.7a: Normalize foreground console output to single-line JSON with UTC time and Cloud Logging severity; provide `LOG_LEVEL`/`LOG_FORMAT`, preserve NETWORK warnings before filtering, and log operator incidents at ERROR. Cover bootstrap, redaction, rejected startup settings, Python warnings and quiet SQL logging. Native and new-image offline verification pass; Cloud Logging ingestion remains unverified. See [operations guide](../cloudrun.md#logs-and-severity), [verification](../../reports/platform/cloudrun-logging-verification.md) and [progress](../../reports/implementation/LIFE_OKX_PROGRESS.md).
- [x] P6.8: Build the fork's Linux amd64 image and pin its deployment digest. Include compiled extensions, SQL resources, strategy/controller modules and the intended non-secret YAML assets. Upstream `latest` does not include local changes.
- [x] P6.9: Add suitable build/deploy targets; replace or avoid the existing `make build` behavior that runs `git clean -xdf`.
- [x] P6.10: Prepare deployment configuration for dedicated PostgreSQL/Cloud SQL, its network path, Secret Manager environment injection and `ENABLE_AUTO_MIGRATION`. DDL permissions depend on the selected migration policy. Review configuration offline; actual provisioning and cloud access are deployment operations, not a staging-test prerequisite.
- [x] P6.11: Include static egress configuration if exchange API keys require IP allowlisting. Document the selected region and required REST/WebSocket destinations; offline tests do not certify regional connectivity or an actual allowlist. [Static outbound IP](https://docs.cloud.google.com/run/docs/configuring/static-outbound-ip)
- [x] P6.12: Test a read-only root filesystem with deployed YAML available, no persistent data/log/PID volumes, bytecode writes disabled and only a designated temporary working directory if necessary. A fresh instance must recover from PostgreSQL plus deployed configuration and injected secrets.
- [x] P6.13: Include enabled migration waits, schema validation and configuration loading in startup/probe budgets. Measure fresh-container startup locally and label it as local evidence; listening on a port alone is not proof of readiness or a measurement of Cloud Run scheduling. Cloud Run must not require a `.env` file.

P6 evidence (local, 2026-10-24): **59 PostgreSQL integration tests**, **866 selected regression tests**, **4 deployment preflight tests**, and a Linux amd64 deployment-image run by registry manifest digest passed for spot/V1 PMM. The image run independently asserts 12 accepted identities, two unique partial fills despite duplicate delivery, exact amounts/fees/PnL, separate CLI control/history, read-only roots, clean stopped/running shutdown/handover, fresh DML-only startup, SIGKILL, accepted-without-ACK, paused owner/session loss and PostgreSQL outage. The final packaging run sampled startup 6.8–7.9s, shutdown 1.3–1.6s, steady memory 310.8 MiB / 2 GiB, Docker CPU 2.13%, and local reused-connection SELECT 1 p95 0.310ms; these are local samples, not Cloud Run/Cloud SQL measurements. Final artifact hashes and later packaging reruns are retained in `build/e2e-offline/RUN_ID/result.json`.

P6 delivery: `hbot serve` invokes the shared application/engine in the foreground with early signal handling, a separate 9s process watchdog, versioned DB observation/publication, healthy standby/stopped startup, stdout context and health-only HTTP routes. The cloud image explicitly selects the existing build target, includes SQL/non-secret configuration, disables bytecode writes and runs non-root; the original unqualified Docker quickstart target remains available. `make build` no longer deletes the working tree. Build/review/deploy targets and [deployment instructions](../cloudrun.md#service-and-configuration) describe Cloud SQL socket/network access, pinned Secret Manager versions, continuous CPU/instance limits and optional static NAT. The concrete preflight binds the deployment registry digest to passing E2E evidence. Required CI steps build/run offline tests and retain artifacts; they have been configured, not executed remotely during this work.

[Acceptance mapping and final evidence](../../reports/platform/postgres-verification.md) record **926 selected regression tests**, **61 real PostgreSQL tests**, and all five passing deployment-image scenarios: schema policy, V1 spot, V2 spot, V2 perpetual and distinct-image upgrade/rollback. The concrete offline deployment review matches the tested registry manifest. No cloud provisioning, external image publication, real exchange request or staging deployment has been performed.

## 7. Tests and acceptance criteria

Extend existing tests wherever possible. Use real PostgreSQL for DDL, locking, concurrency and transaction behavior; SQLite or mocks do not prove those semantics.

### 7.1. Required verification layers

| Layer | What runs | What it proves |
|---|---|---|
| Existing unit/component regression | Existing runner, CLI, strategy, connector, recorder and configuration tests, extended where needed | The port preserves established trading decisions, event processing and accounting behavior |
| PostgreSQL integration | Production migration runner, ORM and recorder against disposable real PostgreSQL | Canonical schema, replay, precision, transactions, constraints and locking |
| Offline container E2E | Deployment image and real entrypoint/engine/connectors/recorder/CLI, real PostgreSQL, local simulated exchange HTTP/WebSocket endpoints | Complete bootstrap, trading, persistence, control, failure handling and fresh-instance recovery |
| Offline deployment review | Container image and deployment configuration | Intended port/probes, continuous CPU setting, instance limits, secret references, config packaging and shutdown budget are configured consistently |

All four layers are required for the selected first-release connectors and strategies. No Cloud Run staging environment, exchange demo account or live trading is required. A paper connector is useful for strategy regression but does not replace exercising the selected real connector's request/response handling in E2E.

### 7.2. Offline E2E harness and scenarios

Here, offline means no access to real exchanges or cloud services during test execution. Build/pull dependencies beforehand, then run the application, PostgreSQL and a minimal exchange simulator on an isolated local container network. Test fixtures and temporary test artifacts are allowed; the application must recover without persistent local runtime files.

- [x] T1: Run the exact Linux amd64 deployment image by digest through its production service entrypoint. Match the target PostgreSQL major version. Do not bypass bootstrap, migration, schema validation, ownership, recorder transactions or recovery with mocks.
- [x] T2: Reuse existing OKX spot/perpetual response fixtures and connector test support. Add only the small HTTP/WebSocket server needed across process boundaries, using existing dependencies where practical. Use existing endpoint/factory injection points or a narrow test adapter; audit REST, WebSocket, time and instrument endpoints and block external network access so incomplete fixtures cannot reach a real exchange.
- [x] T3: Keep exchange truth in the simulator independently of the application DB: accepted client/exchange order IDs, open orders, fills, balances and cancellations. Preserve the simulator and PostgreSQL while replacing the bot container. Support controlled delayed/lost acknowledgments, duplicate/out-of-order events and reconnects; do not implement a general exchange or matching-engine framework.
- [x] T4: Use an existing strategy with fixed configuration and deterministic market events. Assert expected order side, quantity and price, then inject partial/full fills and cancellations. Compare the simulator's accepted orders, SQL rows and CLI history to independently specified expected values, including exact Decimal amounts, fees, time units and applicable positions/PnL. A live process or nonempty table is not sufficient evidence.
- [x] T5: Start from an empty DB with auto-migration enabled, trade, stop and replace the container with a fresh writable layer. Verify restored tracking state/history and reconciliation before new orders. Repeat with auto-migration disabled against a valid schema using a DML-only role; empty/incompatible schemas must fail before any exchange submission.
- [x] T6: Run CLI commands in a separate process with no shared runtime directory. Verify status/history, conflicting control commands, durable user stop and the distinction between user stop and platform SIGTERM. Exercise local dotenv loading and cloud-style injected environment values without keystore/password files.
- [x] T7: Start two bot containers against the same DB/account simulation. Verify serialized migrations, standby startup and single ownership. Terminate the ownership connection and pause/resume a process; inspect the independent exchange request ledger and DB state to expose stale submissions and uncertain in-flight requests.
- [x] T8: At controlled event barriers, kill the bot after exchange acceptance but before acknowledgment/persistence, interrupt a DB transaction, drop/reconnect WebSockets and interrupt/restore PostgreSQL. Verify reconciliation before resubmission, one persisted record per fill and no duplicate accepted logical order in the defined scenarios. Unresolved uncertainty must block new risk rather than be treated as success; this is bounded scenario evidence, not an exchange-wide exactly-once guarantee.
- [x] T9: Send SIGTERM to the real PID 1 and enforce the configured shutdown deadline, then separately exercise SIGKILL recovery. Test read-only deployed YAML/root filesystem with only a disposable temporary directory, stdout/stderr logging and `$PORT` health endpoints. Verify the loop progresses without inbound HTTP requests; this tests application independence from requests, not Cloud Run CPU allocation.
- [x] T10: Exercise compatible old/new ported images locally for additive schema upgrade, ownership handover and compatible rollback. Validate intended config/environment revisions and absence of durable writes to local SQLite, snapshots or PID files.
- [x] T11: Use event barriers and bounded polling with explicit timeouts instead of timing-dependent sleeps. Failed prerequisites/timeouts fail the test; retries must not mask a failing first run. Retain sanitized logs, image digest, scenario seed, schema assertions, selected DB results and the simulator request/event ledger as failure evidence.

Reuse the existing test layout and runner. Introduce only a small E2E harness and the transport simulation needed to run real processes; no second bot implementation or separate strategy framework is required.

Final offline release evidence (2026-10-24): `build/e2e-offline/20261024-190244-08c3ef/result.json` has all five scenarios passing and `release_scope_complete=true`. Tested registry manifest: `sha256:e9f6dc7a5105553723d46c5194c902e47ade792fc7c62d1b0e8bf77d5328c714`; compatible predecessor: P6 manifest `sha256:3fdc613a25c51cd3e2d69b3fd0288744495027e4d4ae04e90aa15e057377e376`. Both image contents differ. The upgrade case mounts one nullable test-only additive column script through the production runner; it adds no production schema requirement. The actual packaged recovery-column upgrade from populated scripts 001–005 to 006–007 is independently covered by PostgreSQL integration. Every future release must review/test its own actual SQL and predecessor compatibility.

The final run records 14 accepted spot identities and three unique fills, plus V2 spot/perpetual positions and restored risk peaks. Local samples: startup 6.77–8.81s, clean shutdown 1.00–1.63s, spot steady memory 237 MiB / 2 GiB, Docker CPU 1.86%, and reused-connection SELECT 1 p95 0.312ms over 25 samples. These are local workload samples, not peaks or Cloud Run/Cloud SQL measurements. A synthetic filled deployment example passes the concrete offline review against this complete report; real project/service-account/secrets/network values remain operator deployment inputs.


### 7.3. Acceptance coverage

Map each criterion to the smallest meaningful layer above; keep complete lifecycle and recovery paths in container E2E. Cloud-style environments, revision overlap and secret updates below are simulated locally, without claiming to test Google Cloud services. Optional legacy-import/cutover criteria apply when those compatibility tools are included.

| ID | Scenario | Required result |
|---|---|---|
| A01 | Empty DB, auto-migration enabled | Required objects are created and validated; no hidden ORM DDL |
| A02 | Replay all SQL 100 times on a populated DB | No duplicate objects/rows, reset state or unexpected definition/data changes |
| A03 | Two starts with auto-migration enabled | Migration execution is serialized with bounded waits and no duplicate-DDL race |
| A04 | SQL error or termination mid-batch | Current file rolls back; next enabled start recovers; trading has not started |
| A05 | Named index/constraint with the wrong definition | Validation detects drift and fails startup clearly |
| A06 | Credential special characters, missing fields or connection/authentication failures | Correct parsing/validation or clear failure, redacted logs and no SQLite/legacy-secret fallback |
| A07 | Recorder round-trip | Identities, Decimal values, time units and JSON survive across all relevant models |
| A08 | Fill before create, duplicate fill, transaction rollback | No lost fills/double counting or cache acknowledgment before commit |
| A09 | Separate CLI process reads cloud state/history | PostgreSQL-backed results work without local runtime snapshots or PID files |
| A10 | Conflicting/retried start/stop control writes | Version checks preserve newer intent and acknowledgments |
| A11 | User stop followed by service restart | Desired stopped state survives |
| A12 | Platform SIGTERM during operation | New sends stop, cleanup is bounded and unresolved state remains recoverable |
| A13 | SIGKILL/restart between send and exchange acknowledgment | Reconcile before resubmission; do not assume failure/cancellation |
| A14 | Overlap, lock connection loss, process pause/resume | Ownership checks protect sends/state, including in-flight uncertainty |
| A15 | Slow/unavailable PostgreSQL | No unbounded event-loop/health hang; block new risk and reconcile before resuming |
| A16 | V1, V2 script, V2 controller and local hot reload | Existing YAML behavior and engine remain usable; generated wrappers are reproducible |
| A17 | Read-only deployed YAML, no persistent runtime directories | Boot/run/stop/restart/history work using PG state and injected credentials |
| A18 | New ported revision while a compatible ported revision is alive | Both use canonical names; additive migration, no standby startup deadlock and verified handover |
| A19 | Application rollback | Only images compatible with canonical schema names are used; no down migration, reverse renaming or old helper replay undoing new behavior |
| A20 | Repeated optional legacy import | No duplicate trading data, precision/time drift or source mutation |
| A21 | Upgrade a populated previous canonical PostgreSQL schema | Preserve data and running-revision compatibility, not just empty-DB behavior; mixed-case legacy conversion is covered separately by A31–A32 |
| A22 | Local `.env`, explicit env file and process overrides | Deterministic path/precedence; missing default allowed; explicit missing file fails; literal secrets preserved |
| A23 | Cloud Run environment without `.env` | Complete bootstrap from injected settings; accidental `.env` is ignored and secrets never serialized into YAML |
| A24 | `ENABLE_AUTO_MIGRATION=false`, compatible existing schema | No migration DDL or automatic ORM DDL; startup works with a suitable DML-only role |
| A25 | `ENABLE_AUTO_MIGRATION=false`, empty/incompatible schema | Fail schema validation before trading; do not silently enable migration |
| A26 | Flag omitted, `true`, `false`, invalid value | Default true, correct boolean parsing, invalid input rejected; process env takes precedence |
| A27 | Explicit `make migrate` while automatic migration is disabled | Migrations execute through the shared runner with locking; normal startup still honors false |
| A28 | Environment credentials with no keystore files/password | Selected connectors authenticate through existing validation/config objects; no `.password_verification` or encrypted YAML required |
| A29 | YAML or secret deployment update | New revision uses intended config/secret versions; ephemeral edits are not reported as durable updates |
| A30 | Fresh canonical schema and ORM round-trip | All application-owned tables/columns/indexes/constraints/sequences/triggers/functions follow the naming contract; ORM writes, joins, recorder history and imports use those names |
| A31 | Populated legacy PostgreSQL schema, explicit cutover and replay | With legacy writers stopped, rows/keys/grants/dependencies/sequence state survive renaming; replay is a no-op and forced failure rolls back the cutover transaction |
| A32 | Legacy names at normal startup, or both old and canonical objects exist | Startup does not create parallel empty tables; conflicts fail clearly rather than choosing or merging data automatically |
| A33 | Deterministic existing strategy through real connector, engine and PostgreSQL recorder | Accepted orders, fills, fees and applicable positions/PnL match independent expected values; preserved business behavior is checked against the P0 baseline |
| A34 | Full container replacement while PostgreSQL and simulator survive | Recovery succeeds with no original writable layer; exchange and persisted state reconcile before new risk |

### 7.4. CI and release gates

The following Make targets and release checks are implemented; final image evidence is recorded below and in the acceptance mapping:

- [x] Add `make test-postgres` using disposable PostgreSQL instances/databases; fixtures execute the production SQL runner.
- [x] Add `make test-e2e-offline` to run the isolated container scenarios and clean up only their test resources. Required PostgreSQL/E2E CI jobs must fail when infrastructure is unavailable rather than silently skip because a DSN or container runtime is absent.
- [x] Run relevant runner, CLI, recorder, configuration, strategy/V2 and selected connector regression tests. Record an explicit test-to-criterion mapping for A01–A34 and the supported connector/strategy scope.
- [x] Require regression, PostgreSQL integration, offline E2E and deployment-configuration review to pass before release. Keep sanitized test reports and image/config digests; the reviewed deployment artifact must use the tested image.
- [x] Record local CPU/RAM/startup/DB latency and shutdown measurements against agreed budgets. Label these as local measurements, not Cloud Run performance evidence. No staging deployment is a release gate.

## 8. Delivery order and definition of done

Suggested order: **P0 -> P1 -> P2 -> P3 -> P4 -> P5 -> P6 -> offline verification/release**. Add meaningful tests before or alongside each implementation change; establish the minimal PostgreSQL/E2E harness early and extend it as each phase lands. Every phase continues to use the same existing engine and models.

- [x] One shared application implements the port; existing trading behavior is preserved by regression and offline E2E evidence. No duplicate legacy/cloud trading engine is introduced.
- [x] PostgreSQL is the durable source for trading and runtime control state; no runtime SQLite/JSON/PID fallback remains in the cloud path.
- [x] Ordinary YAML configuration remains supported without a new PostgreSQL configuration store.
- [x] Local `.env` loading and Cloud Run environment/Secret Manager injection share validated bootstrap settings with documented precedence.
- [x] Connector credentials use environment input on cloud; existing validation is reused and encrypted keystore files/passwords are not required for that mode.
- [x] Every service start honors `ENABLE_AUTO_MIGRATION`; both modes validate schema before order-producing tasks, and explicit migration remains available.
- [x] SQL is ordered, simple, idempotent and reviewable. All application-owned SQL identifiers follow the canonical snake_case mapping and naming conventions; existing data and model behavior are preserved, with new schema limited to justified gaps.
- [x] Deployment configuration specifies background CPU, instance limits, probes, configuration packaging, secrets and PostgreSQL networking. Offline tests verify container startup, health, logging, application PostgreSQL connectivity and shutdown; cloud-only behavior is explicitly recorded as unverified until deployment.
- [x] Required regression, real PostgreSQL integration and offline container E2E gates pass for the declared connector/strategy scope using the deployment image. Cloud Run staging is outside the required verification scope.
- [x] Demonstrate ownership and recovery under interrupted shutdown rather than relying on persistent disks or instance limits alone.
- [x] CLI history/status/control work against PostgreSQL; local YAML editing and cloud deployment updates have explicit semantics.
- [x] Provide a runbook for local bootstrap, both migration policies, legacy schema name cutover when needed, configuration/secret deployment, migration errors, DB outages, stop/recovery and compatible application rollback.
- [x] Review confirms that connectors/strategies/engine were reused and that no unnecessary framework, table or dependency was introduced.

System-wide precision changes, multiple bots sharing databases/accounts, complete Gateway support, live cloud configuration editing, dashboards, event sourcing and broad ORM optimization require separate justification and scope.

## 9. Post-port performance and recovery review

Requested after completion of P0–P6. Evidence and cache decisions are documented in
[postgres-performance-review.md](../../reports/platform/postgres-performance-review.md).

- [x] Measure large-history V2 startup, pending-order lookup plans and actual fenced recorder writes using disposable PostgreSQL; preserve before/after artifacts.
- [x] Limit V2 recovery to selected controllers and required performance columns; stream history and preserve existing Decimal arithmetic and fail-closed validation.
- [x] Add simple idempotent lookup indexes through the existing SQL runner; verify populated-schema replay and migration-disabled startup.
- [x] Check N+1 with query budgets and detached history rendering; prove repeated live PnL reports use the existing in-memory cache without SQL.
- [x] Correct bounded recorder warmup to recover recent order mappings and per-market fills; reproduce the former behavior with a failing test first.
- [x] Review cache necessity: retain existing in-memory PnL/TTL caches. Defer `inmem`/`redis` adapter and environment selection until a measured safe consumer requires them; these options are not implemented by this review.
- [x] Complete regression, real PostgreSQL and rebuilt deployment-image offline E2E checks for the review changes; update evidence and commit.

Review validation: **926 regression tests**, **71 PostgreSQL tests** and **five
offline image scenarios** passed. Report:
`build/e2e-offline/20261024-220352-d8f55f/result.json`; tested manifest
`sha256:70b97bea620732d53f228cb16dde63128b9177fc4e08d8c634e466c662a7879b`.
Upgrade/rollback uses a distinct image rebuilt from `ddf77c4a7`, applies packaged
index migration `008` and preserves recorded economics. The concrete synthetic
deployment preflight passes against this report; no cloud deployment was performed.

## 10. Environment defaults, backend selection and SQL naming follow-up

Requested after the LIFE/OKX audit. Reference: local
`life-wallet-backend/conf/env.go` and `default_values.go`. This extends explicit
local compatibility while keeping the deployed Cloud Run service on PostgreSQL.

- [x] Centralize non-secret defaults in `hummingbot/client/config/defaults.py`; retain process env > selected dotenv > defaults. New env keys omit the `LIFE_` prefix. Preserve secret values and require explicit database/live inputs.
- [x] Add preferred `ENV_FILE`, retaining `HBOT_ENV_FILE` as a lower-priority compatibility alias; reject missing/empty explicit files and cloud env-file selectors.
- [x] Add `STORAGE_BACKEND=postgres|file`. Explicit local file mode uses the original SQLite/JSON/PID paths even when a URL exists. Omitted mode preserves existing PostgreSQL detection; errors in a selected PostgreSQL backend never fall back to files. Service/Cloud Run requires PostgreSQL and mode changes require restart.
- [x] Rename the eight legacy `LIFE_` keys in the ignored local `.env` without changing their values. Document that the previous migration knobs are not active aliases for the current runner; current settings are `DATABASE_URL`/`ENABLE_AUTO_MIGRATION`.
- [x] Remove `public.` qualifiers from all eight packaged SQL scripts and the manual legacy-name script. Keep the runner's canonical `SET LOCAL search_path = public`, schema checks and simple idempotent objects. This requested formatting replay changes script checksums without changing table identities or adding a new schema.
- [x] Verify original file-mode SQLite/JSON round-trips, explicit/legacy backend selection, defaults/precedence, cloud deployment rejection, non-default search-path migration replay and populated legacy cutover. Results: 944 selected regression tests and 72 PostgreSQL tests passed.
- [x] Complete rebuilt-image offline E2E, bind its digest to deployment review, update evidence and commit. All five scenarios passed in `build/e2e-offline/20261025-001145-0d9d95/result.json`; the matching concrete synthetic deployment artifact passed preflight. See the current manifest in [verification evidence](../../reports/platform/postgres-verification.md).

## 11. Cloud Run runtime image optimization

Requested after the env/SQL follow-up. Measurements and the explanation of Docker
image aliases are in [cloudrun-image-optimization.md](../../reports/platform/cloudrun-image-optimization.md).

- [x] Measure layer/package sizes and running containers. The 4.64 GB baseline copies full base Conda plus build/test tooling; stored tags do not represent extra running bots. Previous-image tags are used for compatibility tests.
- [x] Reuse the existing compiler stage/dependency versions, prepare runtime files separately, and copy only the application environment/application into pinned Debian slim. Preserve runtime libraries/SDKs, foreground entrypoint, non-root UID, immutable config, SQL and health/shutdown contracts.
- [x] Remove explicit build/test packages using Conda manifests, preserve files with retained owners, discard headers/static archives/docs/orphan caches and remove empty optional-package directories. Retain valid runtime bytecode caches for startup performance. Cover escaping paths and bytecode-only modules with packager tests.
- [x] Reduce measured Docker image size to 1,363,640,076 bytes from 4,637,764,625 bytes (70.60%). Keep a 1.5 GiB current-image gate in offline E2E. The 1.19 GB no-cache trial passed E2E but increased local bootstrap time; the final candidate retains runtime caches.
- [x] Verify Cython imports, 63 connector settings, Numba JIT, TA-Lib, TLS trust, CLI and all nine SQL assets without network in read-only runtime. Compare retained Python distribution versions; all match.
- [x] Pass 950 native regression tests and 90.18% packager coverage. Align two stale migration-default assertions/docs with the already committed `false` default; test explicit true/false overrides without changing the production default.
- [x] Pass all five slim-image E2E scenarios against a compatible full-image predecessor, bind deployment review to the tested manifest, update evidence and commit. Report: `build/e2e-offline/20261025-011532-ec2194/result.json`; `passed=true`, `release_scope_complete=true`, matching synthetic deployment review passed. Final-image local startup samples: 6.884–8.407s. See [verification evidence](../../reports/platform/postgres-verification.md) for the exact manifest and measurements.
