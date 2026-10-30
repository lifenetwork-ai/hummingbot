# Cloud Run deployment readiness review

Date: 2026-10-29. Scope: deployment template, offline review tool, Make target
and operator/FE documentation. No cloud APIs, pushes, deployments, account
mutations or trading were performed. Project/region/DB/registry/secret identities
and the FE access route were requested from the operator; a concrete deployment
configuration is pending those inputs.

Current status (2026-10-30): the local template/review work is complete. Actual
cloud work below is deferred while the operator prioritizes local verification;
its open items require deployment inputs and cloud/FE access. They are outside
the completed [local runtime acceptance](mm-service-supervision.md), and local
test passes do not close them.

## Completed locally

- [x] Inspect the runtime defaults, service health/state behavior, credential
  loader, packaged configuration and existing release checks.
- [x] Make exchange routing and enabled admin API explicit in the production
  template; add pinned admin and perpetual secret references.
- [x] Require explicit PostgreSQL, production documentation mode, exchange routing
  and enabled admin API in production deployment review.
- [x] Validate pinned secret references for admin and both Live/Demo connectors;
  reject duplicate environment names and incomplete credential trios. Preserve
  spot-only and perpetual-only deployments.
- [x] Require and forward `CLOUDRUN_PROJECT` in the deploy target, independent of
  the workstation's active gcloud project.
- [x] Add the worksheet/stopped-startup/cloud/FE/first-session checklist in the
  existing [Cloud Run guide](../docs/cloudrun.md#first-deployment-checklist), with
  gateway IAM/Basic Auth guidance in the [FE guide](../docs/fe-integration.md).
- [x] Pass 55 deployment/packaging/deploy tests, template review, a concrete
  offline example bound to the actual completed E2E manifest, and Swagger drift.

## Findings and corrections

The original template left admin API disabled by default and omitted its auth
settings. It also relied on the runtime's Live exchange default. This was a
configuration readiness gap, not a missing API implementation. It now declares
`ADMIN_API_ENABLED=true`, admin secret references and `OKX_TRADING_ENV=live`.
Operators must review that exchange choice and use matching Demo names if needed.
`ENV=production` closes HTTP Swagger but does not select Demo versus Live.

The review tool previously accepted missing operating-mode/admin settings and
ignored Demo/admin secret pinning and duplicate variable names. Plaintext Live
credential cases could fail with a KeyError rather than a deliberate review
error. New checks produce safe validation failures. PostgreSQL is now explicit
in the review: the actual runtime already selects PostgreSQL through `K_SERVICE`
or a supplied database URL, so omission did not cause Cloud Run file fallback.

The Make deployment command previously used the active gcloud project implicitly.
A controlled test replaces gcloud with a local argument-capture executable: empty
project must stop before invocation; a supplied project must be forwarded. These
two tests failed before the Make correction and pass afterwards. No real gcloud
was invoked by these tests.

First deployment into a fresh dedicated DB starts stopped. Reusing a DB preserves
its old running intent and can resume trading during startup. The guide now
requires verifying/persisting stopped intent before a reused-DB rollout; it does
not invent a new environment override, clear recovery state or alter session
restoration. Stopping orders retains positions.

Internal ingress, platform IAM and application Basic Auth are separate. A
server-to-server FE gateway can retain Basic Authorization while sending the
platform ID token in X-Serverless-Authorization. This route still needs network
and caller IAM provisioning. Browser CORS alone cannot enable it, and the bot's
existing CORS header list was not broadened.

## TDD and verification evidence

Raw local artifacts are ignored under `build/cloudrun-readiness/`:

| Artifact | Actual outcome |
|---|---|
| `red.log` | 21 contract failures before changes; also one positive Demo fixture setup failure because the old template had no exchange-mode variable |
| `green.log` | Initial 29 deployment tests pass after template/review corrections |
| `project-red.log` | Both missing/explicit-project invocation tests fail before Make correction |
| `focused-green.log` | Final 55 deployment, packaging and existing deploy tests pass |
| `swagger.log` | `make swagger-check` passes |
| `offline-example.yaml` | Dummy resource/secret names only; review passes against the real complete E2E manifest; not a deployable cloud configuration |

The referenced existing E2E result is
`build/e2e-offline/20261029-005631-e305e0/result.json`, image `be3efe46…`, manifest
`sha256:c8cd00a5a9167f61c57ad28e6d67ea76028a742ac512995f3a861becfc49e1b5`.
Reviewing its digest does not establish remote-registry availability.
Application runtime code and packaged trading YAML were not changed; no new image,
PostgreSQL regression, release soak or exchange test is claimed by this review.
The completed [availability release evidence](life-mm-availability-remediation.md)
retains its original runtime scope.

## Pending actual cloud work

Deferred external checklist. Owner: deployment operator, with the separate FE
repository for browser/gateway acceptance. These items are intentionally open;
they do not indicate unfinished backend fixes or authorize a deployment.

- [ ] Fill the operator worksheet and produce/review the concrete service YAML.
- [ ] Verify remote image manifest, service account, secret versions, SQL roles,
  DB backups/major/connection budget, IAM/ingress and exchange egress.
- [ ] Apply the reviewed revision and verify stopped service/ownership/logs.
- [ ] Complete browser/gateway acceptance with the FE repository.
- [ ] Separately review the listing catalogue/budget/instrument and authorize the
  first trading session. No approved LIFE listing parameters are supplied here.

These items remain open rather than being inferred from local tests. Current
Google behavior was checked against official documentation for
[secrets](https://docs.cloud.google.com/run/docs/configuring/services/secrets),
[Cloud SQL](https://docs.cloud.google.com/sql/docs/postgres/connect-run),
[CPU/billing](https://docs.cloud.google.com/run/docs/configuring/billing-settings),
[health probes](https://docs.cloud.google.com/run/docs/configuring/healthchecks),
[ingress](https://docs.cloud.google.com/run/docs/securing/ingress) and
[two-header service authentication](https://docs.cloud.google.com/run/docs/authenticating/service-to-service).
Cloud availability and database permissions still need actual verification.
