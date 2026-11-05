# Exchange credential encryption

A1–A6 implement identity/audit, encrypted storage/providers, managed Set/load and
automatic Disconnect with durable cleanup recovery. Local/offline acceptance is complete;
real Google/GCP and reviewed existing-Demo cutover remain open. Environment
credentials remain the default; managed mode must be explicitly selected. See the [implementation status](../reports/implementation/ADMIN_ACCESS_CREDENTIALS_PROGRESS.md).

Strategy YAML and routing stay deployment-owned. An exchange adapter validates
credential fields and account identity; the shared encryption format covers the bot,
exchange, connector, environment, account binding and credential version. API responses
never expose an envelope, wrapped data key, KEK or saved credential values.

## Source and provider configuration

`EXCHANGE_CREDENTIALS_SOURCE=environment` is the compatible default. The managed
source is `postgres_kms`, requiring PostgreSQL. `KMS_PROVIDER` accepts `local` or
`gcp`; there is no fallback between them or to environment/file credentials.

Local configuration:

```dotenv
EXCHANGE_CREDENTIALS_SOURCE=postgres_kms
KMS_PROVIDER=local
KMS_LOCAL_KEY_ID=local-v1
KMS_LOCAL_SECRET_KEY=<base64-encoded random 32-byte key>
```

Generate and retain a random key in private deployment settings; for example,
`openssl rand -base64 32`. Do not commit its output or generate a replacement at
every process start. The encryption key must remain available across restarts.
An invalid/missing key is a configuration error, never permission to regenerate it.

`OKX_TRADING_ENV=demo|live` selects exchange routing independently of this source
and provider. Cloud Run can use `demo` with `postgres_kms` and `local`; retain the
same private KEK across revisions. The API cannot change these deployment settings.

A managed database remains bound to its original exchange/environment/account,
including after Disconnect. Changing `OKX_TRADING_ENV` from Demo to Live does not
convert its ledger or authorize using Demo keys. Missing Live credentials block
trading, even when usable environment keys are present; attaching a Live key to
that Demo database returns `409 exchange_account_mismatch`. Use a separate bot and
database for independent Live operation, or a separately reviewed cutover. Returning
to Demo restores the original binding; a preserved running intent can resume, while
a stopped bot stays stopped.

With a wrong valid-length local KEK, cold loading blocks trading and keeps admin
health available without replacing ciphertext or falling back to environment keys.
Restore the original KEK and restart; a previously stopped bot still needs Start.
The [cold-restart E2E report](../reports/release/admin-managed-environment-e2e.md)
documents the local checks. Run the same isolated scenario against a compatible image:

```sh
python scripts/test_e2e_offline.py --image hummingbot-cloudrun:local \
  --scenario managed-environment --trading-env demo
```

The scenario explicitly tests both routing modes using an internal-network simulator,
local encryption and Basic bootstrap. It is also included in `--scenario all`; it
does not contact OKX or require Google/GCP resources.

GCP configuration:

```dotenv
EXCHANGE_CREDENTIALS_SOURCE=postgres_kms
KMS_PROVIDER=gcp
KMS_GCP_KEY_RESOURCE=projects/PROJECT/locations/REGION/keyRings/RING/cryptoKeys/KEY
```

Use a symmetric ENCRYPT_DECRYPT CryptoKey and Application Default Credentials.
Grant the runtime identity encrypt/decrypt permission on that key; no downloaded
service-account key or plaintext KEK is required. The implementation uses official
Google Auth `AuthorizedSession` with the [Cloud KMS REST API](https://docs.cloud.google.com/kms/docs/reference/rest/v1/projects.locations.keyRings.cryptoKeys/encrypt).
The full KMS protobuf SDK collides with descriptors vendored by an existing connector,
so it is deliberately not a dependency. CRC32C and authenticated context protect
request/response integrity; calls have time bounds and reject redirects/raw error bodies.
Local/offline tests exercise a simulated transport; real GCP acceptance remains open.

## Encryption and lifecycle

Each save gets a fresh random 32-byte DEK and AES-256-GCM nonce. The KEK wraps the
DEK with a separate authenticated purpose. Provider/key-version metadata is also
authenticated by the payload, so changing its reference cannot silently succeed.
Only the envelope and required metadata enter PostgreSQL. Revocation ultimately
sets the envelope to SQL NULL while retaining account/version/audit metadata.

KMS is used for save/load, never per quote, order or health probe. Already loaded,
authorized connectors can continue through a temporary provider outage. New loads
must remain blocked when decryption cannot be verified.

## Managed Set and runtime loading

`POST /api/v1/admin/credentials/okx/{connector}` accepts a complete input-only
`credentials` object (`api_key`, `secret_key`, `passphrase`), non-negative strict
`expected_version` and `expected_control_version`, plus `Idempotency-Key`.
Only admin/write users may submit. The bot must be stopped and its current control
version acknowledged, with no unresolved mutation/runtime or recovery HALT.

A fresh read-only account-config request verifies permissions and request `uid`
(rather than only its parent `mainUid`). The retained account binding includes the
deployment site (`site:uid`); Demo/live scope and companion slots must agree.
Perpetual setup requires the existing HEDGE/CROSS MM configuration and Futures or
multi-currency Hedge account. See [OKX account configuration](https://www.okx.com/docs-v5/en/#trading-account-rest-api-get-account-configuration).

The existing ledger cannot be adopted silently by the first managed key. A database
with history but no verified managed account binding needs operator-reviewed cutover.
Do not clear its history or HALT to bypass `account_binding_required`.

Read `/credentials`, `/credentials/okx` and `/credentials/operations/{id}` for input
metadata, safe status and progress. The typed receipt distinguishes accepted,
loading and loaded/failed; ordinary trading command history remains unchanged.
Set increments existing control authority, commits ciphertext/audit/receipt together,
detaches old readers/connector models and verifies the loaded connection. Start stays
explicit. Ordinary key replacement preserves positions and ledger. Disconnect is a
separate action with automatic cancellation and perpetual position closure.

Already loaded versions avoid another KMS call when ordinary Start follows Stop.
Cold loads use bounded worker work and read-only account validation. Missing KEK or
provider failure keeps administration alive with trading blocked; after repairing
deployment configuration, restart to retry loading. Current status uses matching fresh
owner evidence, while the original failed receipt remains historical evidence.

## Disconnect and recovery

`POST /api/v1/admin/credentials/{exchange}/{connector}/revoke` takes strict
`expected_version` (credential version), `expected_control_version` and
`Idempotency-Key`. It works without an MM session. It immediately fences ordinary
orders for the entire bot; it does not immediately delete the encrypted bundle.

The existing engine owner stops the strategy, preserves its checkpoints, reloads
existing connectors solely for cleanup and verifies the same exchange account.
It cancels recorded bot orders across both markets, reads back terminal outcomes,
checks held exposure against fresh exchange positions, then closes managed
perpetual LONG and SHORT separately with narrowly scoped market CLOSE orders.
This can incur taker fees and slippage. Spot holdings and unrelated positions are
not sold. Unsupported NET/isolated modes, unmatched exposure, unknown bot orders,
missing tracking state and uncloseable residuals require technical review.

Every identified close fill reduces its saved position in the same transaction as
the fill ledger. Duplicate trade IDs cannot apply twice; cumulative quantity cannot
exceed the admitted close. A lost placement response is read back, never blindly
resubmitted. A cancel acknowledgement or an empty local tracker does not prove
cleanup. Selected credentials remain encrypted and available solely for cleanup
until terminal orders, flat managed perpetual exposure, committed ledger and
detachment of authenticated consumers have all been confirmed.

Only then does one fenced transaction set the selected envelope to SQL NULL,
complete the operation and audit, and leave the bot stopped. Companion credentials,
account binding, order/fill history and unrelated recovery HALTs are retained.
The API key remains valid at OKX until its owner deletes it directly on OKX.
Do not delete the key there while automatic cleanup still needs it.

Read the root receipt at `/credentials/operations/{id}`. `202` is acceptance,
not completion. A scheduled `waiting_for_dependency` retries automatically;
`action_required` keeps the blocker and requires intervention. Two write/admin
actions continue the **same** root operation and control fence:

- `POST /credentials/operations/{id}/retry`: expected credential, control and
  `expected_operation_version` from the latest root progress, plus a new idempotency key.
- `POST /credentials/{exchange}/{connector}/cleanup-credentials`: the same versions,
  root `operation_id` and complete input-only credentials for the **same** account.
  An existing companion slot can also be repaired. This cannot add a new slot,
  bind another account, start trading or bypass an unknown previous send.

Poll the child receipt and its `cleanup_operation_id`. It completes with
`cleanup_completed` when the root finishes; this does not mean the repaired
companion key was removed. A superseded repair finishes as failed/replaced.
Accepted cleanup retains its original audited authority after logout or role
removal; new retry/repair requests recheck the actor's current role.

If a crashed runtime left an unverified checkpoint, automatic closure refuses to
infer missing exposure. Follow the reviewed [recovery runbook](postgres-runtime.md).
No API resets a risk HALT or fabricates a flat position. A cold load still needs
its KEK; a loaded matching version can clean up through a temporary KMS outage.

## Key or provider transition

1. Stop trading and confirm outstanding order cleanup before changing deployment keys.
   Keep the old key/provider available until the transition and backups are reviewed.
2. Change the provider/key settings through the deployment environment. Changing bytes
   or IDs is not an automatic rewrap operation and does not decrypt older envelopes.
3. Explicitly re-enter credentials for the same bound account into each affected
   managed slot using the new provider. Recheck all required slots and preflight;
   do not resume with an unreadable companion connection.
4. Start trading explicitly after verification. Do not clear unrelated recovery HALTs
   or create a different exchange-account binding to bypass a failure.

Old encrypted database backups may still need the old KEK. Python reference removal
does not guarantee physical zeroization. Runtime/deployment code with authorized key
access remains a trust boundary; encryption does not remove that authority.

## Local rollout and rollback

1. Start with an isolated database, a retained local KEK and explicit source/provider
   settings. Keep Demo and live in separate databases/deployments. Another independent
   bot also needs its own database and exchange subaccount; `BOT_ID` alone does not
   isolate the existing shared order/fill tables or ownership lock.
2. Keep ingress private or behind the approved gateway. Follow the
   [authentication and bootstrap flow](fe-integration.md#connection-and-openapi):
   use Basic only for explicit bootstrap, whitelist/bind a Google administrator,
   verify that account works, then disable Basic in environment settings and restart
   the complete service. Remove older reachable revisions with Basic still enabled.
3. Apply the idempotent migrations, verify stopped/acknowledged state, configure both
   required connection slots, then test Start/Stop before broader acceptance. A running
   environment-backed ledger requires a separately reviewed managed-account cutover;
   this work does not perform that cutover on the existing Demo database.
4. Back up database and key configuration together. Roll back only to a binary tested
   against the current schema; older strict schema validation may reject new tables
   or columns. Never drop audit/history, reset HALT, delete a pending cleanup or change
   account binding to force an older image to start. Database restore and any related
   position reconciliation require operator review.
5. Source/provider, auth, routing, whitelist bootstrap policy and strategy YAML remain
   deployment-owned. The API cannot change these settings. Do not switch back to
   environment credentials to evade an incomplete managed Disconnect.

Swagger/OpenAPI HTTP routes remain authenticated and available only under the
existing development `ENV` policy (`dev` default; `DEV`/`development` accepted).
Deploy production with an explicit non-development `ENV`. The committed schema and
`make swagger-check` remain available to maintainers without enabling runtime docs.

Future venues use the existing adapter/registry boundary for input validation,
identity and cleanup capability. They must implement and verify their own native
cancel/close/readback and final-send rules before advertising Disconnect. A generic
credential row or input descriptor alone does not add trading support for that venue.
