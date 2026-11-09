# Cloud Run Demo startup identity conflict — 2026-11-09

Project `lifeai-438107`, region `asia-southeast1`, service `hummingbot-demo`.
Investigation uses read-only Cloud Logging, service metadata and PostgreSQL;
no cloud deployment or database mutation has been performed at this point.

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

After operator approval, update only the service environment identity, preserving
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

The service-only update remains pending operator approval. The existing image
can start with the correct configured identity. No trade/credential setup or
cloud resource increase is part of the proposed correction.
