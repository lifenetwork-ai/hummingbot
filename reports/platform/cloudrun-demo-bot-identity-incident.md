# Cloud Run Demo startup identity conflict — 2026-11-09

Project `lifeai-438107`, region `asia-southeast1`, service `hummingbot-demo`.
Investigation uses read-only Cloud Logging, service metadata and PostgreSQL.
The operator subsequently approved the single-variable service correction below;
no database mutation was performed.

## Confirmed cause

Revisions `hummingbot-demo-00043-v76` and `hummingbot-demo-00044-6wh` repeatedly
exit with `ControlConflict`, mapped to generic `service_internal_failure` and
engine exit1. The public startup probe then returns503 `api_initializing` until
its60-failure limit. The configured8080 ingress is not the primary failure.

The deployed environment says `BOT_ID=okx-demo-btc`, while `public.bot_state` in
database `hummingbot_demo` contains the existing identity `hummingbot-demo`.
`BotStateStore.ensure()` intentionally rejects a different identity before
ownership/bootstrap can complete. This is a configuration mismatch, not an
ownership takeover or a reason to delete the state row.

At the read-only observation, desired state is stopped, control/ack0/0,
config/type null; counts are orders0, fills0, positions0, credentials0 and admin
accounts0. The transaction setting `transaction_read_only=on` was verified.
The Cloud SQL Auth Proxy was bound only to local loopback and used the operator's
existing authenticated gcloud identity. No authorized-network/IAM changes were
needed. No exchange requests or order submissions were made.

The private deployment env initially reused the restored local Demo ledger's
`okx-demo-btc` identity. That assumption does not match this freshly initialized
cloud database. Its local `BOT_ID` is now corrected to `hummingbot-demo`; all
other private env values are preserved. No local Demo ledger identity is changed.

## Concrete service-only correction

The operator approved updating only the service environment identity, preserving
image, secrets, resource limits, routing, scaling and trading desired state:

```sh
gcloud run services update hummingbot-demo \
  --project=lifeai-438107 --region=asia-southeast1 \
  --update-env-vars=BOT_ID=hummingbot-demo
```

Then verify the new revision's Ready/startup state, bootstrap/owner logs and
unchanged stopped DB control/version and empty financial ledger. Do not Start,
Set exchange keys, clear HALT or modify bot-state identity/history as part of
this correction. If a new error appears, retain its separate cause.

The service currently uses2vCPU/1Gi and `ENV=dev`. These differ from the proposed
2vCPU/2Gi and production preset, but there is no evidence that either setting
caused the observed identity conflict. This correction leaves both unchanged.
The deployed image tag references source `063b93756`; image validation and prior
local release/soak evidence keep their existing scopes.

## Diagnostic source correction

The DB fence is correct. The operational defect is that a permanent identity
conflict is indistinguishable from transient control conflicts at the service
boundary, so the engine repeatedly restarts without an actionable reason.

TDD source correction gives this existing guard the stable
`bot_identity_mismatch` code, maps only that conflict to permanent configuration
exit78 and reuses the safe failure-reason projection at the engine boundary.
Generic/control-version/session conflicts still return retryable exit1. The
guard, dedicated-DB rule, advisory identity lock, schema and financial rows remain
unchanged; raw exception/driver messages are not exposed.

Three Red checks reproduce missing permanent classification, the generic engine
log and missing store error identity. Green:94 related native tests and11
PostgreSQL control tests pass (546 unrelated PG cases deselected). The PG test
also verifies the rejected identity leaves all stored state unchanged and creates
no extra bot row; the subprocess logger test verifies safe JSON reason/severity
and no private exception text. These use disposable local tests, not the cloud DB.
This source change is not built/deployed yet and is not needed to correct the
currently deployed service's `BOT_ID`.

## Approved service correction and current status

The approved update completed as revision `hummingbot-demo-00045-p7l`, serving
100% of traffic with the same image/resource/auth configuration. After the
operator's pause for a newer code deployment, the subsequent status/log request
authorized independent read-only verification: service Ready/ConfigurationsReady/
RoutesReady are true; `/startup`200 stopped, `/live`200 api_running, `/ready`503
stopped. Authenticated admin status confirms desired stopped/running false,
error null, owner available and control/ack0/0. Readiness503 is expected before
Start; no trade/credential setup or cloud resource increase was performed.

## Why INFO application logs are absent

The project's `_Default` sink has an enabled exclusion named
`drop-cloudrun-below-warning`, matching Cloud Run records below WARNING except
several existing service exceptions. `hummingbot-demo` is not exempt. Its
application `LOG_LEVEL` is omitted and defaults to INFO; current engine logs are
filtered by ingestion routing, while Cloud Audit lifecycle events still appear.
Changing the viewer severity cannot restore excluded records.

Prepared, not yet approved/applied: preserve the original exclusion and append
an exception **only for this service's application stdout/stderr**:

```text
resource.labels.service_name="hummingbot-demo"
AND (LOG_ID("run.googleapis.com/stdout") OR LOG_ID("run.googleapis.com/stderr"))
```

All other service exclusions and this service's HTTP request-log filtering stay
unchanged. This needs no new service revision or engine/DB change; it applies to
future logs. The exact old/new filter is retained privately for review. Routing
and exclusion behavior follows [Cloud Logging routing](https://docs.cloud.google.com/logging/docs/routing/overview).
