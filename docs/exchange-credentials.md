# Exchange credential encryption

A1–A4a implement identity/audit, encrypted storage/providers and managed Set/load
integration. Automatic Disconnect and cleanup recovery remain A4b work. Environment
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
explicit. Key replacement preserves positions and ledger; automatic cancel/close on
Disconnect is not enabled until A4b has its own engine/recovery acceptance.

Already loaded versions avoid another KMS call when ordinary Start follows Stop.
Cold loads use bounded worker work and read-only account validation. Missing KEK or
provider failure keeps administration alive with trading blocked; after repairing
deployment configuration, restart to retry loading. Current status uses matching fresh
owner evidence, while the original failed receipt remains historical evidence.

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
