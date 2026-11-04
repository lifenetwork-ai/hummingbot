# Exchange credential encryption

A3 implements the encrypted-storage/provider foundation. Managed credential API
and engine integration are A4 work; until then trading continues to use the existing
environment credential path. See the [implementation status](../reports/implementation/ADMIN_ACCESS_CREDENTIALS_PROGRESS.md).

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
