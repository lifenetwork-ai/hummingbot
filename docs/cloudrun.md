# Cloud Run operations

Build, configuration, release verification, deployment and logging for the
foreground PostgreSQL service. Start with [service configuration](#service-and-configuration),
then [runtime images](#runtime-image), [offline release verification](#image-and-offline-verification)
and [cloud configuration](#cloud-configuration). Operational diagnostics are in
[logs/severity](#logs-and-severity) and [memory verification](#long-running-memory).
Use the [first deployment checklist](#first-deployment-checklist) for the project
worksheet, stopped-service verification and FE handoff.

Use [PostgreSQL operations](postgres-runtime.md) for DB/ownership/recovery and
[FE integration](fe-integration.md) for control/reporting/authentication. Local
checks do not provision cloud resources or establish real exchange connectivity;
measured release results remain in [reports](../reports/README.md).

## Service and configuration

For a complete BTC-USDT Demo preset with explicit policy settings, see
[OKX Demo configuration](okx-operations.md#demo-preset).
`make build-cloudrun-demo` adds that catalogue and Demo environment defaults to
an existing runtime image; it does not deploy or start a trading session.

Run `hbot serve`, or the equivalent production command
`python -m hummingbot.cli.service`. Set `STORAGE_BACKEND=postgres`; `DATABASE_URL`
is required. Cloud Run rejects file mode. Local invocation
uses the shared dotenv loader; Cloud Run uses injected environment values and
ignores `.env` and rejects `ENV_FILE`/`HBOT_ENV_FILE`. Supply exchange credentials through the environment field names
documented in [runtime operations](postgres-runtime.md).

The initial database desired state is stopped. In another process, select a
packaged configuration with `hbot start CONFIG.yml`; `hbot stop` persists user
intent. A platform SIGTERM preserves desired state. The same service remains
alive while stopped, and a non-owner revision remains in standby without
constructing connectors/strategies. Status and versioned acknowledgments come
from PostgreSQL. Unclean exits/unknown external effects retain HALT; process
restart does not reset that state.

The service exposes GET `/startup`, `/live`, and `/ready`. With
`ADMIN_API_ENABLED=true`, it also exposes Basic Auth control/reporting endpoints
and development-only Swagger; see the [FE/API guide](fe-integration.md). Startup becomes
successful after migrations/schema/configuration/credential bootstrap, including
valid stopped or standby states. Liveness does not query the DB/exchange.
Readiness describes active trading; do not use it as a deployment startup probe,
because the previous revision may still own the bot.

Copy reviewed non-secret client/strategy/controller YAML into `deploy/cloudrun/conf`
before building. It mirrors the original `conf` layout. The provided client file
disables anonymized metrics; no local `conf`, keystore, `.env`, data or logs are
copied into the cloud image. Strategy/controller modules already come from the
source tree. Configuration hashes and the Cloud Run revision are published in
status. Changing packaged YAML requires rebuilding/retesting the intended image.

## Runtime image

Build the service artifact with `make build-cloudrun`; `CLOUDRUN_IMAGE` selects
its local tag. The upstream `release` / `legacy` targets retain their original
development/interactive packaging.

The `cloudrun` target reuses the builder's compiled Cython extensions and Python
environment at `/opt/conda/envs/hummingbot`. A packaging stage removes compiler,
Conda manager and test tooling using installed package manifests, then copies
the prepared environment and application into the pinned Debian slim runtime.
Runtime dependency versions remain those resolved by the builder. Native
libraries and connector SDKs required by discovery and strategies are retained.
The installed package inventory is `/home/hummingbot/cloudrun-runtime-packages.json`.

The service runs as UID 10001 with packaged SQL and non-secret configuration.
Runtime roots can be read-only; temporary state and Numba cache use `/tmp`.
Valid installed Python bytecode caches are retained, while orphan caches of
removed modules are discarded. Rebuilding dependencies belongs in the builder.

### Images and containers

Images are stored artifacts; containers are processes started from them.
`docker image ls` lists images and `docker ps` lists running containers. Docker's
uncompressed image size, registry download size and process RAM are different
measurements. Multiple tags with the same image ID share stored layers.

`hummingbot-cloudrun:local` is the ordinary local build tag. Offline E2E briefly
uses a loopback registry and a `127.0.0.1:PORT/hummingbot:offline` tag to resolve
an immutable manifest, then removes that registry. Verification creates temporary
database/exchange/client containers, including overlapping old/new bot revisions
for ownership takeover tests. Normal operation needs one service container per
logical bot, connected to PostgreSQL. A local database may use its own container;
Cloud SQL is an external database service.

Builder cache does not ship as part of the runtime image. Keep image selection
separate from cache cleanup; avoid broad prune commands affecting other projects.

### Artifact verification

The offline harness enforces its image-size budget and checks native imports,
Numba JIT, TA-Lib, TLS trust, connector discovery, CLI and packaged migrations in
a network-disabled, read-only container. Release evidence must match the chosen
image/manifest. Packaging changes require fresh image verification.

Historical sizes, package inventories and startup measurements are in the
[image optimization report](../reports/cloudrun-image-optimization.md) and
[PostgreSQL verification report](../reports/postgres-verification.md).

### Long-running memory

The headless service retains at most 1,000 recent connector fill events for
diagnostics; durable fills, fees and history remain in PostgreSQL. Legacy
interactive/file mode keeps full history. When connector balance limits or
snapshot adjustment require full fill history, the service preserves it and logs
`connector_history_unbounded` at WARNING. Do not disable a balance limit simply
to hide memory growth; this special mode requires a separate capacity decision.

The offline memory check reuses the simulator and PostgreSQL harness, injects
fills and UI reads, and rotates/replaces sessions. It records RSS plus task,
strategy/provider and retained-ID counts. A diagnostic GC is requested only
after the measured window and Stop to check collectability; production GC policy
is unchanged. After building the image, run:

```sh
python scripts/test_mm_memory.py --image hummingbot-cloudrun:local --seconds 600 --check-position-startup
```

See [retention review](../reports/mm-memory-retention-review.md) for measured
scope and limitations. A bounded probe is not proof of unlimited uptime.

`--check-position-startup` also checks the entire runtime log for the resolved
private-position contract-size startup error. It does not change service logging
or relax position/readiness validation.

## Logs and severity

`hbot serve` writes console logs to stdout. It defaults to one JSON object per
line with `severity`, `message`, UTC `timestamp`, logger name, bot ID, config,
owner UUID/generation and revision. The current MM session ID is included when
available. Event, reason, scope, error type and command ID are included when
the logging call supplies them. Multiline messages and tracebacks stay in one
physical JSON line.

Cloud Run collects console output. Its structured-log ingestion recognizes
`severity` and `message`; no sidecar or Google logging dependency is required.
See [Google's service logging guide](https://docs.cloud.google.com/run/docs/logging).

```dotenv
LOG_FORMAT=json
LOG_LEVEL=INFO
```

Local `.env` uses the existing environment loader. On Cloud Run inject these
settings into the revision environment. A settings change requires a process
restart/new revision; there is no runtime log-level admin endpoint.

| Level | Meaning / use |
|---|---|
| `DEBUG` | Detailed connector/executor diagnostics; temporarily enable for investigation |
| `INFO` | Normal startup, lifecycle, successful recovery and shutdown |
| `WARNING` | Degradation or transient failure that can recover/retry |
| `ERROR` | Failed operation or incident requiring operator intervention |
| `CRITICAL` | Available for an unrecoverable process-wide failure; ordinary waiting/retry is not critical |

`LOG_LEVEL` is a minimum threshold: `INFO` emits INFO through CRITICAL; `DEBUG`
emits all five. Existing call sites keep their explicit levels. The service
promotes Hummingbot's `network()` calls to WARNING before filtering, so they are
visible at INFO. Custom EVENT/METRIC records remain diagnostic verbosity and
render as DEBUG when admitted. Legacy interactive/file YAML logging keeps its
original NETWORK level and configuration.

SQLAlchemy keeps its WARNING threshold even when the service uses DEBUG, so
routine queries and bound row values do not flood console output. SQLAlchemy
warnings/errors remain visible according to the service threshold. Python
warnings, including Pydantic serializer warnings, go through the same JSON
formatter as WARNING instead of writing multiline text directly to stderr.

`LOG_FORMAT=text` provides readable local output with the same level threshold,
UTC time and context; use JSON for Cloud Logging severity extraction. The service
reinstalls its console formatter after the shared application bootstrap resets
logging. No runtime log files are opened by console-only initialization.

Availability incidents emit WARNING or ERROR consistently with their API
severity. A changed reason or operator-action flag logs immediately; repeated
unchanged incidents remain limited to once per 60 seconds per scope. Recovery
emits INFO. This limits retry noise without hiding escalation.

Known configured secret values are replaced with `[REDACTED]` in messages,
context and exception output: environment keys containing PASSWORD, PASSPHRASE,
SECRET, API_KEY or DATABASE_URL, plus the encoded/decoded DSN password. Values
shorter than four characters are excluded to avoid corrupting routine text.
This is defense in depth, not detection of every possible secret: avoid logging
credentials, headers, raw requests or arbitrary private payloads, including at
DEBUG. The outer service failure boundary continues to log only sanitized reason
and exception type rather than raw driver/Pydantic exceptions.

Example Cloud Logging filters, with the actual service name substituted:

```text
resource.type="cloud_run_revision"
resource.labels.service_name="YOUR_SERVICE"
severity>=ERROR
```

```text
resource.type="cloud_run_revision"
jsonPayload.event="service_incident"
jsonPayload.reason_code="reporting_unavailable"
```

Use the revision label, `jsonPayload.bot_id`, `owner_generation` and `session_id`
to distinguish restarts, ownership handoffs and sessions. These application logs
do not yet propagate HTTP trace context; Cloud Run request logs remain separate.
The existing application access logger stays disabled, and no Authorization
header or Basic Auth credential is added to these context fields.

## Image and offline verification

```sh
make build-cloudrun PYTHON=/path/to/hummingbot/python
# Pull test infrastructure before isolated execution:
docker pull postgres:14-alpine
docker pull registry:2
make test-postgres PYTHON=/path/to/hummingbot/python
make test-cloudrun-regression PYTHON=/path/to/hummingbot/python
make test-e2e-offline PYTHON=/path/to/hummingbot/python \
  CLOUDRUN_PREVIOUS_IMAGE=REGISTRY/COMPATIBLE_PREVIOUS_IMAGE@sha256:PREVIOUS_MANIFEST
```

The cloud target compiles Linux amd64 extensions using the existing build,
contains packaged SQL, runs as UID 10001, and executes Python as PID 1. It sets
`PYTHONDONTWRITEBYTECODE` and sends logs to stdout/stderr with bot/config/owner/
generation/revision context. Cleanup has an 8s coroutine budget and a separate
9s process watchdog. Google documents a normal 10s SIGTERM grace period.
[Container contract](https://docs.cloud.google.com/run/docs/container-contract)

Offline verification enforces a **1.5 GiB** current-image budget and first checks
native imports, Numba JIT, TA-Lib, TLS trust, SDK discovery, CLI and SQL assets in
a network-disabled read-only container. The previous compatible image may exceed
the new budget because it is used only to exercise upgrade/rollback compatibility.

The E2E harness uses real PostgreSQL 14, the deployment image, read-only roots,
temporary `/tmp`, separate CLI containers, and an independent HTTP/WebSocket
exchange ledger on a Docker internal network. It mounts only immutable test YAML
and simulator assets. The narrow transport override accepts public dummy fixture
credentials and rejects `K_SERVICE`; it cannot be enabled on Cloud Run. No test
queries a real exchange. Its simulator is deliberately limited to named scenarios.

The default all-scenarios suite covers schema, V1/V2 spot/perpetual, MM sessions,
funding/recovery and the optional policy scenarios.
For complete release evidence, prefetch a distinct compatible previous ported
image and supply `CLOUDRUN_PREVIOUS_IMAGE`. The upgrade case runs both images,
replays a mounted test-only additive SQL script through the production migration
runner, changes the selected immutable strategy configuration, hands over ownership,
and rolls back the application while retaining new nullable data and old economics.
The fixture column is never added to the production SQL package. This tests the
specified compatible change; every future release must supply its own compatible
predecessor and review its actual SQL changes. Legacy pre-port images are unsupported.
CI accepts the pinned predecessor in repository variable `HBOT_PREVIOUS_COMPATIBLE_IMAGE`.
Without a compatible predecessor, `release_scope_complete=false`. A passing
scoped/smoke run cannot approve deployment. Regression runs isolate legacy event-loop-dependent
configuration/performance tests from CLI tests that use `asyncio.run()`.

Before isolation, the harness briefly publishes the image to a disposable
loopback-only registry, resolves/pulls its manifest digest, then removes that
registry. Containers run by that immutable reference. A Docker image/config ID
is not a registry manifest digest. Results, configuration hash, sanitized logs,
ledger and local resource/startup/shutdown/DB measurements remain under
`build/e2e-offline/RUN_ID`. Only that run's containers/network are removed.

## Cloud configuration

Use [service.example.yaml](../deploy/cloudrun/service.example.yaml) as an operator-filled template.
The proposed region is `asia-southeast1`; it is not a verified exchange region or
an already selected cloud project. Configure a dedicated PostgreSQL database,
service account, explicit numeric Secret Manager versions and the tested image's
registry manifest digest. For perpetual credentials, use the corresponding
`HBOT_OKX_PERPETUAL_` variables. No application secret belongs in ordinary YAML.
[Secret injection](https://docs.cloud.google.com/run/docs/configuring/services/secrets)

The example uses the Cloud SQL Unix-socket integration. Grant the service account
Cloud SQL Client access and access to the referenced secrets. A placeholder DSN
shape for psycopg2 is:

```text
postgresql+psycopg2://BOT_USER:URL_ENCODED_PASSWORD@/BOT_DB?host=/cloudsql/PROJECT:asia-southeast1:INSTANCE
```

Match the instance connection name in the annotation and DSN. PostgreSQL table
permissions are separate from cloud IAM. With `ENABLE_AUTO_MIGRATION=true`, use
the required DDL privileges; with false, apply SQL separately and use the tested
DML/sequence privileges. Do not use a transaction-pooling proxy for the advisory
owner session. The tested major is PostgreSQL 14; run the same suite against a
different selected major before claiming support for it.
[Cloud SQL connection](https://docs.cloud.google.com/sql/docs/postgres/connect-run)

The template enables continuous CPU, min/max instances 1 and a 240s HTTP startup
budget, including the migration runner's 180s maximum. It starts at 1 CPU/2Gi;
local measurements do not establish Cloud Run scheduling or Cloud SQL latency.
The advisory owner still controls overlap. Internal ingress is intentional; use
an appropriate authenticated operations path for health access.
[Cloud Run YAML](https://docs.cloud.google.com/run/docs/reference/yaml/v1)

If exchange keys require IP allowlisting, provision Direct VPC egress and Cloud
NAT with a reserved address. Add these revision annotations using the selected
regional network/subnet; Direct VPC alone does not reserve the outbound address:

```yaml
run.googleapis.com/network-interfaces: '[{"network":"NETWORK","subnetwork":"SUBNET"}]'
run.googleapis.com/vpc-access-egress: all-traffic
```

Allow the configured OKX REST and public/private WebSocket destinations (including
the registration subdomain), DNS/TLS, the chosen DB path and the configured rate
oracle source. The default source also uses public Gate.IO endpoints. Exchange
connectivity and the actual allowlist remain deployment checks, not offline-test
claims. [Static egress](https://docs.cloud.google.com/run/docs/configuring/static-outbound-ip)

Review without deploying:

```sh
python scripts/review_cloudrun.py deploy/cloudrun/service.example.yaml --template
make review-cloudrun-config CLOUDRUN_CONFIG=/path/to/filled-service.yaml \
  CLOUDRUN_EVIDENCE=build/e2e-offline/RUN_ID/result.json
```

The concrete review rejects placeholders, tags, incompatible lifecycle settings,
plaintext credentials, unpinned secrets and a digest differing from the passing
E2E artifact. It also rejects partial smoke reports without the image rollback
gate. It does not call cloud APIs. After the required release gates pass,
an operator may explicitly run `make deploy-cloudrun` with the same configuration,
evidence and selected region. This implementation has not run that target, pushed
an image to an external registry, or performed a Cloud Run staging deployment.

## First deployment checklist

Use this on the selected service; a separate staging service is not required.
Cloud-only items stay open until someone checks the actual deployment. The latest
local release evidence is in the [availability remediation report](../reports/life-mm-availability-remediation.md).
A passed local gate does not validate IAM, regional exchange access or Cloud SQL.

### Deployment worksheet

Fill these non-secret values before producing a concrete service YAML. Secret
**names and numeric versions** belong here; secret values remain in Secret Manager.

| Input | Decision needed |
|---|---|
| GCP project / region / service | Explicit project; region with verified OKX access |
| Runtime service account | Dedicated account; Cloud SQL Client and access to only the referenced secrets |
| Artifact Registry image | Accessible registry, immutable tested manifest digest, compatible predecessor |
| PostgreSQL | Instance connection name, database/user, tested major, backup/recovery policy and available connections |
| Bot identity | Stable `BOT_ID`, dedicated DB; never reuse the local Demo DB for first production launch |
| Exchange | Explicit `OKX_TRADING_ENV=live` or `demo`, `OKX_SITE`, matching credential secret trio per used connector |
| Admin API | Username/password secret references, FE access route, callers' IAM and CORS origins |
| Egress | Exchange registration region, DNS/TLS/REST/WS destinations; reserved NAT IP if keys use an IP allowlist |
| Trading catalogue | Spot/perpetual/both availability, target pair, balances, budgets, limits and enabled policies |
| Release record | Source, image manifest, offline E2E/load evidence, YAML catalogue hash and secret versions |

The existing template explicitly enables admin API, selects Live exchange routing
and closes development docs with `ENV=production`. **Review that Live choice**;
it is not permission to trade. Demo uses `OKX_TRADING_ENV=demo` and
`HBOT_OKX_DEMO_*` / `HBOT_OKX_PERPETUAL_DEMO_*` names. Do not copy live credentials
into Demo names. Spot-only operation can omit the perpetual secret trio; select
`market_mode=spot` and ensure maintainers/FE do not offer an unprovisioned market.
Keeping a perpetual template in YAML alone does not provision its credentials or
make an unlisted perpetual instrument available. The bundled LIFE catalogue is
an example with disabled optional policies, not approved listing parameters.

### Before deployment

- [ ] Fill the worksheet and `REPLACE_*` values in a private, reviewed service YAML.
- [ ] Select the packaged YAML. Changing `deploy/cloudrun/conf/` requires a new
  image and matching verification; API cannot override it. Verify that its
  deployed hash and policies are the ones the operator expects.
- [ ] Publish/mirror the tested image to the chosen registry and verify its
  manifest digest against the E2E evidence. Tags and Docker config/image IDs are
  not manifest digests. If copying changes the manifest, verify that exact new
  manifest again; do not edit a result JSON to manufacture a match.
- [ ] Check Cloud SQL connection path, secret access, DDL/DML permissions and
  `ENABLE_AUTO_MIGRATION`. Budget for overlapping revisions, each with its own
  pool; five is per engine, not a database-wide connection ceiling.
- [ ] Keep continuous CPU, min/max one, tested startup `/startup` and liveness
  `/live`. Do not replace startup with `/ready`. Verify ingress/IAM separately
  from application Basic Auth; service YAML does not grant invoker permissions.
- [ ] Keep the initial desired state stopped. A fresh DB initializes stopped;
  **an existing DB can resume its saved running session**, even with no new API
  Start. For a reused DB, persist Stop through the current API/CLI and confirm its
  acknowledgment and absence of working orders before rollout. SIGTERM, changing
  traffic or rebuilding YAML does not erase desired state, positions or HALT.
- [ ] Review rollout/rollback compatibility and DB backups. Stop removes quotes
  while retaining positions; do not advertise Stop as closing exposure.
- [ ] Run template/concrete checks below; retain the reviewed config/evidence pair.

```sh
# Local check only; placeholders are intentionally allowed here.
python scripts/review_cloudrun.py deploy/cloudrun/service.example.yaml --template

# Concrete check: placeholders forbidden, complete E2E scope and digest required.
make review-cloudrun-config \
  CLOUDRUN_CONFIG=/path/to/filled-service.yaml \
  CLOUDRUN_EVIDENCE=build/e2e-offline/RUN_ID/result.json
```

The review now requires explicit PostgreSQL, production docs mode, exchange
routing and enabled admin API; checks pinned admin/Live/Demo secret references;
rejects duplicate environment names and incomplete supplied credential trios.
It checks references, not secret contents, account permissions, packaged market
availability or cloud resources. Keep the default project out of deployment
selection: after the concrete result is reviewed, the existing deployment target
requires `CLOUDRUN_PROJECT` explicitly.

```sh
# Applies an actual revision; run only for the selected reviewed deployment.
make deploy-cloudrun \
  CLOUDRUN_PROJECT=YOUR_PROJECT \
  CLOUDRUN_REGION=YOUR_REGION \
  CLOUDRUN_CONFIG=/path/to/filled-service.yaml \
  CLOUDRUN_EVIDENCE=build/e2e-offline/RUN_ID/result.json
```

### Verify the stopped service

- [ ] Confirm project, region, runtime service account, image digest, CPU/memory,
  ingress, secret versions and effective revision environment in Cloud Run.
- [ ] Verify `/startup` and `/live` succeed through an authorized operations path.
  Stopped/standby can be healthy while `/ready` is unavailable; no trades or fills
  are expected. Startup success does not prove exchange credentials can trade.
- [ ] From the real FE gateway/network, verify `/api/v1/admin/status` and `/profiles`
  with Basic Auth. Compare process/owner revision, fresh heartbeat, `BOT_ID`,
  desired state, acknowledged version and catalogue hash. Wait for ownership
  handoff if needed; traffic reaching a revision does not itself grant ownership.
- [ ] Confirm one effective owner, `desired_state=stopped`, no active session and
  no bot working orders. Investigate stale/unknown observations; do not mark them
  as a confirmed zero balance/zero order count.
- [ ] Verify authenticated production docs/schema/assets return 404; build FE
  types from checked `docs/api/openapi.json`. Verify missing/wrong Basic Auth is
  rejected at the application after passing platform/network access.
- [ ] Check structured JSON severity/context, startup/migration events, unexpected
  ERRORs and restarts. Configure alerts for repeated incidents, stale owner/API,
  restart loops, memory pressure and DB connection pressure. A deliberate stopped
  session is not an incident; no fills alone does not mean no working quotes.
- [ ] Observe that the stopped service stays available without dashboard polling.
  This is a cloud check, not something a Docker test establishes.

Cloud Run minimum instances can still restart, and maximum limits can briefly be
exceeded. Ownership/recovery remains authoritative rather than instance count.
See [minimum instances](https://docs.cloud.google.com/run/docs/configuring/min-instances),
[maximum instances](https://docs.cloud.google.com/run/docs/configuring/max-instances)
and [CPU/billing behavior](https://docs.cloud.google.com/run/docs/configuring/billing-settings).

### FE and first session

- [ ] Complete the [FE browser acceptance](fe-integration.md#browser-acceptance-checklist)
  on the actual access path: freshness, pending receipts, conflict handling,
  blocked reasons, read-only YAML settings and Stop confirmation.
- [ ] Verify instrument rules, account identity, available LIFE/USDT and approved
  budgets before Live Start. Demo compatibility uses an existing listed pair;
  it cannot prove LIFE liquidity before listing. Do not Start the LIFE example
  merely to make `/ready` green before its instrument exists.
- [ ] For the separately approved first trading session, select provisioned markets,
  a deployed profile and bounded duration/budget through the existing command
  workflow. Verify real working orders, final tick/min-size behavior, fee/balance
  reporting and position-preserving Stop. Record actual scope under `reports/`.
- [ ] During a controlled rollout, verify the new owner/configuration and retained
  stopped intent. An old revision can remain standby/owner during overlap; review
  old traffic, tags and revision minimums if handoff does not finish. Do not bypass
  the DB ownership lock or clear recovery state to force progress.

For an incident, use [OKX blocked-quote handling](okx-operations.md) and
[PostgreSQL recovery](postgres-runtime.md). Retrying a command uses the original
idempotency key/body; a restart or automatic Start must not become a way to clear
HALT. Keep failed cloud checks and actual recovery evidence alongside successes.
