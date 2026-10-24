# PostgreSQL service deployment

The application uses the existing Hummingbot engine. Implementation and release
evidence are tracked in [the plan](../../docs/plans/POSTGRES_CLOUD_RUN_PORT_PLAN.md).
No Cloud Run service, Secret Manager secret, exchange account or cloud database
has been provisioned or contacted by these local tests.

## Foreground service and configuration

Run `hbot serve`, or the equivalent production command
`python -m hummingbot.cli.service`. Set `STORAGE_BACKEND=postgres`; `DATABASE_URL`
is required. Cloud Run rejects file mode. Local invocation
uses the shared dotenv loader; Cloud Run uses injected environment values and
ignores `.env` and rejects `ENV_FILE`/`HBOT_ENV_FILE`. Supply exchange credentials through the environment field names
documented in [runtime operations](../../docs/postgres-runtime.md).

The initial database desired state is stopped. In another process, select a
packaged configuration with `hbot start CONFIG.yml`; `hbot stop` persists user
intent. A platform SIGTERM preserves desired state. The same service remains
alive while stopped, and a non-owner revision remains in standby without
constructing connectors/strategies. Status and versioned acknowledgments come
from PostgreSQL. Unclean exits/unknown external effects retain HALT; process
restart does not reset that state.

The HTTP API contains only GET `/startup`, `/live`, and `/ready`. Startup becomes
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

The optimized `cloudrun` target copies the compiled runtime environment into a
digest-pinned Debian slim image, excluding the base Conda installation and build/
test toolchains. Measured image size is **1.36 GB**, down from **4.64 GB**; runtime
dependency versions are preserved. See [image packaging and Docker references](../../docs/cloudrun-image-optimization.md)
for the inventory, measurements and the distinction between image tags and running
containers. Builder cache remains separate from the shipped image.

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

The default suite covers schema rejection, V1 spot, V2 spot and V2 perpetual.
For complete release evidence, prefetch a distinct compatible previous ported
image and supply `CLOUDRUN_PREVIOUS_IMAGE`. The upgrade case runs both images,
replays a mounted test-only additive SQL script through the production migration
runner, changes the selected immutable strategy configuration, hands over ownership,
and rolls back the application while retaining new nullable data and old economics.
The fixture column is never added to the production SQL package. This tests the
specified compatible change; every future release must supply its own compatible
predecessor and review its actual SQL changes. Legacy pre-port images are unsupported.
CI accepts the pinned predecessor in repository variable `HBOT_PREVIOUS_COMPATIBLE_IMAGE`.
Without it, the four-scenario smoke report has `release_scope_complete=false` and
cannot approve deployment. Regression runs isolate legacy event-loop-dependent
configuration/performance tests from CLI tests that use `asyncio.run()`.

Before isolation, the harness briefly publishes the image to a disposable
loopback-only registry, resolves/pulls its manifest digest, then removes that
registry. Containers run by that immutable reference. A Docker image/config ID
is not a registry manifest digest. Results, configuration hash, sanitized logs,
ledger and local resource/startup/shutdown/DB measurements remain under
`build/e2e-offline/RUN_ID`. Only that run's containers/network are removed.

## Cloud configuration

Use [service.example.yaml](service.example.yaml) as an operator-filled template.
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
