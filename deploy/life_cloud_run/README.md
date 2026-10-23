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
pinned definition with zero worker allocation; await the migration Job when
configured; apply one unarmed worker allocation. It never runs from a worker,
uses no shell, stops on a failed/ambiguous step, and rejects changed Terraform,
provider-lock or variable inputs before the next step. Tests replace subprocess
execution with a recording port; this command has **not** been run against GCP.
A disabled migration still requires native startup schema compatibility.

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

Build `Dockerfile` with `--target life-worker --build-arg LIFE_POSTGRES=true` and
an explicit Linux platform. It runs UID/GID 10001, the foreground worker as the
entry point, and no TTY or shell. Runtime config/secrets are mounted/injected;
`.env`, host extensions and bytecode stay outside the build context. The ordinary
Docker image default remains the existing `release` command. Linux AMD64 build,
full dependency reproducibility and container signal/kill acceptance are not yet
qualified by these source definitions or the host process tests.

## Remaining operational work

Lifecycle/DB/local-readiness/event-loop-lag telemetry and bounded local native
pool-pressure, physical outcome/uncertainty counters and recent phase timings
are implemented. Unknown counters count historical events, not unresolved funds.
Complete bounded unknown/hold/budget/audit/backup metrics, calibrated alerts,
full image tests and all PG8 gates remain open. A status response is never a
permission to trade. Private probe exposure and actual scrape/log access must be
verified before deployment; do not publish the status endpoint on a public service.
