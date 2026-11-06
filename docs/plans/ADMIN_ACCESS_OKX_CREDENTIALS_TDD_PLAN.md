# Admin access and encrypted exchange credentials — TDD plan (OKX first)

Status: A1–A5 implemented; A6 local/offline acceptance completed on 2026-11-05 (Asia/Saigon). Real Google/GCP and reviewed existing-Demo cutover remain open; no production deployment.
Evidence: [implementation progress](../../reports/implementation/ADMIN_ACCESS_CREDENTIALS_PROGRESS.md).
Unchecked sections describe proposed behavior. Current endpoints
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
- [x] Confirm Disconnect automatically cancels all bot-owned pending orders and
  closes all bot-managed perpetual positions before removing selected credentials.
- [x] Design storage, API, audit and lifecycle boundaries for additional exchanges;
  implement only OKX in this phase and reuse existing Hummingbot connectors.
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
| `TradingCore`, executor close actions and held-position checkpoints | Reuse execution and persistence, but add explicit Disconnect cleanup: the current owner shutdown preserves positions and its submission gate rejects orders after Stop |
| Funding observer, reconciliation and CLI preflight | Audit all credential consumers; a revoked key must not survive through an environment-only reader or an idle observer |
| `mm_events` | Keep session history intact; its mandatory session identity makes it unsuitable as the general security audit table |
| Existing cryptography package, Google libraries | Reuse authenticated encryption; add official Google identity/KMS clients where needed, and update runtime packaging |

This creates one explicit future exception to the current configuration contract:
managed **exchange credentials** become API-editable encrypted DB data, starting
with OKX. Strategy YAML, exchange live/demo routing, auth flags, KEK/provider
settings and other environment settings remain deployment-owned.
Existing documentation must not claim these
new endpoints work before implementation and verification finish.

### Additional exchanges without rebuilding credential management

Keep the current plan filename for stable links. Shared authentication, roles,
encryption, audit, receipts and operator flow must not depend on OKX-specific names.
Use an explicit `exchange_id`, existing Hummingbot `connector_name`, deployment-owned
environment and verified account/subaccount binding. For the first adapter these
are `okx` with `okx` or `okx_perpetual`; another exchange is not another key for an
existing account binding or permission to reuse its ledger.

Reuse connector configuration schemas, order tracking, fills and position models.
Add only a small adapter boundary for exchange-specific credential validation,
account identity and confirmation of cancellation/position closure. Each adapter
must prove the supported position modes and exchange-enforced closing semantics;
no generic opposite-side order may be assumed safe for every exchange. An exchange
with incompatible account discovery or cleanup needs its own tested implementation.

The server exposes only deployment-enabled, implemented exchange/connector choices
and their safe required-input descriptions/capabilities. Forms use those descriptions
instead of assuming every exchange needs an API key, secret and passphrase. Actual
input is validated against the existing connector schema and encrypted as a whole;
responses never include saved field values. Unsupported exchange, connector,
environment or cleanup mode gets a specific error, never an OKX fallback or
dynamic import based on an arbitrary client-supplied name.

An `is_connect_key` annotation is not sufficient to expose a writable field:
OKX's schema also marks registration subdomain that way. The adapter must allowlist
credential fields only and inject deployment routing, site, environment and connector
identity itself. Reject client routing/config overrides even if their values match
deployment. Generate input descriptions from schema metadata, never a populated
global connector configuration or its current values.

Keep the existing service's reviewed OKX allowlist in this phase. A second exchange
requires connector/ownership/recovery integration and its own offline/Demo acceptance,
not just a new dropdown entry. Test the shared contract with a fake second adapter;
do not implement speculative live adapters, cross-exchange trading or a plugin system.
Changing trading venues remains a deployment/YAML and dedicated-ledger decision,
not an API override of strategy configuration.

## Authentication, roles and bootstrap shutdown

| Capability | Basic admin, when enabled | Google `admin` | Google `write` | Google `read` |
| --- | --- | --- | --- | --- |
| Read bot status/history/credential status/audit | Yes | Yes | Yes | Yes |
| Start/switch/stop and existing operational mutations | Yes | Yes | Yes | No |
| Set or revoke managed credentials for enabled exchanges | Yes | Yes | Yes | No |
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
| `EXCHANGE_CREDENTIALS_SOURCE` | New shared setting: `environment` by default for existing setups; managed mode is `postgres_kms` |
| `KMS_PROVIDER` | `local` by default; valid values **only** `local`, `gcp`; used when source is `postgres_kms` |
| `KMS_LOCAL_SECRET_KEY` | Required in local mode: base64-encoded random 32-byte KEK; no generated/default production key |
| `KMS_LOCAL_KEY_ID` | Stable non-secret identifier, default `local-v1`; must identify the actual configured KEK |
| `KMS_GCP_KEY_RESOURCE` | Required in GCP mode: approved Cloud KMS symmetric encryption CryptoKey resource; no fallback |

All flags use the existing strict boolean parser and environment precedence.
Unknown provider/source values fail validation. Managed source and Google whitelist
authentication require PostgreSQL.

In `environment` mode, credential status says "Managed by deployment" and all
credential set/Disconnect/cleanup-key endpoints reject with
`409 credentials_managed_externally` before encrypting or writing anything. The
existing Stop API remains available; deleting a DB row cannot revoke an env key.
Managed lifecycle APIs require `postgres_kms`. Selecting this source on a stopped
deployment with no saved credentials must still allow admin onboarding and reads.

`EXCHANGE_CREDENTIALS_SOURCE` supersedes the earlier unimplemented plan name
`OKX_CREDENTIALS_SOURCE`; do not add two competing source switches. Existing
connector-specific environment credential names remain unchanged.

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
32-byte data key (DEK) encrypts the complete connector credential bundle with
AES-256-GCM and a fresh nonce. The KEK wraps the DEK. Authenticate format version,
bot ID, exchange ID, connector, deployment environment and credential version as
associated data so moving ciphertext to another scope/version fails authentication.

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
status, version), `exchange_credentials` (one current envelope/version per bot,
exchange, connector and environment) and `admin_audit_events` (append-only safe events).
Use the same scope in uniqueness constraints, receipts, queries, audit filters and
encryption context; never create a new credential table for each exchange.
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

### Receipts and authorization transactions

Reuse the `admin_commands` table, not its trading-only HTTP model. Current
`CommandResponse`/history accept only start/switch/stop and require a session;
`record_observation()` infers Stop completion from stopped engine flags, which
cannot prove Disconnect completion. Use distinct actions `credentials_set`,
`credentials_disconnect`, `credentials_cleanup_key` and `credentials_cleanup_retry`,
safe typed payload/result models and a dedicated credential-operation GET below.
Leave the current trading command/history contract unchanged. A credential receipt
may have no session and must never enter the ordinary Stop success/supersession path.
Hash only allowlisted action/scope/expected-version/actor metadata for the table's
required `request_hash`; never hash submitted credentials. Namespace/validate retry
identity across action, actor and bot before returning a prior receipt.

Use `accepted`/`applying` until actual completion; a blocked Disconnect remains
`applying` with `operator_action_required` and a reason in its safe result. A rejected
Set may be `failed`. Missing evidence is an observation state, not a successful or
terminal receipt. Persist the active Disconnect ID and affected credential versions
under the existing bot control/recovery state. All API/CLI Start/switch/replace paths
must reject while it is active; repeated ordinary Stop cannot supersede, complete or
discard it. Disconnect works without an MM session, including a newly configured
stopped bot. Do not call `request_mm_session(stop)` when no session exists.

Serialize role changes and mutation admission with a consistent DB lock order and
locked account/version checks inside the final transaction. External verification
and encryption happen before these short locks. A mutation accepted before a role
removal remains an audited authorized operation: token expiry, logout or subsequent
role removal do not abandon already accepted cancellation/closure. New mutations,
including retry or cleanup-key repair, require fresh current permission.

## Credential lifecycle and availability

Expose requested versus observed state. `configured=true` means an encrypted
credential bundle is saved and permitted, not that OKX connectivity or recovery
has succeeded. Status includes safe credential version, validation/runtime state,
operation ID, changed-at/changed-by and an actionable English reason.

**Set/replace:** require the bot to be stopped with acknowledged order cleanup
and no pending exchange mutation; otherwise return `409 bot_must_be_stopped` and
direct the caller to the existing Stop flow. Validate the candidate with bounded
read-only account/permission/environment checks before replacing the saved bundle;
never start strategy loops or change account settings during this check. A rejected
candidate leaves the prior saved version unchanged and the bot stopped. This is
rejection before commit, not an automatic fallback after a committed replacement.
Encrypt off the event loop before the short transaction, then recheck actor, state,
account binding and expected versions. Atomically
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

For OKX, reuse the account-config reader in `okx_account_observations.py`, but use
a fresh candidate-scoped verified `uid` for authorization; its current best-effort
reporting cache is not an authorization source. `uid` identifies the request account;
`mainUid` alone cannot distinguish its subaccounts. Persist the binding independently
of the encrypted bundle so Disconnect cannot erase it. Different API keys with the
same verified binding may serve spot/perpetual; do not compare key strings as account
identity. See [OKX account configuration](https://www.okx.com/docs-v5/en/#trading-account-rest-api-get-account-configuration).

### Disconnect: cancel orders, close perpetual positions, then remove credentials

**Revoke, as confirmed by the user:** atomically mark the selected credential as
revoking, request a bot-wide Stop, bump control authority and record the actor and
receipt. This simple first version stops the joint bot even if only one connector
is revoked. Cleanup includes all bot-owned orders and bot-managed perpetual
positions in that runtime, including previously held positions, not just the
currently selected session/market. Only the selected credential slot is erased;
other configured slots stay saved and the bot stays stopped. Show this scope before
confirmation, including the effect on perpetual when disconnecting a spot slot.
Spot balances are retained; Disconnect does not sell spot inventory or withdraw funds.
New risk is denied at the existing final send gates. Requests already
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

After pending quote orders and in-flight submissions are resolved, automatically
close remaining perpetual exposure through the existing connector/tracking/recorder
path. Use market-close orders in the initial flow; explain that execution can incur
fees, slippage and realized profit/loss and has no guaranteed execution price or ETA.
Confirm each order's outcome, persist fills/fees and reconcile the remaining position.
Close HEDGE long and short separately: net exposure of zero does not mean both are
closed. Include recovered/held positions, not just live executors. Apply instrument
size/precision rules; an uncloseable residual is a blocker, not a rounded-to-zero
success. Do not modify unrelated manual/other-bot exposure. If account positions
are mixed and bot ownership cannot be established, stop cleanup with a clear reason.

Current `BotOwnership.shutdown_core()` calls `keep_positions=True`, while
`authorize_rest()` treats placement as new risk and applies normal MM/session and
post-only checks. Merely changing that boolean will not implement this requirement.
Keep ordinary Stop, crash/revision shutdown and key rotation semantics unchanged.
Add an explicit Disconnect cleanup intent under the existing ownership/receipt model:
only the current owner, matching operation and credential version may place a
recorded close order for verified remaining exposure. It may bypass the normal
MM quote/session restriction only for this purpose; it must not reopen general
submissions, clear unrelated HALTs, bypass persistence checks or use a stale owner.
Unknown account/position state, lost ownership or unhealthy persistence blocks closure.
Keep required connectors and the recorder available during the acknowledged cleanup
operation; do not tear them down through ordinary Stop before close orders settle.
The operation survives bounded request/process lifetimes through its receipt and
recovery state; it is not a longer unbounded shutdown coroutine.

Dispatch a pending Disconnect in `service.observe_once()` before its ordinary
control-version change calls `stop_core()`. Handle both `before_order()` admission
and `authorize_rest()` transport checks, plus durable intent recovery: a final-REST
exception alone is insufficient. Cleanup authority is the recorded Disconnect,
not an expired MM session or its maker/quote deadline. Give each close a bounded
transport deadline and persisted remaining-quantity reservation. SIGTERM still
fences new sends and retains the existing hard shutdown deadline; a successor first
reconciles already-sent closes before resuming. Update held-position checkpoints
through the existing recorder so a restart cannot resurrect already closed exposure.

Verify the adapter's exchange-enforced close/reduce-only semantics for each supported
position mode; HEDGE must target the correct side. The current generic ONEWAY close
path can use `PositionAction.OPEN`, so it must not be reused blindly for Disconnect.
Implement and test a safe mode-specific mapping before enabling it, or report
`unsupported_cleanup_mode`. Reserve/reconcile in-flight close quantity so concurrent
or repeated cleanup cannot over-close or reverse a position. A lost placement ACK
requires intent/order lookup before resubmission; only confirmed remaining quantity
may get another close intent. Partial closes and late fills resume the same durable
operation after restart. All network work/retries remain bounded and observable.

Initial acceptance covers the existing LIFE MM configuration: OKX USDT perpetual,
HEDGE and CROSS. Close long with `side=sell,posSide=long`; close short with
`side=buy,posSide=short`, using the connector's normal order endpoint and contract
conversion. This targets the exchange's closing semantics; do not introduce a raw
close-position request outside tracking. Net/ONEWAY, isolated margin and unsupported
instruments fail capability checks; implementing those is a later adapter task.
The parameter `reduceOnly` has different applicability in net mode, so a universal
flag is not a substitute for side/mode validation. See
[OKX order parameters](https://www.okx.com/docs-v5/en/#order-book-trading-trade-post-place-order).

The owner may use credentials only for authorized cancellation, position closure
and necessary cleanup reads during revocation. Keep the encrypted bundle until
cleanup is verified so a restarted owner can resume this restricted workflow;
loading it requires the same operation/scope checks and must not enable normal trading.
Completion requires fresh exchange evidence of zero bot-owned pending orders and
zero bot-managed perpetual positions, no unresolved sends, and durable fill/fee/
position checkpoints. Then detach **all** consumers of the selected credentials,
including idle funding and reconciliation readers; erase the current encrypted
bundle/wrapped DEK and mark revoked after observation confirms detachment.
Keep safe audit/version records.
Python reference removal is not guaranteed physical memory zeroization, and database
backups may retain older encrypted rows. No silent reload from `.env` is permitted.
Revoke admission does not require KMS access.

If cleanup times out, ownership is unavailable, an in-flight order is unresolved,
positions remain or restart interrupts revocation, expose `cleanup_pending`/operator
action instead of reporting completed revocation. Admission remains closed; restart resumes only
the fenced cleanup workflow. Transient errors use bounded automatic retries with
the next retry time where known. Remaining orders/positions need manual exchange
action only when automatic cleanup cannot complete. Recheck exchange evidence after
manual intervention; an operator assertion alone cannot mark cleanup complete.
If credentials were already deleted on the exchange, show an explicit verification
blocker rather than claiming the orders/positions disappeared. There is no initial
"force complete" or "skip cleanup" API. Exchange API-key deletion remains manual.

### Recovering a blocked Disconnect

Bound automatic retries per external step and expose their budget/next retry time;
exhaustion keeps the same operation blocked, not completed. An admin/write operator
can request "Retry cleanup" on that operation with fresh authorization, expected
versions and an idempotency key. Reconcile first, then retry only unresolved work.
It never creates a second Disconnect or blindly resubmits an unknown close.

If an API key has expired or was deleted before cleanup finished, normal Set/replace
is deliberately blocked. Provide a narrowly scoped "Update key to finish disconnect"
action for an affected connector. Validate the replacement read-only against the
persisted exchange/account/environment binding, then atomically record its encrypted
version, the same Disconnect ID and an audit event. Detach/fence the old consumer;
use the replacement only for reconciliation/cancel/close while cleanup is active.
Recheck unknown earlier sends before any further close. No Start, new account binding,
HALT reset or permission change is implied. A bad candidate leaves existing data intact.
This uses the existing credential row, not a plaintext secret in the operation receipt.

The selected slot is still erased at completion; a repaired companion slot that was
not selected for removal remains saved, with trading stopped. State that distinction
in its confirmation. KMS failure requires fixing the configured provider/key first,
never an env/plaintext fallback. Manual cancellation/closure on the exchange remains
possible, but final verification still needs working read access; an operator's
"done" click cannot prove flat positions. No SQL edits or fresh trading session are
required for the supported retry/same-account credential-repair paths.

### Disconnect UX for a non-technical operator

Keep one guided action with a confirmation, a progress view and a final result.
Do not require the operator to issue separate cancel/close requests. FE implementation
remains in its own repository; this plan supplies the API contract and English copy.

1. **Review:** show the selected exchange/account/credential slot, the bot-wide
   effect, pending orders and perpetual long/short quantities with timestamps.
   Stale/missing values say "Unknown", never zero. The server rechecks actual state;
   the preview is not an authority snapshot or a guarantee against a late fill.
2. **Confirm:** title "Disconnect {exchange}?"; text: "This will stop the bot,
   cancel its pending orders, close its perpetual positions, and remove the selected
   saved connection. Closing positions may incur fees and realize a profit or loss.
   Spot balances stay in your exchange account. This does not delete the API key
   on the exchange." Buttons: "Back" and "Close positions and disconnect"; for a
   proven spot-only scope with no perpetual exposure, use "Stop and disconnect".
   Unknown exposure does not get the simplified spot-only confirmation. Confirmation
   authorizes the complete scoped cleanup, including exposure from late order fills.
3. **Track:** reuse the durable operation receipt; show the current stage below,
   remaining work and last confirmed update. Browser refresh/sign-out does not
   cancel server cleanup. Disable duplicate/conflicting Start/replace/Disconnect
   actions; a retried original request returns the same operation.
4. **Finish:** only after verified cleanup and credential removal, show
   "Disconnected. Bot orders cancelled and perpetual positions closed."
   For verified spot-only cleanup, omit the perpetual-position sentence.
   Show the final fill/fee summary, timestamp and audit link. Keep a separate note:
   "The API key was not deleted on the exchange. Delete it there if no longer needed."
   This note is not a claim that the key is currently valid. API/service remain up;
   connecting a key again does not auto-start trading.

The following stage values are a projection of existing receipt/cleanup evidence,
not another independent state machine. Persist only progress needed for recovery.

| Receipt `result.stage` | English UI label | Required evidence / next step |
| --- | --- | --- |
| `waiting_for_owner` | Waiting for trading service | Durable trading fence; resolve submissions already sent |
| `cancelling_orders` | Cancelling pending orders | Automatic cancellation and terminal order reconciliation; show remaining/unknown counts |
| `closing_positions` | Closing perpetual positions | Automatic close orders; show remaining long/short quantity per pair and tracked close-order state |
| `verifying` | Verifying orders and positions | Exchange readback plus committed ledger; late fills/residuals return to the necessary cleanup stage |
| `detaching` | Removing saved connection | Verified cleanup; detach consumers and erase the selected envelope |
| `removed` | Disconnected | Cleanup verified and credential removal committed; bot remains stopped |

`connection_status=action_required` retains the blocked stage and uses specific
reasons such as `order_cancellation_unconfirmed`, `position_close_unconfirmed`,
`position_ownership_unverified` or `cleanup_credentials_unavailable`. Say "Pending
orders need attention" or "Open positions need attention", with affected items,
the safe failure reason and "Open {exchange}" guidance. Keep the last observation
timestamp and unknown state visible. If a real retry is scheduled, show "Retrying"
and its time; never loop a spinner indefinitely or ask for a fresh Disconnect
operation. Manual exchange action is the exception after automatic cleanup fails.
Offer "Retry cleanup" when its preconditions hold and "Update key to finish
disconnect" for credential failures; both keep the original Disconnect visible.

### KMS availability

KMS is used for set/load/unwrap, **not per order, tick, quote or health request**.
An already loaded, still-authorized connector should continue through a temporary
KMS outage. A cold start unable to decrypt stays idle with credentials unavailable
while keeping administration/diagnostics responsive. Reuse current bounded workers,
adding only a small bounded security/provider lane if necessary to keep slow KMS
or Google work out of the engine loop and Stop lane. Do not raise DB pools or
timeouts to hide blocking. No unbounded queues, extra/shared plaintext credential
caches or secret-bearing cross-process status messages; authorized connectors
necessarily retain credentials in their own memory while in use.

Wire the provider into all existing consumers: `Security.api_keys()` lazy loading,
`initialize_from_environment()` bootstrap, service `validate_selection()`, funding
discovery/`FundingReader.from_environment()`, preflight and connector initialization.
Managed mode must never fall back through `_environment_mode` or configured-env
discovery; invalidate the old in-memory connector configs on version change/removal.
Test missing credentials on a cold restart with a previously running desired state:
publish a trading blocker and keep the process/admin alive, not a configuration-error
restart loop. An unconfigured stopped bot can acquire ownership and accept its first key.

Review supervisor/API/engine settings and `child_environment()` explicitly: the API
still gets no `HBOT_*` trading keys but now processes submitted credentials and needs
its configured encryption/validation provider. The engine needs unwrap/read access.
Do not fetch Google keys, query whitelist state or contact KMS synchronously in the
supervisor's pre-launch config validation. Use safe health/blocker status for runtime
dependency failures. Shared Cloud Run identity/local KEK is still a trust boundary;
separate processes alone do not make API code cryptographically unable to decrypt.

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
| 409 `credentials_managed_externally` | “This connection is managed by deployment settings.” Use `contact_support`; do not pretend DB changes can revoke an env key |
| 409 `credential_operation_active` | “A connection change is already in progress. View that operation before continuing.” Use `refresh_state` and link its safe operation ID |
| 409 `exchange_account_mismatch` | “This key belongs to a different exchange account. Use a key for this bot's account.” Use `fix_request`; never offer a reset-history bypass |
| 422 `validation_failed` / `exchange_credentials_rejected` | Identify safe field paths or say “The exchange did not accept the connection details. Check the required credentials.” Use `fix_request`; never echo supplied values |
| 422 `unsupported_exchange` / `unsupported_connector` / `unsupported_cleanup_mode` | Explain the unsupported configured choice and supported alternatives; use `fix_request`, never fall back to another exchange or unsafe closing semantics |
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
Credential mutations accept bounded JSON only; reject unsupported media types,
oversized bodies and unknown fields without reflecting input. Apply the existing
origin policy and explicit Authorization header, not cookies or query-string tokens.

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
| `disconnecting` | Disconnecting | Stop/revoke accepted, new risk fenced, automatic cancellation/position closure/removal underway; show the current cleanup stage |
| `action_required` | Action required | Cleanup outcome is unresolved or intervention is required; show the blocker and safe next action |
| `disconnected` | Disconnected | Bot order/position cleanup was verified, selected bundle removed and its consumers detached; show completion time, not a promise about later manual exchange activity |
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
Disconnected while cancellation, position closure or detachment is pending.
Logout, HTTP/network failure or a stale observation cannot fabricate a stopped engine, zero pending orders or
closed positions. Surface retained exposure and its freshness using existing
order/position views; unknown values remain unknown.

Use “Disconnect {exchange}” (initially “Disconnect OKX”) and the guided flow above.
Audit, receipt, status and request logs share operation/request IDs and consistent
reason codes so support can follow one action through request, cleanup and final
outcome. Keep completed cleanup evidence visible after removal; live account
monitoring using the removed credentials ends, and later account activity is unknown.

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
action time, action, exchange/connector/environment, target/credential version,
outcome, safe reason code and allowlisted before/after metadata. Invalid tokens have an unknown/unverified actor;
never attribute their claimed email to a verified user. Retries and owner takeover
must not duplicate the same committed state-transition event.
Disconnect audit links the operator's confirmed scope, meaningful cleanup transitions,
close-order/fill references and final credential removal. Preserve trading fees and
positions in the existing ledger; do not duplicate every polling observation in audit.

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

The following routes are implemented under `/api/v1/admin`; final-image and external acceptance are tracked at A6.

| Endpoint | Access | Contract |
| --- | --- | --- |
| `POST /access/login` | Verified enabled identity | Record explicit sign-in outcome and return safe principal/permissions; no new token issuance |
| `GET /access/me` | Any authenticated role | Principal, role, allowed actions; no token/password |
| `GET /access/bootstrap` | Admin | Basic-enabled state and whether a bound Google admin is ready |
| `GET /access/accounts` | Admin | Bounded whitelist list |
| `POST /access/accounts` | Admin | Whitelist verified-email onboarding target and role |
| `POST /access/accounts/{id}` | Admin | Versioned role/active changes; protect last bound admin |
| `GET /credentials` | All roles | Deployment-enabled exchanges/connectors and safe input/capability descriptions; no saved credential values |
| `GET /credentials/{exchange}` | All roles | Status for configured connector slots, cleanup progress, source mode and allowed actions; no secret or cryptographic blob |
| `POST /credentials/{exchange}/{connector}` | Admin/write | Save complete bundle with expected credential/control versions and idempotency key |
| `POST /credentials/{exchange}/{connector}/revoke` | Admin/write | Durable receipt for bot-wide cancel/close cleanup and selected credential removal; no exchange key-deletion claim |
| `GET /credentials/operations/{id}` | All roles | Typed credential receipt, progress, blockers and final result from `admin_commands`; nullable session, no raw secret payload |
| `POST /credentials/operations/{id}/retry` | Admin/write | Versioned, idempotent request to recheck/resume the same blocked cleanup; cannot skip unknown outcomes |
| `POST /credentials/{exchange}/{connector}/cleanup-credentials` | Admin/write | Same-account replacement solely to finish an identified active Disconnect; expected operation/credential/control versions required |
| `GET /audit` | All roles | Stable cursor pagination and filters by UTC range, actor, action, outcome, target and operation ID |
| Existing `GET /commands/{id}` | Authorized existing scope | Keep the trading-command contract; credential receipts use their typed route above |

`exchange=okx` is the only initial enabled implementation; the resulting existing
planned OKX URL shape is retained. Path, connector, environment and account scope
must agree with the deployment and authenticated operation. Disconnect semantics
are fixed: no client flag can skip cancellation/closure or erase credentials early.
Do not add a general close-any-account endpoint as part of credential management.
Register and test the operation routes explicitly; path parsing must not treat
`operations` as a user-selectable exchange or expose another bot's receipt.

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

### A1 — Storage foundation, authentication settings and permissions

- [x] Create/test accounts and audit migrations/models before enabling DB-backed
  authentication; repeat migrations, missing-schema behavior and safe service startup.
- [x] Test Basic on/off, populated-but-disabled credentials, wrong/expired Google
  tokens, wrong audience/issuer, non-whitelisted identities and disabled users.
- [x] Implement official Google verification and principal propagation; cover every
  existing handler with explicit read/write/admin permissions and correct audit actor.
- [x] Test no Basic fallback on Google/DB failure; avoid recording unverified email,
  Authorization headers or sensitive Pydantic input in errors/logs.
- [x] Test explicit 401/403 explanations, safe verified-identity boundaries and every
  new error-code/retry mapping; specific errors must not collapse into generic failures.
- [x] Test bounded signing-key retrieval/caching and auth worker saturation without
  blocking engine progress or starving Stop; document legacy env upgrade requirements.

### A2 — Whitelist, roles and audit API

- [x] Implement whitelist/bootstrap and audit endpoints over A1 models; test version
  conflicts, transactional role enforcement and cursor/filter behavior with real PostgreSQL.
- [x] Test subject binding, role hierarchy, self-demotion and simultaneous last-admin
  removals; an unverified invitation must not satisfy bootstrap readiness.
- [x] Implement the audit API and complete actor/outcome coverage, including failures
  and denied actions; test atomic change+audit rollback and duplicate retries.
- [x] Prove revoked/demoted users lose permission across API restarts/processes with
  an existing token; failed auth lookup remains fail-closed.
- [x] Validate disabling Basic across full restart/revision replacement while Google
  admin remains usable; no API can toggle either authentication env flag.

### A3 — Envelopes and local/GCP providers

- [x] Test and implement the idempotent `exchange_credentials` migration, scoped
  uniqueness/version checks and encrypted persistence without exchange-specific tables.
- [x] Test encryption/decryption, distinct nonces/DEKs, restart persistence, wrong key,
  malformed local key, modified ciphertext and wrong bot/exchange/connector/environment/version.
- [x] Implement the small wrap/unwrap provider interface with local and GCP REST/official Google Auth
  adapters; verify no automatic fallback, regeneration or plaintext persistence.
- [x] Test KMS denial/timeout/wrong-key-version and dependency packaging; use fakes for
  deterministic offline tests; actual GCP acceptance remains explicitly open at A6.
  The official protobuf KMS SDK conflicts with connector descriptors, so use
  Cloud KMS REST via Google Auth ADC, CRC32C and bounded transport instead.
- [x] Test KEK/credentials/token redaction through repr, SQL/logging, error responses,
  trace and generic command serialization. Scan disposable DB rows/exports for canaries.
- [x] Document controlled key/provider transition with re-entry, backup key retention,
  and environment-mode cutover; do not auto-import or silently copy existing `.env` keys.

### A4 — Credential API and engine lifecycle

Complete A4a before A4b; record/commit each verified subphase independently.
Do not advertise or accept Disconnect until the A4b engine workflow is implemented;
an unprocessable placeholder `202` is not an acceptable intermediate API.

#### A4a — Provider integration, typed receipts and safe replacement

- [x] Test set/replace only after acknowledged Stop; recheck authorization after slow
  encryption, use safe idempotent receipts and atomically commit credential/audit/control.
- [x] Test candidate preflight before commit, wrong-account/invalid-key rejection
  preserving the old version, persisted `uid` binding and first-key onboarding.
- [x] Add typed credential-operation reads over existing receipts without breaking
  command/history schemas. Cover nullable sessions and cross-actor/action idempotency.
  Disconnect admission/completion belongs to A4b, consistent with its runtime capability gate.
- [x] Implement managed source into existing connector models; cover service startup,
  CLI preflight, funding observer and reconciliation consumers, with no env fallback.
- [x] Test source-mode mutation rejection, all lazy credential consumers and process
  env/provider wiring; a cold managed-key outage keeps admin alive without a restart loop.
- [x] Test exchange-neutral API/storage/audit scope and input descriptions with OKX
  and a fake second adapter; reject unsupported/mismatched connectors and prevent
  cross-exchange account binding, envelope reuse or implicit venue switches.
  Include connector fields marked `is_connect_key` that are deployment-owned:
  request values must not override OKX site/routing or leak through metadata.
- [x] Test missing/bad credentials keep admin usable; same-account binding, spot-only,
  perpetual/both, wrong environment, and validation failure never auto-resume trading.
- [x] Test same-account key rotation retains ledger/positions; different subaccount,
  Demo/live, cross-slot identity mismatch and a second `BOT_ID` in the same DB are
  rejected without resetting balances, history, HALT or account bindings.

#### A4b — Automatic Disconnect and recovery

- [x] Prove no-session Disconnect admission and completion distinct from ordinary Stop;
  enable its endpoints only with the complete engine cleanup workflow.

- [x] Test revoke against queued sends, delayed ACK/fill/cancel, an absent owner and
  concurrent Start/replace; no new risk after the existing fenced send check.
- [x] Test bot-wide serialization across API/CLI control, repeated Stop and both
  credential slots; accepted cleanup continues after logout/token expiry/role removal,
  while newly submitted retry/repair requires current permission.
- [x] Test automatic cancellation across both markets, recovered/late orders,
  partial fills racing with cancellation, bounded retries and manual fallback.
  Exclude unrelated orders; an empty local tracker or cancel ACK cannot finish cleanup.
- [x] Test Disconnect closes live and recovered/held perpetual positions, HEDGE
  long and short independently, spot-only/both scopes and a spot-slot disconnect
  with held perpetual exposure. Preserve spot balances and unrelated exposure;
  ordinary Stop, crash shutdown and key rotation retain their existing semantics.
- [x] Test scoped close authorization through the final REST gate, partial fills,
  lost placement ACK, concurrent cleanup, stale owners, unsupported position modes
  and uncloseable residuals. No duplicate close, position reversal, new quote,
  bypassed persistence/HALT or premature credential erasure is permitted.
- [x] Cover `before_order`, final REST, expired session, service control dispatch,
  SIGTERM and close-result checkpoints together; verify confirmed flat exposure
  cannot reappear as held exposure after restart. Reject unsupported modes explicitly.
- [x] Test restart at every accepted/applied/revoking boundary, cleanup timeout and
  revoke during KMS outage; recover tracked closes and cleanup-only credential use,
  then detach all selected-credential consumers. Outcomes remain honest.
- [x] Test accepted versus applied/failed/unknown outcomes, lost responses, stale
  owner/credential versions and cleanup requiring intervention. Never show false
  Connected/Disconnected, reset exposure to zero or report completion prematurely.
- [x] Test blocked-cleanup retry and same-account cleanup-key repair after key
  expiry/deletion, including a companion slot, stale versions and unknown prior sends.
  Retain the original operation/fence and remove only selected credentials at completion.
- [x] Verify KMS/auth work stays outside the quote path; loaded authorized clients
  tolerate KMS outage while cold loads and removed credentials remain blocked.

### A5 — FE handoff, Swagger and operational runbook

- [x] Document endpoint models, status versus applied state, role matrix, bootstrap
  shutdown and credential exception to the old API/YAML/env ownership table.
- [x] Provide simple English UI labels: Connected, Not configured, Checking connection,
  Disconnecting, Disconnected, Action required; audit exposes actor/action/time/result.
- [x] Document the error/status tables, role-aware action availability, safe inline
  validation, retry/receipt handling and request-ID support path. Add OpenAPI examples
  for healthy, stopped, blocked, stale, permission-denied and cleanup-pending cases.
- [x] Document the guided Disconnect confirmation/progress/result flow, automatic
  order cancellation and perpetual closure, fees/execution uncertainty, retained
  spot balances and selected-slot removal. No success while work is unconfirmed;
  show actionable failures and prevent duplicate/conflicting button actions.
- [x] Document credential-operation polling, "Retry cleanup" and same-account
  cleanup-key repair; distinguish them from trading receipts and normal key rotation.
- [x] Update annotations and run `make swagger` / `make swagger-check`; schema must
  contain no reveal-secret operation or secret-valued response example.
- [x] Document local/GCP setup, rollout/rollback, private ingress/auth headers, env-only
  Basic disable, last-admin recovery and actual OKX key revocation by the operator.

### A6 — Final verification, load and evidence

- [x] Run focused plus required native/regression and PostgreSQL suites on final code;
  exercise authenticated read/write/admin and Basic-disabled cases end to end.
- [x] Run offline lifecycle/fault tests using real local encryption and simulated KMS
  delays/denials: credential change/revoke, automatic cancel/close, owner restart,
  DB loss and token/role changes. Reconcile terminal orders, fills/fees and flat
  bot-managed perpetual positions before accepting Disconnect completion.
- [x] Verify error, receipt, audit and status correlation end to end, including a
  post-commit response loss and a KMS outage while MM keeps running; all visible
  guidance remains English, secret-free and consistent with observed trading state.
- [x] Verify bounded audit queries with substantial history, query counts, and secret-
  free export; authenticate sustained load as Google identities with fresh role lookups.
  Offline signing keys/verifier inputs are fixture injection only, never a shipped
  environment flag that disables Google verification or permits arbitrary issuers.
- [x] Run the original final-image load/retention gates including the unchanged
  60-minute soak; record auth/audit/provider overhead and MM/probe witnesses, preserving
  original limits and every failed attempt. Do not transfer older-image acceptance.
- [x] Add a bounded mixed-security workload for concurrent role change, invalid auth,
  credential status/audit reads and slow KMS. Prove Stop remains responsive, accepted
  mutations remain auditable, queues are bounded and no secret memory accumulation occurs.
- [ ] Verify real Google sign-in and a real Cloud KMS key with synthetic credentials
  when project/client resources are provided. Keep unavailable external checks open;
  offline mocks cannot close them. Test actual OKX Demo credentials only under an
  explicitly reviewed cutover, retaining prior ledger and recovery evidence.
- [x] Complete FE integration notes, evidence and remaining limitations; verify no
  secret enters Git, mark completed phases and commit. Do not deploy production here.

### A6 follow-up — Local managed-environment cold restarts

- [x] Run the shipping image/entrypoint on isolated PostgreSQL with local KEK and
  explicit Basic bootstrap: stopped `demo -> live -> demo` keeps Demo ciphertext,
  refuses env-key fallback and rejects attaching a Live key to the Demo ledger.
- [x] Repeat the boundary with a persisted running intent and native spot/perpetual
  fills: Live remains blocked; returning to Demo restores only the original scope
  and retains exact fill/fee/position history.
- [x] Cold-load with the wrong valid-length local KEK: admin stays available,
  trading/key fallback stays blocked, ciphertext/history remain unchanged; restore
  the original KEK and verify stopped/connected state without auto-start.
- [x] Include the scenario in the standard offline E2E matrix, record source/image
  hashes and safe evidence, update FE/runbook guidance and commit. Real Google/GCP
  resources and existing-Demo cutover remain outside this follow-up.

Evidence: [local managed-environment E2E](../../reports/release/admin-managed-environment-e2e.md),
final artifact `build/e2e-offline/20261105-175542-5adcab/result.json`. Seven cold
revisions and exact native simulator-ledger reconciliation pass on unchanged image
`1bb946c3…`; 69 related native tests pass. No new full load gate or real external
acceptance is claimed by this test/docs-only follow-up.

### A6 follow-up — Additional native lifecycle E2E

- [x] Add Red/Green checks for simulator-only credential generations and deterministic
  cleanup barriers; keep the shipping service entrypoint and runtime policy unchanged.
- [x] Kill the actual service container after exchange close acceptance before ACK,
  and after a close fill is committed before cleanup verification. Restart the
  same durable operation; prove no duplicate close/reversal or early erasure,
  retain spot, and reconcile all native fills/fees/positions.
- [x] Rotate both slots A to B on the same account using admin APIs; reject an invalid
  replacement without mutation, cold restart and Start, then independently observe
  REST/WS/funding/reconciliation using B without old-key fallback or ledger loss.
- [x] Reject an expired cleanup key, retain Action required and ciphertext, repair
  the same root operation via API with a same-account key and complete cleanup;
  verify idempotency, versions and actor-correlated audit without duplicate sends.
- [x] Include all new scenarios in the standard offline matrix, run appropriate
  regressions, retain failed attempts and safe evidence, update documentation and
  commit each completed phase. Real Google/GCP and existing Demo are excluded.

Final evidence: [native lifecycle report](../../reports/release/admin-managed-lifecycle-e2e.md),
`build/e2e-offline/20261105-213142-34b8a4/result.json`. All four grouped E2E cases
pass on unchanged image `1bb946c3…`, including every physical close attempt (two
per cleanup case, no rejected duplicate sends). Related native regression passes
102 tests. Five fixture Red/Green checks and two corrected E2E test assumptions
are retained in the report. No runtime fix, new full load gate or external
Google/GCP/Demo acceptance is claimed.

### A6 follow-up — Partial closes and cleanup database failures

- [x] Add Red/Green simulator checks for one-shot partial native closes, WS loss,
  both still-live and IOC-cancelled remainders, and independent physical-send truth.
- [x] Verify partial live close readback after WS loss: keep credentials/operation
  pending until the original remainder fills; no extra close while it remains live.
- [x] Verify a terminal partial close is reconciled before a fresh residual-only
  close, preserving the same operation, exact quantities/fees and spot holdings.
- [x] Add guarded test-DB transaction faults for cleanup-fill persistence and final
  removal audit. Prove rollback prevents partial financial state/key erasure.
- [x] Exercise both faults on the shipping image; after repair, reconcile through
  normal recovery/retry or expose a specific safe blocker, never fabricate state.
- [x] Run appropriate regressions and final grouped E2E, retain all attempts and
  source/image hashes, update evidence/runbook/checklist and commit. Real Google,
  GCP, actual Demo/Live and production deployment remain excluded.

Final evidence: [partial/DB cleanup report](../../reports/release/admin-cleanup-fault-e2e.md).
New image `89d5e7c6…`, runtime fix `16b268737`, passes all four new cases and four
prior lifecycle cases. Native release scope passes 2,689 tests plus 11 subtests;
full PostgreSQL confirmation passes 452 tests, with the initial 300 ms probe
timeout retained and no exclusive cause claimed. A runner port-publication race
has six focused passing regressions. Ten unused bot images are removed; all
container references, candidate/rollback and volumes are preserved. This closes
the targeted follow-up, not a new full-image hour/upgrade acceptance gate.

### A6 follow-up — Partial-close crash and invalid readback coverage

- [x] Kill the shipping service after a partial close is committed, for both live
  and cancelled remainders; recover the same operation, close only the residual,
  preserve spot and prove all physical sends, fills, fees and final key removal.
- [x] Cover stale/wrong-generation/incomplete cleanup proof against PostgreSQL;
  rejected proof must preserve ciphertext, the shutdown fence and audit state.
- [x] Cover malformed/unavailable ticker reads and owner loss before close send;
  reject placement and leave cleanup authority out of the caller context.
- [x] Record exact image/test scope and results; keep real Google/GCP and existing
  Demo excluded, and commit the completed coverage.

The [follow-up report](../../reports/release/admin-cleanup-partial-crash-e2e.md)
records 25 additional cases and final targeted results: native52, PostgreSQL68,
two native-image E2E. Both crash cases reconcile six synthetic fills and three
physical close attempts, preserve spot and complete removal/restart on unchanged
image `89d5e7c6…`. No runtime fix or new full/hour gate is claimed.

### A6 follow-up — Lost responses and concurrent cleanup requests

- [x] Abort real test HTTP transport after committed Disconnect/Retry/Repair;
  replay idempotently before and after completion with one correlated child/root.
- [x] Race two Retry requests and Retry versus Repair against real PostgreSQL;
  serialize the same fence, reject stale transitions and preserve exact versions.
- [x] Reject a prepared Repair when the requesting account is disabled/downgraded
  before commit; preserve ciphertext, receipt, control and audit state.
- [x] Exercise a final-removal commit followed by an injected lost acknowledgement;
  durable readback/re-entry must not repeat closes or completion audit.
- [x] Run related checks, record the precise scope, update this plan and commit.

Ten additional PG cases pass in the related native110/PG102 regression. No runtime
defect reproduced or production/image change required. The initial test-field typo
and the exact HTTP/DB injection boundaries are retained in the
[existing resilience report](../../reports/release/admin-cleanup-partial-crash-e2e.md#further-cleanup-api-and-transaction-coverage--2026-11-06).

### Cleanup polling efficiency and dependency timeout classification

- [x] Reproduce repeated terminal-order reads and raw status-read timeouts with
  behavioral tests; retain first-pass REST verification and persistence checks.
- [x] Poll only orders still pending after their first verified terminal read;
  bound each refresh by the remaining polling deadline and reuse native readers.
- [x] Map status-read timeouts to transient `cleanup_orders_pending`, preserving
  tracked identities, credentials, cancellation and final ledger/flat proof gates.
- [x] Verify related native/PG cases and shipping-image cleanup recovery; record
  before/after request counts, exact image scope, results and any limitations.

Final source `f1a7429f4` / image `8cdadb21…`: native 2,719 + 11 subtests, related
PG 104, six native-image cleanup cases pass. Eleven new cases cover timeout,
deadline, delayed-fill final readback, cancellation and same-operation retry.
Controlled refresh counts fall nine → seven (22.2%); no overall MM latency claim,
new full/hour gate, real Google/GCP or existing Demo changes. See the
[optimization report](../../reports/performance/admin-cleanup-polling.md) and
[portable evidence](../../reports/performance/admin-cleanup-polling-evidence.json).

### Cleanup ownership loss, shutdown failure and snapshot cost

- [x] Reproduce ownership loss between cancel/close/verification/detachment and
  snapshot consumption; classify lost authority without TypeError or stale writes.
- [x] Test shutdown failures before removal, after completion and during another
  failure; preserve receipt/key/audit semantics and the intentional engine-restart fence.
- [ ] Measure full snapshot queries/payload/row-lock scope with a large tracking
  fixture before optimizing; avoid loading unused tracking JSON if justified.
- [ ] Preserve full tracking recovery for runtime preparation and all existing
  owner/control/operation fences; verify matching snapshots and stale-owner rejection.
- [ ] Run final related/native and rebuilt-image recovery checks, record scoped
  results, update documentation/checklists and commit completed phases.

## Implementation-readiness review

The review found design/integration gaps, not newly reproduced runtime defects.
The decisions above close them at plan level; their implementation tests stay open.

| Gap found in current code / earlier draft | Required decision |
| --- | --- |
| Trading receipts require session/start-switch-stop; Stop success only proves stopped engine state | Separate typed credential-operation API over the same table, with explicit cleanup completion |
| Env-only/lazy credential readers can reload removed keys or fail cold startup | Explicit source-mode contract, all-consumer provider wiring and no fallback/restart loop |
| Pending Disconnect cannot use normal rotation if its key no longer works | Narrow same-account cleanup-key repair and retry on the original durable operation |
| Ordinary shutdown preserves positions; both admission and REST gates reject closes after Stop | Scoped Disconnect authority and dispatch before teardown; preserve normal Stop/shutdown semantics |
| Candidate validation after replacement could destroy a usable saved connection | Read-only validation before commit, versioned transaction rechecks and durable account binding |
| Generic connector input metadata includes deployment-owned fields | Credential-only field allowlist and tested routing/identity injection |
| DB-backed auth was scheduled before its schema; role changes can race mutation admission | A1 includes storage foundation; permission checks and mutation commit have defined transaction ordering |

A1–A5 and all available A6 local/offline checks are complete. Final source
`3a3011d6f`, image `1bb946c3…`, passed the unchanged full load/sixty-minute soak,
all five faults, native managed cleanup/restart and retention gates. Native regression
passed 2,680 tests plus 11 subtests; final related PostgreSQL scope passed 148 tests.
The earlier full 442-case PG and 17-scenario E2E baselines retain their exact scope.
See [acceptance report](../../reports/release/admin-access-credentials-acceptance.md)
and [portable evidence](../../reports/release/admin-access-credentials-evidence.json).
Recovered probe episodes and the historical single HTTP timeout remain qualified,
not declared exclusively explained. Real Google sign-in, GCP KMS and reviewed
existing-Demo cutover need external resources and remain unchecked above. Offline
fixtures cannot close them; no production or existing Demo cutover was performed.

## Primary references

- [Google ID-token verification](https://developers.google.com/identity/gsi/web/guides/verify-google-id-token)
  — use verified subject identity and audience; email onboarding has ownership limits.
- [Cloud KMS envelope encryption](https://docs.cloud.google.com/kms/docs/envelope-encryption)
  — separate data keys from the KEK and use authenticated encryption.
- [Cloud Run IAM](https://docs.cloud.google.com/run/docs/reference/iam/roles)
  — deployment/service-account permissions remain part of the trust boundary.
- [OKX API documentation](https://www.okx.com/docs-v5/en/)
  — exchange authentication/permissions; local Disconnect does not claim exchange key deletion.
