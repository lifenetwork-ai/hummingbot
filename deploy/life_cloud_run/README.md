# Native LIFE worker infrastructure (offline candidate)

This directory contains definitions and local validation. **Nothing here has
been deployed.** PG6 import/post-cut recovery, PG7 image/complete telemetry,
PG8 full acceptance and the OKX deployment/account gates still apply. One desired
instance is scheduling, not a fence against an old REST/WS sender. No deployment
step activates an account or changes financial limits.

## Components

- A same-region manual Cloud Run Worker Pool with an explicit count of **0 or 1**,
  pinned image/secret versions, distinct generation service account and Direct
  VPC `ALL_TRAFFIC` egress through one static NAT address.
- A private PostgreSQL 16 Enterprise instance with explicit tier, disk ceiling,
  backup count, WAL retention and availability inputs; deletion protection is on.
  Runtime connects directly with the existing verified-TLS asyncpg path. Shared
  CAS supplies an instance DNS SAN; a private DNS zone maps that certified PSA
  hostname to the private IP. DSNs must use that hostname and the pinned CA file.
  Actual name/certificate/network behavior remains a deployed qualification.
- An isolated migration Job: **one task, parallelism one, zero platform retries**,
  dedicated IAM identity and migration secret. It runs the bundled numbered SQL
  entry point. Runtime has neither its DSN secret nor Job execution/impersonation
  permission. Service YAML, CA and DSNs are provisioned as distinct pinned Secret
  Manager versions; secret values never pass through Terraform variables/state.
- Native worker foreground supervision: JSON stdout, bounded `/live`, `/startup`
  and read-only `/status`; a healthy unarmed process stays live. SIGTERM closes
  local admission synchronously, starts one nonrenewable physical exit deadline
  below nine seconds, and gives owned persistence/SDK cleanup its original task.
  Timeout exits nonzero without reporting terminality, releasing holds, cancelling
  a COMMIT thread or granting replay. Cloud forced kill is still tested separately.

The official [Direct VPC worker documentation](https://docs.cloud.google.com/run/docs/configuring/vpc-direct-vpc),
[worker secret documentation](https://docs.cloud.google.com/run/docs/configuring/workerpools/secrets)
and [Cloud SQL identity-verification documentation](https://docs.cloud.google.com/sql/docs/postgres/configure-ssl-instance)
underpin these definitions. Provider schema is pinned to `hashicorp/google 7.9.0`;
Terraform is pinned to `1.11.4`, with the generated provider checksum lock checked
in. This is not a claim that the preview worker feature or an actual region has
been approved for production.

## Validate without credentials or provisioning

```sh
terraform init -backend=false -input=false
terraform fmt -check -recursive
terraform validate
terraform test
```

Every `.tftest.hcl` run uses `mock_provider "google"`; the positive `apply` is an
in-memory mock apply, not a GCP apply. Tests retain provider type validation and
check role/secret separation, fixed egress/private TLS/PITR definitions, pinned
versions, explicit instance count and unsafe-input refusals. They do not prove
actual IAM, billing, quotas, certificates, routing, Cloud SQL durability or key
exclusion. Do not substitute these mocks for OKX PG-E1/PG-E2/PG9 gates.

## Configuration and automatic migrations

`variables.tf` contains no implicit production sizing/economic defaults. Supply
reviewed nonsecret `.tfvars.json` inputs; keep `worker_instances=0` while preparing.
Do not put passwords, DSN values, private keys or API credentials in that file.
Provision the named versioned secrets and SQL principals through the operator
identity; migrations do not bootstrap accounts, policies, balances or owners.
The runtime identity must have no inherited project-wide secret access or actAs
path. The native PG4 credential broker remains a separate pinned service/identity;
no exchange key is provisioned by these definitions.

Two automatic execution modes remain available:

1. The existing trusted local headless host can use `LIFE_AUTO_MIGRATE=true` and
   await the central runner before factory/application construction.
2. The Cloud release uses the **same switch** in the deployer's `.env`, but runs
   migration in a separate Job before starting the worker. The worker itself has
   `LIFE_AUTO_MIGRATE=false` and receives no migration secret. Merely removing a
   variable after an in-process migration would not isolate schema-owner access.

The explicit release helper is `python -m deploy.life_cloud_run.rollout --env-file
/path/to/release.env`. It uses a **deployer** identity and executes: apply the
pinned definition with zero worker allocation; verify its public release identity;
await the migration Job when configured; apply one unarmed worker allocation. It never runs from a worker,
uses no shell, stops on a failed/ambiguous step, and rejects changed Terraform,
provider-lock or variable inputs before the next step. Tests replace subprocess
execution with a recording port; this command has **not** been run against GCP.
A disabled migration still requires native startup schema compatibility.

The bounded `life_rollout_identity` output must match the selected project,
region and migration Job, report zero worker allocation, and bind both workloads
to the same immutable Artifact Registry image digest. An absent/invalid output,
different Job or image, active allocation, or failed identity read stops before
Job execution or worker start. Only this public output is read; command output
is not logged, and secret values are not exported by the identity output.

The release helper uses the default Terraform workspace. It refuses inherited
`TF_CLI_ARGS*`, `TF_VAR_*`, `TF_WORKSPACE` and `TF_DATA_DIR` overrides before any
command; put reviewed variables in the explicit variable file. It freezes JSON
Terraform definitions, implicit `terraform.tfvars*`/`*.auto.tfvars*` files and
the provider lock alongside HCL inputs, checking changes before each stage.
Each child receives the same copied environment. These local input checks do not
prove deployed IAM, credential exclusion or completion of a stopped rollout.

This is a stopped-rollout workflow. Reducing desired allocation does not prove
exchange key exclusion or terminality. Complete native recovery, old-generation
exclusion and independent signed authority remain mandatory before a worker may
send. A rollback after native orders cannot be implemented by this helper or by
changing a Terraform flag; PG6 requires a reconciled controlled cutover.

The pool's resource name stays fixed for the account namespace. Generation
changes update its revision label and use a distinct generation service account;
they do not create another protected pool next to the predecessor. The first
release stage still sets allocation to zero. The generation-change mock verifies
resource identity, not actual old-instance/key exclusion.

## Container target

Build the dedicated LIFE/OKX profile from the repository root:

```sh
make life-worker-build
# Equivalent Docker command:
docker build --platform linux/amd64 -f deploy/life_cloud_run/Dockerfile \
  --target life-worker --build-arg HUMMINGBOT_BUILD_JOBS=2 -t life-worker:local .
```

The Make target accepts `LIFE_WORKER_IMAGE` and `LIFE_WORKER_BUILD_JOBS`; it
does not clean the checkout, load `.env`, start a container or push an image.

This profile pins the base digest, signed Debian snapshot, explicit conda package
URLs/hashes and pip wheel versions/hashes in `setup/locks/`. No Python dependency
solver runs during this build. `pip check` must pass for the entire installed
Python dependency graph. The artifact gate also compares packaged locks and
installed pip versions before exercising the original native modules. Resolved
exports inside an image are diagnostics, not substitutes for these build inputs.

The builder consumes `life-worker-linux-64.conda.lock`; the final worker consumes
its reviewed `life-worker-linux-64.runtime.conda.lock` subset. The intermediate
`runtime-dependencies` stage removes 16 compiler/header/build/lint packages using
Conda's offline exact-removal mode. `setup/life_runtime_dependencies.py` first
checks that every installed build artifact matches the lock and that no retained
native dependency would be removed. Original pinned pip wheels supply the mixed
`cffi`/`cryptography` dependencies. Versions of retained packages do not change;
`pip check` runs after removal. The final image copies only the resulting prefix,
so deleted toolchain bytes do not remain in its layers. Build tools remain in
the builder/cache, not in the deployed worker.

The image gate verifies the exact installed runtime Conda artifact set, original
pip versions and both lock inputs; it also refuses compiler executables/sysroot
in the worker. Build and runtime exports have separate names. This conservative
profile retains runtime numerical libraries and the existing container test
harness. It does not claim a minimal general-purpose Hummingbot dependency set.
Docker build cache is separate from image size and is not automatically pruned.

Local AMD64 measurement (2026-10-23, Docker's uncompressed image `Size`):

| Worker artifact | Bytes | Decimal GB |
| --- | ---: | ---: |
| Rebuilt original profile at `fecc42566` | 3,967,061,411 | 3.97 |
| Locked runtime subset | 2,142,560,655 | 2.14 |

The reduction is **46.0%**. The environment shrinks from 3,294 MiB to
1,542 MiB; application code/extensions occupy about 23 MiB. Registry transfer
size and shared host build-cache usage are different measurements. This change
applies to this dedicated Dockerfile, not the ordinary root Dockerfile.

The profile deliberately excludes optional `bip-utils`, `pandas-ta` and
`eip712-structs`: they belong to other connector/indicator paths and their prior
resolved dependency sets were inconsistent. This is **not** a general Hummingbot
image or qualification of arbitrary connectors/controllers. The repository's
ordinary root Dockerfile and its default `release` command remain separate.

Runtime uses UID/GID 10001, a direct foreground Python entry point and no TTY or
shell. Runtime config/secrets are mounted/injected; `.env`, host extensions and
bytecode stay outside the build context. Building the locked image successfully
does not by itself qualify reproducible native binaries, full financial replay,
shutdown or the PG8 aggregate; record their actual gate results separately.

### Native versus emulated image qualification

`run_postgres --linux-image` writes `image_environment.json` into the selected
disposable `/tmp` output directory before creating its database. It records only
the immutable image ID/architecture, Docker daemon architecture/CPU/memory,
configured worker/database resource limits and the number of containers already
running. It does not collect environment variables, image secrets or Docker
registry configuration. The daemon architecture is used because the CLI host can
connect to a different machine.

An AMD64 worker on an ARM64 Docker daemon is labelled `emulated`. That can test
packaging and functional behavior, but does not establish native AMD64 latency
headroom. For a native qualification machine, use:

```sh
python -m test.hummingbot.strategy_v2.life_liquidity.storage.run_postgres \
  --suite image_native --linux-image sha256:<immutable-image-id> \
  --require-native-image --output /tmp/<new-output-directory>
```

The explicit flag refuses emulation before creating any test database. Omitting
it still permits the labelled functional run. Both profiles retain the same
2-CPU/2-GiB worker, 2-CPU/1-GiB database and original economic/source deadlines.
The starting container count is only an observation: it does not certify CPU
isolation or absence of competing host/VM work throughout the test. Preserve
failed runs; a later green subset does not replace aggregate qualification.

For bounded investigation of the two retained quote/SWAP failures, select
`--suite image_flows` with the same immutable image and a new `/tmp` output.
This enables process-local flow spans in the parent and real service child,
including active `in_flight` spans at the intentional crash boundary. Its two
selected cases are a diagnostic subset, never the full acceptance matrix;
instrumentation can change timing. Local quote ordinals and fixed phase names
carry no request bodies or financial identities. The ordinary `image_native`
selector leaves this tracing disabled.

## Remaining operational work

Lifecycle/DB/local-readiness/loop-timer-lag telemetry includes native pool
pressure, physical outcome counters and recent phase timings. The financial
sampler uses one additional **read-only** connection, outside sender/safety pools,
with no queued samples and a deadline capped at two seconds. It reads at most
257 active rows per attempt/hold/claim family, two balances, nine current budget
keys and indexed financial/audit heads. Counts with `truncated=true` are lower
bounds; missing budgets are absent, not zero. Status/probe handlers do no SQL.
Snapshot failure retains old values with their age and `UNAVAILABLE` state.

`monitoring.tf` defines routed alerts for missing/stale telemetry, unknowns,
holds, budget utilization, DB/loop latency, loss of local eligibility while
ACTIVE, and unverified integrity/recovery status. All thresholds and notification
channels are explicit inputs. Worker alerts are disabled for an intentional zero
allocation; Cloud SQL backup-failure alerts remain enabled. The heartbeat absence
policy requires an initial time series: deployment must verify an initial STATUS
and an actual delivered test alert, including never-started-worker detection.

The recorded audit sequence/signature-presence fields are **not verification**;
the worker reports audit/backup health `UNVERIFIED`. A backup-success log also
does not prove restore parity or reconcile exchange history. Independent audit
verification/freshness and backup/recovery evidence still need the PG6/PG8 path;
do not mark PG7.8 complete from these diagnostic fields alone. The logging filters
use the documented [worker-pool resource](https://docs.cloud.google.com/monitoring/api/resources#tag_cloud_run_worker_pool)
and [automated-backup audit fields](https://docs.cloud.google.com/sql/docs/postgres/backup-recovery/view-audit-logs-for-automated-backups).
Actual log shapes, delivery, IAM and latency require deployed qualification.

Synthetic mock-test calibration: engineering owns the 1-unknown/64-hold triggers,
0.8 budget warning, 500-ms loop probe, 1,000-ms DB phase, 15-second sample age,
120-second heartbeat absence and 600-second notification rate limit. They test
warning before a 1.0 budget limit, truncation detection, and rate-limited
notification routing with a five-second sample interval. They are not approved
production thresholds; calibrate against the qualified workload and operational
response time before deployment. The loop metric is timer overshoot, not a full
interval maximum or an SLO histogram. Private probes and actual scrape/log access
must be verified; never publish the status endpoint on a public service.
