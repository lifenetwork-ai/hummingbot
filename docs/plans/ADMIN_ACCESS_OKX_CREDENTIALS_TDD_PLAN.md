# Admin access and encrypted OKX credentials — TDD implementation plan

Status: requirements reviewed on 2026-11-05 (Asia/Saigon); implementation has
**not started**. This document describes proposed behavior. Current endpoints
and deployment behavior remain documented in [FE integration](../fe-integration.md).

## Agreed scope and review verdict

The requirements are compatible with the current architecture. Keep one Cloud
Run service, the existing API/engine processes, PostgreSQL ownership/recovery,
Hummingbot connectors and YAML strategy configuration. Add a small authentication,
authorization and credential-storage layer; no new web framework, identity server,
Redis, broker, signing service or exchange-account administration service.

- [x] Confirm optional Basic Auth for initial administration, disabled explicitly
  by environment after whitelisted Google accounts can administer the service.
- [x] Confirm roles `admin`, `write`, `read`; `admin` and `write` can set/revoke
  OKX credentials, while only `admin` manages the whitelist and roles.
- [x] Confirm credentials encrypted in PostgreSQL, a KEK supplied by
  `KMS_PROVIDER=local|gcp`, and no API that reveals saved secret material.
- [x] Confirm `local` loads its KEK from environment; `gcp` uses Cloud KMS.
- [x] Confirm user clarification: revoke disconnects credentials **inside this
  bot**. Deleting the API key on OKX remains an operator action on OKX.
- [x] Require durable audit history and an authenticated, filterable audit API.
- [x] Require clear English errors and operator status: what happened, current
  impact on trading, whether recovery is automatic, and the next permitted action.

Google sign-in follows the original Gmail/Google-account requirement. Whitelisting
an email does not authenticate a request. The backend must verify Google identity
and apply its own persisted role. `write` deliberately includes credential control;
grant it only to operators trusted to stop or change the exchange connection.

Disabling Basic Auth removes the shared-password login path. It does not disable
the `admin` role or the API, and does not eliminate risks from a compromised Google
account, deployment pipeline or runtime. Local KEK mode exposes key material to
the process environment; GCP mode restricts key use through KMS IAM, but authorized
runtime code still obtains decrypted credentials. Neither mode promises protection
against arbitrary malicious code deployed with the bot's permissions.

## Existing code to reuse and necessary changes

| Existing component | Reuse / change |
| --- | --- |
| `admin_config.py`, defaults and dotenv loader | Add conditional authentication/provider configuration; currently enabling API unconditionally requires Basic credentials |
| `admin_api.py` middleware, handler metadata, errors and CORS | Verify Basic or Google identity, attach an authenticated principal and enforce explicit route permissions; do not infer authorization from HTTP method alone |
| `AdminStore`, `admin_commands`, control-version transactions | Reuse receipts, pagination, locks and Stop workflow; secret requests need a dedicated adapter because generic command payloads are persisted |
| `Security`, `environment_credentials.py`, connector Pydantic models | Keep existing environment mode; add a managed provider feeding the same in-memory connector models without writing YAML or `.env` |
| `BotOwnership.authorize_rest`, service lifecycle and recovery | Fence credential changes through existing desired-state/control-version checks; reuse cancellation, reconciliation and restart handling |
| Funding observer, reconciliation and CLI preflight | Audit all credential consumers; a revoked key must not survive through an environment-only reader or an idle observer |
| `mm_events` | Keep session history intact; its mandatory session identity makes it unsuitable as the general security audit table |
| Existing cryptography package, Google libraries | Reuse authenticated encryption; add official Google identity/KMS clients where needed, and update runtime packaging |

This creates one explicit future exception to the current configuration contract:
managed **OKX credentials** become API-editable encrypted DB data. Strategy YAML,
exchange live/demo routing, auth flags, KEK/provider settings and other environment
settings remain deployment-owned. Existing documentation must not claim these
new endpoints work before implementation and verification finish.

## Authentication, roles and bootstrap shutdown

| Capability | Basic admin, when enabled | Google `admin` | Google `write` | Google `read` |
| --- | --- | --- | --- | --- |
| Read bot status/history/credential status/audit | Yes | Yes | Yes | Yes |
| Start/switch/stop and existing operational mutations | Yes | Yes | Yes | No |
| Set or revoke managed OKX credentials | Yes | Yes | Yes | No |
| List/change whitelist, role or account access | Yes | Yes | No | No |
| Change auth/provider env or strategy YAML through API | No | No | No | No |
| Read plaintext credentials, KEK or encrypted blobs through API | No | No | No | No |

Basic is one shared bootstrap principal, audited as `basic:<configured username>`;
it cannot prove which human used that password. Google actions record stable
subject identity plus the verified email snapshot. Never trust actor/role/email
headers or actor fields supplied in a request body. Audit readers see safe actor
metadata, not the complete whitelist or identity tokens.

Use official Google token verification: signature, allowed issuer, configured
audience, expiry, subject and verified email. Bind a pre-whitelisted Gmail or
Google Workspace address to `iss/sub` on its first verified sign-in. Preserve
that binding; an email change or a different subject cannot silently claim an
existing account. Non-Google-hosted email ownership needs separate verification
and is outside the initial email-onboarding flow. No public signup or token issuer
is added. The FE obtains a Google ID token and sends `Authorization: Bearer ...`.

Read the current active account/role from PostgreSQL for each authenticated API
request; do not cache writable permissions across revocation. Recheck authorization
inside mutation transactions after any external/KMS work. Public Google signing
keys may use the official bounded HTTP cache; token verification/network work must
not block the event loop or consume the Stop-command lane indefinitely. Use bounded
work and timeouts, not a network call to Google for every normal request.

### Proposed environment contract

| Variable | Default / behavior |
| --- | --- |
| `ADMIN_API_ENABLED` | Existing master API switch, default `false` |
| `ADMIN_BASIC_AUTH_ENABLED` | New, default `false`; explicitly enable for bootstrap or a reviewed legacy deployment |
| `ADMIN_API_USERNAME`, `ADMIN_API_PASSWORD` | Existing names; required only when Basic is enabled; never authorize Basic when its flag is false |
| `ADMIN_GOOGLE_AUTH_ENABLED` | New, default `false`; enable for whitelisted Google users |
| `ADMIN_GOOGLE_CLIENT_ID` | Required with Google auth; no usable default audience |
| `OKX_CREDENTIALS_SOURCE` | `environment` by default for existing setups; managed mode is `postgres_kms` |
| `KMS_PROVIDER` | `local` by default; valid values **only** `local`, `gcp`; used when source is `postgres_kms` |
| `KMS_LOCAL_SECRET_KEY` | Required in local mode: base64-encoded random 32-byte KEK; no generated/default production key |
| `KMS_LOCAL_KEY_ID` | Stable non-secret identifier, default `local-v1`; must identify the actual configured KEK |
| `KMS_GCP_KEY_RESOURCE` | Required in GCP mode: approved Cloud KMS symmetric encryption CryptoKey resource; no fallback |

All flags use the existing strict boolean parser and environment precedence.
Unknown provider/source values fail validation. Managed source and Google whitelist
authentication require PostgreSQL.
Enabled API must have at least one enabled authentication method. Missing fields
for a disabled method are not errors. Existing Basic-only deployments/tests must
explicitly set the new Basic flag before upgrading; do not silently break their
rollout or infer Basic activation from a populated password.

Bootstrap sequence:

1. Explicitly enable Basic and Google auth in deployment configuration.
2. Whitelist at least one Google `admin`; that account signs in successfully,
   becomes bound to its verified subject and checks its admin permissions.
3. Deploy/restart with `ADMIN_BASIC_AUTH_ENABLED=false`. Keep the API and Google
   auth enabled. Basic requests now fail even if the old password remains present.
4. Retire every old process/revision and tagged route that still permits Basic;
   test the actual exposed routes with the old password. Remove the unused password
   reference from the active deployment after verification.

Always prevent disabling/demoting/removing the last active, bound Google admin
once onboarding is complete. Serialize competing changes in PostgreSQL so two
admins cannot concurrently remove each other. Pending email invitations do not
count as functioning admins. Provide a bootstrap-readiness check for the rollout.
An invalid Google-only/no-admin deployment must remain fail-closed with a clear
configuration error; it must never automatically re-enable Basic. Recovery is an
explicit deployment change by an authorized operator using a fresh bootstrap
password rather than reactivating a possibly exposed old one. Temporary auth-DB/JWKS
failure rejects affected requests; it must not itself kill a healthy trading engine.

## Encrypted storage and provider boundaries

Use envelope encryption with existing `cryptography` primitives: a fresh random
32-byte data key (DEK) encrypts the complete API-key/secret/passphrase bundle with
AES-256-GCM and a fresh nonce. The KEK wraps the DEK. Authenticate format version,
bot ID, connector, live/demo environment and credential version as associated data
so moving ciphertext to another scope/version fails authentication.

- `local`: wrap the DEK with the configured KEK using authenticated encryption,
  a separate nonce and purpose-bound associated data. KEK stays in environment,
  never PostgreSQL, receipts, audit, YAML, API responses or logs.
- `gcp`: wrap/unwrap through Cloud KMS using Application Default Credentials and
  narrow key IAM. Store the actual key-version reference returned by KMS. No
  downloaded service-account private key or export of the KMS KEK is required.
- Persist an envelope format/provider/key identifier, ciphertext, wrapped DEK
  and the required nonces/metadata. No API exposes any of these cryptographic blobs.
- Local KEK must survive restarts. Changing its bytes, deleting a required GCP
  key version or switching provider is **not** automatic key migration. Fail with
  a recoverable credential-unavailable reason; never regenerate a KEK, overwrite
  ciphertext or fall back to environment credentials.
- For this small initial scope, provider/key changes require a controlled Stop
  and explicit credential re-entry/re-encryption with the new provider, preserving
  audit and exchange-account binding. Keep the old key until the transition and
  backups are reviewed. A general bulk rewrap/keyring service is out of scope.

Keep new state small: `admin_accounts` (whitelist, bound identity, role, active
status, version), `okx_credentials` (one current envelope/version per bot,
connector and environment) and `admin_audit_events` (append-only safe events).
Use existing `admin_commands` for safe receipts; reuse current bot control-version
fencing rather than introducing another ownership/lease mechanism. Allocate the
next available migration number when implementing. SQL must be idempotent,
snake_case, unqualified by `public.`, and use UTC `timestamptz` consistently.

No request containing a secret may pass into generic command-payload persistence,
exception serialization, traces, audit payloads or SQL parameter logging. Mutation
receipts contain only safe action/scope/version/state metadata. Deduplicate by the
existing idempotency key: the first committed operation wins; retries return its
receipt and never replace it with a newly submitted secret. Changed non-secret
scope/version metadata with the same key is a conflict. Document that a genuinely
new credential submission requires a new operation key; do not persist a plaintext
body or secret-derived request hash to implement retries.

## Credential lifecycle and availability

Expose requested versus observed state. `configured=true` means an encrypted
credential bundle is saved and permitted, not that OKX connectivity or recovery
has succeeded. Status includes safe credential version, validation/runtime state,
operation ID, changed-at/changed-by and an actionable English reason.

**Set/replace:** initially require the bot to be stopped with acknowledged cleanup
and no pending exchange mutation; otherwise return `409 bot_must_be_stopped` and
direct the caller to the existing Stop flow. Encrypt off the event loop before
the short transaction, then recheck actor, state and expected versions. Atomically
save the envelope, bump existing control authority, insert the safe receipt and
audit event. Existing owner lifecycle detaches old authenticated clients and
reloads the provider; commands cannot use a new version until observation/preflight
confirms it. Do not hot-swap an already initialized connector or auto-resume trading.

Preflight checks the selected environment and actual exchange-account identity.
Changing a key must not silently attach existing fills/positions to another OKX
account. Existing ledgers without a verified account binding need explicit reviewed
binding during cutover; do not infer identity from `BOT_ID` or an env label. A wrong
key leaves the API available with a clear validation error, without restoring an
old revoked credential behind the operator's back. Spot/perpetual/both retain the
existing shared-account and recovery rules.

### Replacing a key versus creating another bot

| Operator intent | Planned behavior |
| --- | --- |
| New API key for the same verified OKX account/subaccount and environment | Keep the same bot, DB, ledger and account binding; increment credential version, preflight and recover after Stop |
| New key belongs to a different account/subaccount | Refuse activation with `exchange_account_mismatch`; do not relabel or clear existing history/positions |
| Change Demo to live | Environment/deployment change with a separate live database/account binding, not a credential API override |
| Start an additional independent bot | Separate deployment, stable `BOT_ID`, dedicated PostgreSQL database and exchange subaccount are the supported simple isolation model |

The current `BotStateStore.ensure` rejects a database already owned by another
`BOT_ID`, the ownership advisory-lock key is database-wide, and core order/fill/
position tables are not a multi-bot tenant partition. Merely changing `BOT_ID` or
adding a key row cannot create an isolated second bot in that database. The code
and container image can be reused; do not copy the first bot's operational ledger.
Concurrent bots sharing one exchange account can also confuse order/position
recovery, so that is not approved by this plan. A multi-bot dashboard/orchestrator
inside one deployment would be a separate scope, not an automatic consequence of
supporting credential replacement.

Derive account binding from authenticated exchange responses, including the actual
subaccount identity rather than only a common parent account. Spot and perpetual
credential slots must satisfy the existing same-account contract when used jointly.
Audit rotation records the old/new credential version and safe account binding,
never either key. Replacing a credential does not revoke the old API key at OKX.

**Revoke, as confirmed by the user:** atomically mark the selected credential as
revoking, request a bot-wide Stop, bump control authority and record the actor and
receipt. This simple first version stops the joint bot even if only one connector
is revoked. New risk is denied at the existing final send gates. Requests already
sent before the fence may still complete and must be reconciled; receipt acceptance
does not claim instantaneous cancellation on OKX.

Cancellation is automatic, not an instruction for the operator to cancel normally.
The owner must discover and cancel every remaining bot-owned order across the
stopped bot's spot/perpetual markets, including recovered orders and late exchange
acknowledgements. A cancel request being accepted is not proof of cancellation:
reconcile each order's terminal outcome and any fills racing with cancellation.
An empty in-memory order list alone is insufficient. Do not cancel unrelated manual
orders or another bot's orders; unclear ownership blocks completion with an explicit
reason. Reuse existing order tracking, cancellation and reconciliation facilities.

Show automatic progress as "Stopping new orders", then "Cancelling pending orders"
with confirmed remaining/unknown counts and last observation time. Transient errors
use bounded automatic retries with the next retry time where known. Only unresolved
cleanup or a failure requiring intervention shows "Pending orders need attention",
the affected orders and the reason. Manual OKX cancellation is the fallback, not
the normal Disconnect workflow. Recheck exchange evidence after manual intervention;
an operator assertion alone must not mark cleanup complete.

The existing owner may use the old in-memory credential only for bounded cancellation
and necessary cleanup reads. Then detach **all** consumers, including idle funding
and reconciliation readers; erase the current encrypted bundle/wrapped DEK and
mark revoked after observation confirms detachment. Keep safe audit/version records.
Python reference removal is not guaranteed physical memory zeroization, and database
backups may retain older encrypted rows. No silent reload from `.env` is permitted.
Revoke admission does not require KMS access.

If cleanup times out, ownership is unavailable, an in-flight order is unresolved
or restart interrupts revocation, expose `cleanup_pending`/operator action instead
of reporting completed revocation. Admission remains closed; restart resumes only
the fenced cleanup workflow. Outstanding exchange orders/positions may need manual
OKX action. Revoke neither closes perpetual positions nor deletes the key on OKX.

KMS is used for set/load/unwrap, **not per order, tick, quote or health request**.
An already loaded, still-authorized connector should continue through a temporary
KMS outage. A cold start unable to decrypt stays idle with credentials unavailable
while keeping administration/diagnostics responsive. Reuse current bounded workers,
adding only a small bounded security/provider lane if necessary to keep slow KMS
or Google work out of the engine loop and Stop lane. Do not raise DB pools or
timeouts to hide blocking. No unbounded queues, extra/shared plaintext credential
caches or secret-bearing cross-process status messages; authorized connectors
necessarily retain credentials in their own memory while in use.

## Clear errors and operational status — required FE contract

Extend the existing `ErrorResponse`, `error_response`, `OperatorStatus`,
`ActionAvailability`, observation freshness and incident models. Do not add a
second error envelope or an independent source of trading/credential authority.
All human-facing labels, details and instructions are plain English. The main UI
must be understandable without knowing KMS, control versions or connector internals;
technical IDs and request references belong in an expandable Details view.

### Errors identify the problem and next action

Every handled rejection/failure must provide a stable machine-readable `code`,
safe English `detail`, server-generated `request_id`/`X-Request-ID`, and an explicit
existing `retry` instruction. Include safe `fields[].path` for input errors and
`Retry-After`/`retry_after_seconds` only when an actual backoff is known. Register
each new code in the current error/retry/message mapping: the helper currently
falls back to a generic error for unregistered codes, which must not erase the
specific cause. FE branches on codes and action metadata, never parsed prose.

| Synchronous HTTP result / proposed code | Operator meaning and guidance |
| --- | --- |
| 401 `authentication_required` / `token_expired` | “Sign in again to continue.” Use `reconnect`; do not infer the bot stopped |
| 401 `basic_auth_disabled` | “Password sign-in is disabled. Sign in with Google.” Use `reconnect`; never enable Basic automatically |
| 403 `account_not_allowed` / `account_disabled` / `insufficient_role` | “Your account cannot perform this action. Contact an administrator.” No automatic retry; use `contact_support` |
| 409 `last_admin_required` | “Keep at least one active Google administrator.” Use `fix_request` |
| 409 `bot_must_be_stopped` | “Stop the bot and wait for pending orders to finish cancelling before changing the connection.” Use `refresh_state` |
| 409 `credential_version_conflict` / `control_conflict` | “The connection or bot state changed. Refresh and review before continuing.” Use `refresh_state` |
| 409 `exchange_account_mismatch` | “This key belongs to a different OKX account. Use a key for this bot's account.” Use `fix_request`; never offer a reset-history bypass |
| 422 `validation_failed` / `okx_credentials_rejected` | Identify safe field paths or say “OKX did not accept the connection details. Check the API key, secret and passphrase.” Use `fix_request`; never echo supplied values |
| 429 `security_work_busy` | “The service is busy. Please wait before trying again.” Use `wait` and a bounded retry delay |
| 503 `kms_unavailable` / `database_unavailable` / `owner_unavailable` | Identify the unavailable dependency in safe language; choose `wait`, `same_request` or `contact_support` according to confirmed outcome and recovery behavior |
| 503 `credential_decryption_failed` / `kms_permission_denied` | “The saved connection cannot be loaded. Contact support.” Use `contact_support`; no destructive replacement, automatic fallback or blind retry loop |
| 500 `internal_error` | “The request could not be completed. Contact support with the request ID.” Preserve whether the mutation outcome is known |

For auth failures, reveal account-specific access reasons only after verifying
the identity; never expose whitelist membership for an arbitrary supplied email.
Raw OKX/KMS/SQL messages, stack traces, submitted field values, tokens and secrets
never appear in responses, notifications or audit details. Unknown failures still
return a safe explanation and request ID rather than an empty/generic UI message.
Distinguish confirmed invalid credentials from exchange timeout/unavailability:
a failed network check must not tell the operator that their key is wrong or
require them to replace it.

HTTP acceptance and runtime application are different. A `202` receipt means the
operation was accepted durably, not that credentials work or Disconnect completed.
A later validation/cleanup failure is represented in the receipt and operational
status with its stable code; a successful GET of that result may still be HTTP 200.
Include operation ID when known and distinguish confirmed not-applied, accepted
and unknown mutation outcome. A lost response/timeout must not produce a success
toast or a new mutation identity. FE queries the existing receipt/history and
preserves the original idempotency key if replay is needed.

### Status separates service, trading and the exchange connection

Keep the existing service/engine status alongside credential status: a reachable
API, saved credentials, a verified connection and active MM are different facts.
Credential projection uses the persisted version plus matching fresh owner evidence;
it is display information and cannot authorize a trade. Reuse existing freshness
fields and operator incidents instead of creating another scheduler/state machine.

| Proposed connection code | English label | Meaning and permitted next step |
| --- | --- | --- |
| `not_configured` | Not configured | No bundle has been configured; admin/write may configure it. A saved bundle that cannot be loaded is a failure, not missing configuration |
| `checking` | Checking connection | Saved bundle is being validated or loaded; show progress and wait |
| `connected` | Connected | Current credential version is verified/loaded with fresh runtime evidence; show the bot's separate running/stopped/guard state |
| `failed` | Connection failed | Validation rejected the key/account/environment or loading failed; explain the specific reason and offer the appropriate authorized action |
| `disconnecting` | Disconnecting | Stop/revoke accepted, new risk fenced, cancellation/detachment underway; show outstanding work |
| `action_required` | Action required | Cleanup outcome is unresolved or intervention is required; show the blocker and safe next action |
| `disconnected` | Disconnected | Current bundle removed and all consumers confirmed detached; API remains usable and positions may still be open |
| `unavailable` | Status unavailable | Current evidence is missing, stale or belongs to another owner/credential version; show last observation time without asserting Connected or Disconnected |

Each blocked/pending/failed view includes a reason code, clear `detail`/next action,
operation ID where relevant, last update/observation time, affected connector and
whether operator action is required. Include conditional automatic-recovery guidance
and scheduled retry time only when a retry actually exists. Long-running cleanup
must expose its last progress and blocker; no endless spinner, fabricated completion
percentage or invented recovery ETA.

Expose allowed actions using the existing `allowed`, `reason_code`, `detail`
pattern, combining the verified role with current domain preconditions. This covers
Configure, Disconnect, Start, Stop and account administration. A disabled button
explains why; backend enforcement remains authoritative. Do not unnecessarily hide
an authorized durable Stop merely because the engine is unavailable. Read-only
users get clear permission guidance rather than buttons which inevitably fail.

Temporary KMS trouble with an already loaded authorized connection is a dependency
warning, not a claim that MM stopped. Missing credentials on cold start show an
available administration service with trading blocked. Revocation cannot display
Disconnected while cancellation/detachment is pending. Logout, HTTP/network failure
or a stale observation cannot fabricate a stopped engine, zero pending orders or
closed positions. Surface retained exposure and its freshness using existing
order/position views; unknown values remain unknown.

Use “Disconnect OKX” for the user-facing action. Its confirmation says it stops
the bot, cancels its pending orders where confirmed, removes saved credentials,
leaves positions open and does not delete the API key on OKX. Audit, receipt,
status and request logs share operation/request IDs and consistent reason codes
so support can follow one action through request, processing and final outcome.

## Audit contract and proposed API

Audit business/security actions durably: explicit sign-in and denied access;
whitelist add/bind/disable and role changes; credential set/revoke requested,
applied, rejected and failed; and the existing start/switch/stop/reconciliation
commands with their actual authenticated actors. Record observed auth/provider
configuration at process startup without guessing who changed deployment env;
the deployment actor belongs to platform audit logs. Preserve existing MM events
and link their command/session IDs rather than rewriting history.

Each event has a stable ID, UTC database timestamp, bot scope, request/operation
ID, actor kind and stable ID, verified email snapshot where applicable, role at
action time, action, target/credential version, outcome, safe reason code and
allowlisted before/after metadata. Invalid tokens have an unknown/unverified actor;
never attribute their claimed email to a verified user. Retries and owner takeover
must not duplicate the same committed state-transition event.

Successful state changes and their audit event commit in the **same transaction**;
audit insertion failure aborts the change. Rejected/failed attempts get their own
safe event without persisting secrets. If PostgreSQL itself is unavailable, do not
apply the requested mutation; emit a structured security failure with request ID.
The DB audit API cannot promise rows for a time when its DB was unreachable: expose
that limitation and retain platform logs. No silent success or invented backfill.
Ordinary polling/quote ticks remain access/diagnostic logs, not high-volume security
history rows. No public audit delete/update endpoint or automatic audit pruning in
this phase. DB administrators are still a trust boundary; this is not a claim of
cryptographically immutable history.

The FE uses an explicit sign-in endpoint to record each sign-in attempt and obtain
its principal/permissions; this endpoint issues no new token or server session.
Other requests still independently verify their supplied identity and current role.
Do not call every authenticated polling request a new login. Record mutation
denials in security history and retain request/auth failures in structured access
logs with bounded input sizes and admission controls; do not let anonymous failure
traffic exhaust the DB pool or an unbounded audit queue. Real Google tokens used
by non-interactive API clients retain per-request access logging and per-mutation
actor audit even when they do not perform an interactive sign-in.

All paths below are proposed additions under `/api/v1/admin`, not shipped routes.

| Endpoint | Access | Contract |
| --- | --- | --- |
| `POST /access/login` | Verified enabled identity | Record explicit sign-in outcome and return safe principal/permissions; no new token issuance |
| `GET /access/me` | Any authenticated role | Principal, role, allowed actions; no token/password |
| `GET /access/bootstrap` | Admin | Basic-enabled state and whether a bound Google admin is ready |
| `GET /access/accounts` | Admin | Bounded whitelist list |
| `POST /access/accounts` | Admin | Whitelist verified-email onboarding target and role |
| `POST /access/accounts/{id}` | Admin | Versioned role/active changes; protect last bound admin |
| `GET /credentials/okx` | All roles | Status for configured connector slots; no secret or cryptographic blob |
| `POST /credentials/okx/{connector}` | Admin/write | Save complete bundle with expected credential/control versions and idempotency key |
| `POST /credentials/okx/{connector}/revoke` | Admin/write | Durable Stop/revoke receipt; no OKX key-deletion claim |
| `GET /audit` | All roles | Stable cursor pagination and filters by UTC range, actor, action, outcome, target and operation ID |
| Existing `GET /commands/{id}` | Authorized existing scope | Poll safe receipt until runtime application/cleanup completes |

Use the existing bounded pagination pattern: default 100, maximum 500, stable
`created_at/id` ordering, cursor bound to filters and bot scope. Parameterize filters
and add only indexes justified by the query paths. No unlimited audit dump, N+1
actor lookup, ciphertext fields or raw HTTP payloads. Audit shows the actor snapshot
even after the account is disabled. Authorization applies to **all** existing
routes, including reporting and mutations; fresh DB role checks prevent a removed
user retaining access through a still-valid Google token.

FE keeps Google tokens out of URLs and persistent storage; no shared Basic password
in the frontend build. For private Cloud Run invocation through the existing gateway,
use `Authorization: Bearer <user Google ID token>` for application identity and the
separate platform invocation header. Do not let the gateway substitute a shared
Basic identity and bypass per-user authorization. Reuse CORS/English errors and
handler Swagger annotations; Google Bearer and optional Basic are alternatives in
OpenAPI. Documentation remains available only for `ENV=dev/development`.

## Sequential implementation and acceptance checklist

Every implementation phase starts with failing behavioral tests, records Red/Green
evidence in `reports/`, updates its checkboxes only after verification, and commits
with a conventional prefix. Planning checks above do not mark implementation done.

### A1 — Authentication settings, identity and permissions

- [ ] Test Basic on/off, populated-but-disabled credentials, wrong/expired Google
  tokens, wrong audience/issuer, non-whitelisted identities and disabled users.
- [ ] Implement official Google verification and principal propagation; cover every
  existing handler with explicit read/write/admin permissions and correct audit actor.
- [ ] Test no Basic fallback on Google/DB failure; avoid recording unverified email,
  Authorization headers or sensitive Pydantic input in errors/logs.
- [ ] Test explicit 401/403 explanations, safe verified-identity boundaries and every
  new error-code/retry mapping; specific errors must not collapse into generic failures.
- [ ] Test bounded signing-key retrieval/caching and auth worker saturation without
  blocking engine progress or starving Stop; document legacy env upgrade requirements.

### A2 — Whitelist, roles and audit foundation

- [ ] Add idempotent PostgreSQL migrations/models for accounts and audit; test repeat
  migrations, version conflicts and cursor/filter behavior with real PostgreSQL.
- [ ] Test subject binding, role hierarchy, self-demotion and simultaneous last-admin
  removals; an unverified invitation must not satisfy bootstrap readiness.
- [ ] Implement the audit API and complete actor/outcome coverage, including failures
  and denied actions; test atomic change+audit rollback and duplicate retries.
- [ ] Prove revoked/demoted users lose permission across API restarts/processes with
  an existing token; failed auth lookup remains fail-closed.
- [ ] Validate disabling Basic across full restart/revision replacement while Google
  admin remains usable; no API can toggle either authentication env flag.

### A3 — Envelopes and local/GCP providers

- [ ] Test encryption/decryption, distinct nonces/DEKs, restart persistence, wrong key,
  malformed local key, modified ciphertext and wrong bot/connector/environment/version.
- [ ] Implement the small wrap/unwrap provider interface with local and official GCP
  adapters; verify no automatic fallback, regeneration or plaintext persistence.
- [ ] Test KMS denial/timeout/wrong-key-version and dependency packaging; use fakes for
  deterministic offline tests and a separately scoped real-GCP integration check.
- [ ] Test KEK/credentials/token redaction through repr, SQL/logging, error responses,
  trace and generic command serialization. Scan disposable DB rows/exports for canaries.
- [ ] Document controlled key/provider transition with re-entry, backup key retention,
  and environment-mode cutover; do not auto-import or silently copy existing `.env` keys.

### A4 — Credential API and engine lifecycle

- [ ] Test set/replace only after acknowledged Stop; recheck authorization after slow
  encryption, use safe idempotent receipts and atomically commit credential/audit/control.
- [ ] Implement managed source into existing connector models; cover service startup,
  CLI preflight, funding observer and reconciliation consumers, with no env fallback.
- [ ] Test missing/bad credentials keep admin usable; same-account binding, spot-only,
  perpetual/both, wrong environment, and validation failure never auto-resume trading.
- [ ] Test same-account key rotation retains ledger/positions; different subaccount,
  Demo/live, cross-slot identity mismatch and a second `BOT_ID` in the same DB are
  rejected without resetting balances, history, HALT or account bindings.
- [ ] Test revoke against queued sends, delayed ACK/fill/cancel, an absent owner and
  concurrent Start/replace; no new risk after the existing fenced send check.
- [ ] Test automatic cancellation across both markets, recovered/late orders,
  partial fills racing with cancellation, bounded retries and manual fallback.
  Exclude unrelated orders; an empty local tracker or cancel ACK cannot finish cleanup.
- [ ] Test restart at every accepted/applied/revoking boundary, cleanup timeout and
  revoke during KMS outage; all consumers detach and outcomes remain honest.
- [ ] Test accepted versus applied/failed/unknown outcomes, lost responses, stale
  owner/credential versions and cleanup requiring intervention. Never show false
  Connected/Disconnected, reset exposure to zero or report completion prematurely.
- [ ] Verify KMS/auth work stays outside the quote path; loaded authorized clients
  tolerate KMS outage while cold loads and removed credentials remain blocked.

### A5 — FE handoff, Swagger and operational runbook

- [ ] Document endpoint models, status versus applied state, role matrix, bootstrap
  shutdown and credential exception to the old API/YAML/env ownership table.
- [ ] Provide simple English UI labels: Connected, Not configured, Checking connection,
  Disconnecting, Disconnected, Action required; audit exposes actor/action/time/result.
- [ ] Document the error/status tables, role-aware action availability, safe inline
  validation, retry/receipt handling and request-ID support path. Add OpenAPI examples
  for healthy, stopped, blocked, stale, permission-denied and cleanup-pending cases.
- [ ] Show that Disconnect stops the bot, may need outstanding-order cleanup, leaves
  positions open and does not delete the OKX API key; prevent duplicate button actions.
- [ ] Update annotations and run `make swagger` / `make swagger-check`; schema must
  contain no reveal-secret operation or secret-valued response example.
- [ ] Document local/GCP setup, rollout/rollback, private ingress/auth headers, env-only
  Basic disable, last-admin recovery and actual OKX key revocation by the operator.

### A6 — Final verification, load and evidence

- [ ] Run focused plus required native/regression and PostgreSQL suites on final code;
  exercise authenticated read/write/admin and Basic-disabled cases end to end.
- [ ] Run offline lifecycle/fault tests using real local encryption and simulated KMS
  delays/denials: credential change/revoke, owner restart, DB loss and token/role changes.
- [ ] Verify error, receipt, audit and status correlation end to end, including a
  post-commit response loss and a KMS outage while MM keeps running; all visible
  guidance remains English, secret-free and consistent with observed trading state.
- [ ] Verify bounded audit queries with substantial history, query counts, and secret-
  free export; authenticate sustained load as Google identities with fresh role lookups.
  Offline signing keys/verifier inputs are fixture injection only, never a shipped
  environment flag that disables Google verification or permits arbitrary issuers.
- [ ] Run the original final-image load/retention gates including the unchanged
  60-minute soak; record auth/audit/provider overhead and MM/probe witnesses, preserving
  original limits and every failed attempt. Do not transfer older-image acceptance.
- [ ] Add a bounded mixed-security workload for concurrent role change, invalid auth,
  credential status/audit reads and slow KMS. Prove Stop remains responsive, accepted
  mutations remain auditable, queues are bounded and no secret memory accumulation occurs.
- [ ] Verify real Google sign-in and a real Cloud KMS key with synthetic credentials
  when project/client resources are provided. Keep unavailable external checks open;
  offline mocks cannot close them. Test actual OKX Demo credentials only under an
  explicitly reviewed cutover, retaining prior ledger and recovery evidence.
- [ ] Complete FE integration notes, evidence and remaining limitations; verify no
  secret enters Git, mark completed phases and commit. Do not deploy production here.

## Primary references

- [Google ID-token verification](https://developers.google.com/identity/gsi/web/guides/verify-google-id-token)
  — use verified subject identity and audience; email onboarding has ownership limits.
- [Cloud KMS envelope encryption](https://docs.cloud.google.com/kms/docs/envelope-encryption)
  — separate data keys from the KEK and use authenticated encryption.
- [Cloud Run IAM](https://docs.cloud.google.com/run/docs/reference/iam/roles)
  — deployment/service-account permissions remain part of the trust boundary.
- [OKX API documentation](https://www.okx.com/docs-v5/en/)
  — exchange authentication/permissions; local Disconnect does not claim exchange key deletion.
