# FE integration

This is the single handoff for the LIFE MM operations dashboard, implemented under
`dashboard/` in this repository. It combines the API contract, operator UX and configuration
ownership rules. The checked [OpenAPI contract](api/openapi.json) is the source for
request/response types; generate TypeScript types from it with
`pnpm --dir dashboard run api:generate` (verify with `api:check`).
See the [dashboard API foundation](../dashboard/docs/API.md) for client and test fixtures. Runtime API responses,
Swagger annotations and UI copy are English.

The primary operator may be non-technical. The dashboard should answer: is the
service available, are there working orders, what is blocking trading, how much
capital/exposure remains, and what should the operator do? Profit is secondary.
The dashboard foundation lives in `dashboard/`; the complete operations UI and
its browser acceptance remain delivery work.

The [admin access and encrypted exchange credentials plan](plans/ADMIN_ACCESS_OKX_CREDENTIALS_TDD_PLAN.md)
tracks phased implementation. Google whitelist/roles, bootstrap disable, audit,
local/GCP envelope encryption and managed credential Set/status/receipt routes are
implemented locally through A4b, including automatic Disconnect and same-operation
cleanup retry/key repair. Final release acceptance remains open. Integrate only routes in the current OpenAPI contract.
Basic bootstrap requires explicit `ADMIN_BASIC_AUTH_ENABLED=true`; disable it after
Google administration is verified. Actual Google/GCP verification awaits resources.

Exchange credentials use `EXCHANGE_CREDENTIALS_SOURCE=environment|postgres_kms`.
Environment mode keeps deployment ownership. Managed mode permits admin/write users
to submit complete credentials after acknowledged Stop, with read-only candidate
validation and a dedicated operation receipt. Saving never starts trading. Read the
[credential guide](exchange-credentials.md) for source/key setup and cutover limits.

## Managed connection integration

| Route relative to `/api/v1/admin` | Role | Use |
|---|---|---|
| `GET /credentials` | Any | Deployment-enabled exchange/connectors, input names, source and `disconnect_available` capability |
| `GET /credentials/{exchange}` | Any | Safe saved/loaded state, version, actor/time, operation link and Configure permission |
| `POST /credentials/{exchange}/{connector}` | Write/admin | Complete secret bundle, expected credential/control versions and `Idempotency-Key`; returns `202` |
| `GET /credentials/operations/{id}` | Any | Typed credential receipt; session may be null; separate from `/commands/{id}` |
| `POST /credentials/{exchange}/{connector}/revoke` | Write/admin | Stop bot-wide trading, cancel bot orders, close managed perpetual exposure, then remove selected key |
| `POST /credentials/operations/{id}/retry` | Write/admin | Versioned retry of the same blocked cleanup; no automatic Start |
| `POST /credentials/{exchange}/{connector}/cleanup-credentials` | Write/admin | Working same-account key to finish cleanup; includes root operation ID and all expected versions |

For OKX, submit `credentials: {api_key, secret_key, passphrase}`. These are input-only
secret fields. Site, Demo/live routing and strategy YAML stay deployment-owned.
Use the existing Stop workflow first; `bot_must_be_stopped` means its cleanup or
acknowledgement is incomplete. A new empty managed deployment can onboard without
an MM session. Existing unbound ledgers require reviewed account binding during
cutover; the API rejects attempts to silently adopt their history.

Poll the returned operation: `accepted/waiting_for_owner`, `applying/loading`, then
`succeeded/loaded` or `failed`. Receipt success describes loading, never active MM.
The current status additionally requires fresh matching owner/version evidence to
say Connected; historical receipt failure does not override later verified recovery.
After confirmation the operator explicitly chooses Start. Clear all secret inputs
from FE state after submission; receipt/status/audit cannot return them.

A lost response retries the same operation key and unchanged non-secret preconditions.
The first committed secret bundle wins; submitting different secrets with that same
identity returns the original receipt. A genuinely new save requires a new key and
fresh versions. Do not infer mutation success from a timeout or stale status.

### Disconnect UX

Keep ordinary **Stop orders** separate from **Disconnect exchange**. Stop retains
positions and credentials. Disconnect stops the whole bot, cleans up both markets
and removes only the chosen slot. Confirm once with clear English copy:

> Stop trading, cancel this bot's pending orders and close its managed perpetual
> positions at market price, then remove this saved connection. Market closure may
> incur fees and slippage. Spot holdings will remain. The API key on OKX will remain
> valid until you delete it there separately.

Use server `actions` for Configure, Disconnect, Retry cleanup and Repair cleanup key;
never infer permission from a role label alone. Previously configured slots remain
visible for cleanup even if removed from the current strategy YAML. Render
`configured=true` during cleanup: acceptance has not removed the key.

Poll the root credential receipt. Suggested labels for `result.stage`:

| Stage | English label |
|---|---|
| `waiting_for_owner` | Waiting for trading service |
| `checking_account` | Checking connection and saved positions |
| `cancelling_orders` | Cancelling pending orders |
| `closing_positions` | Closing perpetual positions |
| `verifying` | Verifying orders and positions |
| `detaching` | Removing saved connection |
| `waiting_for_dependency` | Waiting to retry |
| `action_required` | Action required |
| `removed` with `status=succeeded` | Disconnected |

Show `blocked_stage`, safe `reason_code` and the scheduled UTC `next_retry_at` when
present. Counts are **recorded** remaining orders/position rows, not an exchange-wide
live snapshot; absence of a count or zero alone cannot imply completion. Root final
proofs are `orders_terminal_verified`, `positions_flat_verified`, `ledger_confirmed`
and `consumers_detached`. A timeout, stale owner or unknown outcome keeps the
operation open, with credentials retained; never offer a force-delete button.

For an invalid/expired key, show **Repair cleanup key** and ask for a complete new
key for the same account. For a temporary dependency failure, show its scheduled
retry; optionally offer **Retry cleanup** when allowed. Submit the current slot
credential `version`, current control version and root `result.operation_version`.
Refresh all three after a conflict. Retry/repair responses are child receipts linked
by `cleanup_operation_id`; a child `succeeded/cleanup_completed` confirms completion
of its root, not removal of every companion key. Follow the root for final status.

Connection labels are Connected, Not configured, Checking connection, Disconnecting,
Action required, Disconnected and Status unavailable. Environment source instead
shows Managed by deployment. Connected means a fresh owner loaded the current key;
it does not mean MM is running. Disconnected confirms this bot's cleanup/removal at
that time; it makes no claim about future manual exchange activity. For technical
blockers, retain the operation/request ID and link to support and the exchange.

### Verified Cloud Run connection flow

The [2026-11-09 Cloud Run Demo acceptance](../reports/demo/cloudrun-google-credentials-acceptance.md)
exercises real Google sign-in and the existing APIs using the invited admin's
identity, including actual spot/perpetual fills and Disconnect closure. No new
endpoint is needed. Implement the flow in this order:

1. Bootstrap invites the email. The invited user signs in with Google and calls
   `/access/login`; an invitation alone is not a completed Google sign-in.
2. Read current `/status` and `/credentials/okx`. Submit one complete selected
   slot bundle, poll `/credentials/operations/{id}`, then refresh both endpoints
   before configuring the next slot. Set stays separate from Start.
3. Start with the current `control_version` and `/profiles.catalogue_hash`.
   Watch both the command outcome and observed status/quote activity.
4. Disconnect the selected slot, poll its root receipt to `succeeded/removed`
   with the four final proofs, then refresh all slots. Cleanup is bot-wide, but
   removal is per slot. To remove both slots, complete each operation serially;
   do not label the still-connected companion as Disconnected.
5. Reconfigure a disconnected slot with its **retained** version and the latest
   control version, wait for Connected, then offer Start. The observed cycle
   went0→1 on first Set, stayed1 when revoked and became2 on Set again.

There are separate version domains. Request `expected_version` comes from the
selected **slot.version**; `expected_control_version` comes from `/status`.
At the credential receipt root, `expected_version`/`effective_version` describe
**control** versions; `result.credential_version` describes the selected key.
Never use the root effective version as the next slot version or reset a revoked
slot's version to0. Optional intermediate proof fields can be null at the final
stage; require the four documented final proofs, not every optional proof field.

Trading `CommandResponse.payload` contains the command fields and has no
`actor_id`. Credential receipts have a different payload. Show who requested a
change using `/audit?operation_id={receipt_id}` and its verified `actor_email` /
role snapshot; use `/access/me` for the current viewer. Do not assume both receipt
payload schemas are interchangeable or substitute a shared Basic identity.

The acceptance used a temporary localhost Google relay and a private test runner;
it does not deliver a dashboard or qualify production browser/CORS/IAM routes.
Basic bootstrap remains enabled in that test deployment. Disabling it after
verified Google administration is a separate deployment change.

## Start here

1. Agree on the service URL, Cloud Run network/IAM access and authentication route.
   Internal ingress is not made accessible by CORS configuration.
2. Generate the FE types from [OpenAPI](api/openapi.json). Review the
   [connection contract](#connection-and-openapi) and
   [configuration ownership](#what-can-change-and-when).
3. Implement the [five screens](#five-screens), starting with Overview and the
   [session command workflow](#session-command-workflow).
4. Preserve [freshness and status semantics](#service-health-and-working-orders),
   [money/chart semantics](#financial-and-chart-semantics), and
   [polling rules](#polling-and-availability).
5. Complete the [browser acceptance checklist](#browser-acceptance-checklist).
   Backend tests and simulator results are separate evidence.

## Connection and OpenAPI

All operational paths below are relative to `/api/v1/admin`. Health probes remain
separate. Enable `ADMIN_API_ENABLED=true` and configure the accepted sign-in method
through the existing dotenv/deployment settings:

- Bootstrap: `ADMIN_BASIC_AUTH_ENABLED=true` with `ADMIN_API_USERNAME` and
  `ADMIN_API_PASSWORD`. This is a shared administrator identity.
- Google: `ADMIN_GOOGLE_AUTH_ENABLED=true`, `ADMIN_GOOGLE_CLIENT_ID` and PostgreSQL.
  FE uses Google Identity Services with the same client ID and sends the Google
  ID token in `Authorization: Bearer …`. The backend verifies identity and current
  whitelist/role on every request. Use `/access/login` before starting dashboard polls.

There are no usable password/audience defaults. The API does not issue its own
tokens or sessions. Initial Google sign-in binds the approved email to its verified
subject; a pending invitation alone cannot replace a working Google administrator.

Use TLS for the hosted dashboard. Exchange credentials and recovery confirmation
tokens stay out of FE builds, URLs, logs and persistent browser storage. The React
dashboard persists its verified Google ID token in scoped localStorage and restores
it through `/access/me` while unexpired; this client behavior does not add API-issued
sessions or refresh tokens. Google tokens stay out of builds, URLs and logs.
Direct browser requests send an explicit
`Authorization` header and `credentials: "omit"`. A gateway route needs its own
browser authentication and forwarding behavior; that deployment decision is not
implemented by the bot API.

For the internal Cloud Run service, the proposed integration is browser → FE
backend/gateway → bot. The gateway forwards the individual user's Google ID token
and uses an ingress path permitted by the deployment. A shared Basic identity
must not substitute for users and bypass per-user roles/audit.
When Cloud Run IAM invocation is also required, send the platform ID token in
`X-Serverless-Authorization: Bearer …` and retain `Authorization: Bearer <user ID token>` for the
bot. Using the same Authorization header for both would lose application auth.
Google documents this [two-header invocation](https://docs.cloud.google.com/run/docs/authenticating/service-to-service).
This is a deployment integration contract, not a gateway shipped by this repo.
Do not add the platform token/header to direct browser calls: the existing bot
CORS contract does not allow that header. CORS or valid Basic Auth alone cannot
make an internal/IAM-protected service reachable. Agree on this path before FE
browser acceptance; use the [deployment worksheet](cloudrun.md#deployment-worksheet).

`ADMIN_CORS_ORIGINS=*` supports any FE origin without
`Access-Control-Allow-Credentials`; an explicit comma-separated origin allowlist
is also supported. The preflight allowlist is Authorization, Content-Type and
Idempotency-Key. Do not add `X-Requested-With` or other custom headers without
corresponding backend support. `X-Request-ID` and `Retry-After` are exposed for
operational error handling. A browser network/CORS failure does not prove the
password is wrong or the bot is stopped. On 401, suspend automatic submissions
and ask the operator to reconnect.

### Accounts, roles and audit

For real Google sign-in acceptance before the dashboard exists, use the
[loopback helper](../scripts/google_login_local.py):

```sh
python -m scripts.google_login_local --env-file /path/to/auth-local.env
```

This helper serves `http://localhost:3000`, reuses the production Google verifier,
access handlers and PostgreSQL audit, and never starts the trading engine. Only
access/bootstrap/account-list/account-invitation/audit routes are exposed; trading
and exchange-credential routes return 404. It requires a separate PostgreSQL
database named `hbot_auth_*` on `localhost` or `127.0.0.1`, applies existing SQL
migrations there, and uses the supplied env file without loading exchange secrets
from the shell or repository `.env`. Include `DATABASE_URL`, `STORAGE_BACKEND=postgres`,
`BOT_ID`, `ADMIN_API_ENABLED=true`, the Google settings above, and the temporary
Basic bootstrap settings in this private env file. Keep it outside Git.

Register `http://localhost:3000` as a Google JavaScript origin. Invite the test
email through the existing account API using Basic before sign-in. Tokens are
kept in page memory only; the page displays identity, bootstrap readiness and the
latest ten audit entries. Clearing the page is not Google sign-out or revocation.
An invitation (`bound=false`) and successful public-certificate discovery do not
close real-login acceptance: the operator must complete Google sign-in, then
verify identity/role/audit and Google-only access after disabling Basic and
restarting the helper. This separate auth database does not provision accounts
in the eventual trading service database.

| API | Permission | FE behavior |
| --- | --- | --- |
| `POST /access/login` | Verified enabled identity | Header-only sign-in; returns identity, English role label and permissions; records sign-in |
| `GET /access/me` | All roles | Refresh current identity/role; permissions are role grants, combined with runtime/action availability |
| `GET /access/bootstrap` | Admin | Check bound Google administrator readiness before changing auth environment flags |
| `GET /access/accounts` | Admin | Cursor-paginated approved accounts, role, enabled/bound state and version |
| `POST /access/accounts` | Admin | Invite `{email, role, active?}`; 201 is committed, duplicate email returns 409 |
| `POST /access/accounts/{id}` | Admin | Change `{expected_version, role?, active?}`; protects last bound active Google admin |
| `GET /audit` | All roles | Cursor-paginated security history with verified actor snapshots and request/operation IDs |

`read` is Viewer, `write` is Operator and `admin` is Administrator. Operators can
control trading; administrators additionally manage accounts. After any 403,
refresh `/access/me` and explain permission/access changes. Keep credentials and
Google tokens out of account request bodies. Changing a role/active flag takes
effect on the next request, including with an already issued Google token.

Bootstrap flow: enable both methods, invite a Google admin, let that person sign
in, verify `/access/bootstrap.google_admin_ready`, then redeploy/restart with
`ADMIN_BASIC_AUTH_ENABLED=false`. No API enables Basic again. Remove older exposed
revisions and the unused password reference. Disabling Basic never disables the
Google `admin` role. Google-only operation without a bound administrator fails
closed with `bootstrap_required` and requires maintainer recovery.

Audit filters: `from_time` inclusive, `to_time` exclusive (timezone required),
`actor_id`, `action`, `outcome`, `target_id`, `operation_id`, `exchange`, `connector`,
`environment`; default limit 100, maximum 500. Keep filters unchanged while using
`next_cursor`. IDs are strings; timestamps are UTC. Successful changes/audit commit
together; explicit sign-in/mutation denials also have durable history when the bounded
audit write completes. Ordinary polling and anonymous request failures use structured
platform logs. DB outages or admission timeouts can prevent a denial-audit row;
request and verified actor IDs remain in correlated platform logs. A failed mandatory
mutation audit rolls back the mutation.
There is no audit edit/delete API. Account management uses synchronous committed
responses; a 409 means refresh/review, not blind resubmission.
An `accepted` audit event records admission of a control request; use its linked
command receipt to inspect later runtime success/failure. It does not assert that
an exchange operation already completed.

Generate the offline schema and dashboard TypeScript types together with
`make swagger`; check both with `make swagger-check`. Dashboard dependencies must
be installed first. The Python CI lane uses `make swagger-backend-check`, while
frontend CI runs `api:check`. Handler annotations and Pydantic models supply the contract.
Protected `/docs`, `/openapi.json` and `/docs/assets/{asset}` exist only when
`ENV=dev` or `development`, case-insensitive. The default is dev; other values
return 404 for these documentation routes. The production template explicitly
sets `ENV=production`. Swagger uses packaged assets without a CDN/remote validator
and does not persist authorization. Production FE builds use the checked schema,
without depending on the production HTTP documentation routes.

`ENV` controls documentation availability. `OKX_TRADING_ENV` independently selects
exchange routing. All timestamp responses use UTC `Z`; monetary values use Decimal
strings. Keep one small API client for authentication, errors, idempotency and retries.

For managed credentials, changing routing does not convert an existing database's
account/environment binding. A Demo database started in Live blocks trading with
missing Live credentials and rejects attaching a Live key with
`409 exchange_account_mismatch`; it never falls back to environment keys. Display
the current deployment environment and connection status rather than offering a
Demo/Live session toggle. Independent Live operation needs its own bot/database or
a reviewed cutover. Returning to Demo can resume preserved running intent; stopped
intent stays stopped. A wrong local KEK also blocks cold loading while admin remains
available. Only the maintainer can restore deployment key settings; FE cannot reset
the binding or regenerate the KEK. See [credential configuration](exchange-credentials.md).

## Configuration ownership

API commands control sessions; the access API manages the persisted whitelist and
roles. Maintainers change strategy/profile settings in
YAML and service settings in the process environment. No API edits or overrides
either file, including an override equal to YAML or set to null.

`DB_POOL_SIZE` is an environment-only total PostgreSQL pool ceiling per service
instance (default 5, range 5–50): API two slots, engine the remainder. Restart the
whole service to apply environment changes; child restarts do not reload dotenv.
It is not an editable FE setting or API override. Admin DB concurrency remains
capped at two workers. Existing durable sessions keep their pinned catalogue;
restarting a child does not apply new YAML settings to a running session.

### What can change, and when

| Setting | Source of truth | API editable? | When a change applies |
|---|---|---|---|
| Start, switch, stop | Durable PostgreSQL command/session | Yes | Accepted intent is applied by the owner; inspect receipt and fresh status |
| Spot, perpetual or both | Session control, constrained by YAML market templates | Yes | New start/switch; never a patch to a running controller |
| One profile or Random | Session control using deployed profile IDs | Yes | New start/switch |
| Duration mode and timed duration | Session control; timed bounds/default and until-stopped permission from YAML | Yes | New start/switch; timing begins at joint readiness |
| Permission for continuous sessions | YAML `allow_until_stopped`, default false if absent | No | New sessions after deployment; packaged normal/Demo presets explicitly enable permission |
| Follow pairs and weights | `life_mm_profiles.yaml` profiles | No | New session after the updated YAML is deployed |
| Random pool and step duration | YAML `random.profile_ids`, `step_duration_seconds` | No | New session after deployment |
| Trading pair, budgets, spreads, leverage/margin, price/inventory limits | YAML market templates | No | New session after deployment and normal account/readiness validation |
| Guards, bootstrap, drawdown, outflow, inventory/spread policies | YAML plus existing durable policy bindings | No | New session only where compatible with bound policy; conflicts require technical review |
| Exchange credentials in `environment` mode | Process environment / deployment secrets | No | New process with reviewed environment |
| Exchange credentials in `postgres_kms` mode | Encrypted PostgreSQL bundle and durable credential operation | Admin/write may Set after acknowledged Stop, Disconnect, or repair blocked cleanup | Validate candidate, commit receipt, wait for confirmed engine outcome; never auto-start |
| Credential source/provider, KEK, database, auth/CORS, logging, intervals | Process environment / deployment secrets | No | New process with reviewed environment |
| Swagger availability | `ENV`, default `dev` | No | New process; only `dev`/`development`, case-insensitive, expose UI/schema/assets |
| Missing working-order warning | `MM_NO_QUOTE_WARNING_SECONDS`, default 120; 0 disables | No | New process; monitoring only, continuous window resets after restart |
| Exchange live/demo routing | `OKX_TRADING_ENV` and matching credentials | No | Deployment-level account/environment change; not a session switch |

Environment precedence is existing process values, then local dotenv, then shared
defaults. YAML profile settings are not an additional layer of API overrides.

`duration_mode=timed` is the default. `until_stopped` must omit duration_seconds
and be offered only when `/profiles.duration_modes` advertises it. It removes
the overall session deadline, not risk limits or the finite random step windows.
Permission is in YAML, not `.env` or a writable command field. The API must reject
an attempted `allow_until_stopped` override.

For the packaged Cloud Run image, YAML is copied into `conf/` at build time.
Changing a file in the repository requires a new image/deployment before a new
process sees it. This API does not reload profile files in a running process.
If a deployment mounts configuration instead, restart the process to load it.

This API revision includes idempotent migration 017 for session-history indexing.
With `ENABLE_AUTO_MIGRATION=false`, apply `make migrate` before starting the new
image; startup still validates schema compatibility. With it enabled, the normal
startup migration path applies the script before trading admission.

### Requested, deployed and pinned configuration

- `GET /profiles` describes the responding process's deployed catalogue and its
  `catalogue_hash`. It offers settings for a new session.
- `GET /status` describes requested and observed state separately. Its
  `session_configuration_url` points to that session's pinned effective settings.
- `GET /sessions/{id}/configuration` returns the persisted session snapshot or
  its creation event. The response labels current versus creation-time profile
  context. Missing historical configuration is 404, never a substitute from today.

Send `expected_catalogue_hash` from the reviewed profile response with start or
switch. If another revision has different YAML, `catalogue_changed` asks the FE to
refresh and let the operator review again. The precondition is optional for old
clients, so the FE must include it to receive this protection. A matching retry
returns its original receipt even after YAML changes; use the same body and key.

The catalogue hash describes validated effective YAML; the session `config_hash`
also describes the effective request. Neither is a secret or a field for an
operator to type. Put hashes, revision IDs and technical diagnostics in Details.
Configured process/owner environment identity is not exchange-account verification.

### Restart and rollout examples

**Restart while active.** A session follows the saved BTC/ETH basket with a
one-hour deadline. Restart restores that basket, anchors, current random step and
absolute deadline. It does not grant another hour or draw another random step just
because the process restarted. An elapsed deadline follows the existing expiry
policy. Runtime recovery/readiness checks still apply before orders are allowed.

**Restart a continuous session.** Its `expires_at` stays null and Stop remains
explicit. Fixed selection restores its anchors; random restores the current
window or skips elapsed windows using the saved start time. Restart never renews
bootstrap, clears bound risk limits or permits an old generation to send orders.
Changing current YAML permission to false does not rewrite this pinned session.
Switch to a timed session or Stop through the existing API when desired.
If startup spans a random boundary, initialization finishes before automatic
rotation skips elapsed windows; expired-step sends remain blocked. Stop/Switch
and overall timed expiry keep their normal cleanup behavior.

**Deploy different YAML.** The repository now follows AVAX and has a different
budget. An existing persisted session continues to use its old snapshot. The UI
shows Active session settings separately from Available settings. A new reviewed
start/switch uses the new catalogue. Switch replaces the session and starts its
new timer at readiness; it does not flatten held positions.

**Change a bound risk policy.** Some risk, bootstrap and outflow policies retain
durable scope/deadlines/reservations. Deploying different limits may be rejected
with a policy-bound conflict. Stop/start, a restart, a new key or a new API profile
must not be presented as a way to reset HALT or bypass those bindings. Follow the
[technical recovery runbook](postgres-runtime.md); the boss-facing action is
“Contact support,” not a database-edit instruction.

**Read a legacy receipt.** Older stored requests may contain reference or random
overrides. Read models retain them for history and restoration, with
`legacy_overrides` on the session configuration response. Copying those fields
into a new command is rejected with 422, even if null or equal to current YAML.

Older runtime binaries do not understand the new persisted duration/catalogue
fields. Use a compatible runtime for saved sessions; automatic downgrade/reset
and old-image rollback are not provided by this feature.

## Five screens

| Screen | Main content | Existing API, relative to `/api/v1/admin` |
|---|---|---|
| Overview | Service/owner health, requested vs observed session, countdown, current own quotes/spread/coverage, blockers, shared balances and held exposure | `/status`, `/statistics/liquidity`, `/balances`, `/positions` |
| Run session | Market; one profile or Random; duration; read-only references/budget/limits; review of requested change | `/profiles`, `/sessions/{id}/configuration`, `/commands`, `/commands/{id}` |
| Orders and fills | Separate market filters, open/closed orders, fills/fees, order detail drawer | `/orders`, `/orders/{id}`, `/fills` |
| Activity and diagnosis | Session transitions, startup waits, command outcome, reconciliation scope/differences | `/sessions`, `/commands`, `/sessions/{id}/events`, `/status`, `/reconciliation/{id}`, `/reconciliation/check` |
| Daily statistics | Volume, fills, paid fees/rebates, observed funding, shared account balances by reporting date; optional controller performance | `/statistics/daily`, `/performance` |

The top bar shows API connection, Demo/Live, bot/pair, service/session state,
last observation and **Stop orders**. Use the identity fields described below;
show unknown/mismatch explicitly rather than inferring the environment from a URL.
Shared balances appear once; spot and perpetual have separate quote/exposure cards.

An overview wireframe (not a shipped UI):

```text
Bot / pair    DEMO or LIVE    Session status              [Stop orders]
Last updated / data quality   Current profile   Time remaining

Attention: reason / affected market or side / next action / last retry

Spot liquidity                       Perpetual liquidity
Own bid / ask / spread               Own bid / ask / spread
Remaining own notional per side      Remaining own notional per side
Sampled two-sided coverage / gaps    Sampled two-sided coverage / gaps

Shared account cash/equity           Positions LONG / SHORT + margin
Spot BUY capacity / reservations     Bound drawdown / guard state

Latest command + session events      Daily fee/volume/funding summary
```

## Service health and working orders

`/status.service` reports service availability separately from trading readiness:
phase/reason, observation time, observer progress age and at most sixteen current
incidents. Incidents carry a stable reason code, component scope, severity,
sanitized exception class, first/last occurrence, last healthy observation,
retry count/next retry and whether operator action is required. Controller
initialization read retries appear here while the same task waits/retries;
they do not create another strategy or reset session deadlines.

During a guarded automatic database update, a new engine can report
`phase: standby`, `reason_code: migration_pending`. Present this as **Update
waiting**: the previous process must finish before the update continues. This
revision can pass `/startup` with a compatible schema, but `/ready` remains503
and it cannot acquire trading ownership until pending migration completes.
The old owner may still trade; use the existing owner/session observation to
describe trading activity. Do not infer that all trading has stopped from the
new revision's standby phase. An incompatible schema instead fails startup and
requires a maintenance window. Migration policy is ENV-only, not an API override.
`phase: migrating`, `reason_code: migration_in_progress` means the bounded update
step is in progress; show **Updating**, with this revision's trading readiness
still false. Responsive health remains bounded by the existing bootstrap deadline,
then returns to the ordinary observer budget.

If the control DB cannot be read while this process is still responding, `/status`
returns HTTP 503 with `error: unavailable` and local `service` diagnostics labelled
`state_source: process`, `persisted_state_available: false`. It does not return a
fabricated owner/control version or accept commands without durable storage.
Ordinary status uses the owner's stored observation, marked stale when that owner
is unavailable. A reporting projection failure marks prior account/position/PnL
observations stale even while the trading owner remains healthy.

Do not derive one green “Running” badge from HTTP 200, `/live`, `running`, a command
receipt or one market's `ready` field alone.

| Observed condition | Operator presentation |
|---|---|
| Connection/auth failure | Cannot observe service; retain prior values as stale, not zero |
| Status 503 with `service.state_source=process` | Process responding; durable control state unavailable; no fabricated version/session |
| `owner_available=false` | Owner unavailable; invalidate current-trading interpretation of nested saved observations |
| Requested and observed session IDs/generations differ | Applying change; show old observed and new requested configuration separately |
| Starting/preparing with no `started_at` | Preparing connections/data; session-duration countdown has not started |
| Healthy owner, matching session, `phase=running` | Strategy admitted; actual quote presence still comes from fresh liquidity observations |
| Fresh liquidity with one/no side | One-sided/no quotes observed; inspect per-market and BUY-only guards, balances and caps |
| Readiness wait/recovering guard | Service available; quotes constrained; show reason/retry/recovery timing |
| Stopped/expired | MM idle; continue displaying held exposure and reporting freshness |
| HALT/operator action required | Admin: offer the reviewed recovery flow below; other roles: contact an administrator. Risk or incomplete evidence remains blocked. |

Cloud edge errors during maintenance can return HTML/plain text for HTTP502/503/504.
Handle the HTTP status even when the body cannot be parsed as JSON: show **Service
temporarily unavailable**, keep prior observations stale and retry reads. Do not
render a raw JSON-parser exception or automatically repeat trading mutations.

Risk observation failures have separate meanings:

- `drawdown_controller_limit` / `drawdown_global_limit`: a configured protection
  stopped trading. Show **Trading stopped — risk limit reached**, the affected
  controller and configured threshold, with held exposure still visible. This is
  distinct from a connectivity failure or unknown service crash. After teardown,
  current PnL/drawdown may be null/stale; do not fabricate an exact breach value
  from the limit or an earlier sample. Retain the saved reason/peaks and operator
  action requirement; reconnecting or restarting does not reset the protection.
- `spot_outflow_checkpoint_invalid` / `drawdown_checkpoint_invalid`: saved risk
  data cannot be verified. A fresh operator summary shows **Risk data needs review**,
  disables Start/Switch and retains Stop. Contact support; do not reset or delete
  checkpoints through the UI. A stopped service remains available. A running intent
  is fenced through existing recovery HALT while its original checkpoint is retained.
- `spot_outflow_observation_unavailable` / `drawdown_observation_unavailable`:
  the risk panel cannot currently be rendered. Its value may be null; show
  **Risk status unavailable**, not **Disabled**. Runtime risk checks remain
  independent and can keep quoting blocked. Retry normal status polling and
  display the scoped incident; do not auto-start or weaken a guard.

Stale incident observations do not grant or revoke trading authority. Read
`recovery.halted` separately; a checkpoint warning while stopped does not invent
a durable HALT. Ownership, recorder and required checkpoint-write failures retain
their critical recovery behavior.

Quote guards, spread fallback,
drawdown and spot outflow have separate effects. For example, a spot BUY denial
does not imply funded SELL or perpetual admission is blocked. A disabled optional
policy is labelled **Disabled**, not **Healthy** or **Failed**.

### Working orders, fills and missing-order warnings

A quote is a resting buy or sell limit order placed by the bot. A fill is an
execution against an order. Working orders can provide liquidity without fills.
The operator UI should say **Working orders**, rather than require understanding
of the word quote. API machine fields retain `quote_activity`.

`GET /status` returns `quote_activity[market]`; `GET /statistics/liquidity`
returns `markets[market].quote_activity`. Both carry the same sampled observations. GET reads add no exchange requests
or extra state queries. Spot and perpetual have separate activity and BUY/SELL
reasons; the top operator summary reflects missing or one-sided orders separately
from the engine's `running`/`phase` fields.

| State | Meaning for operators |
|---|---|
| `orders_working` | Acknowledged working buy and sell limit orders are present |
| `one_sided` | Working orders exist on only one side; inspect the other side |
| `no_quotes` | A fresh sample has no acknowledged working limit orders |
| `unavailable` | Current orders cannot be confirmed; retain last-good values as stale |
| `idle` | Session is stopping, stopped, expired or not yet actively admitted |

`fill_observation_basis=current_working_orders` is deliberately limited to the
current working orders. `no_fills_on_working_orders` means none of those orders
has an observed executed base amount. It does **not** mean there have been no
earlier session/account fills: filled/cancelled orders may already have left the
working tracker. Use `/fills` for recorded history. A no-fill observation is not
a service failure and does not trigger a missing-order warning.

Each side has its observed `order_count`, English detail, reason codes and
`proposed_count` from the last actual controller evaluation. A proposal is not
exchange acceptance. Missing-side reasons include insufficient quote/base funds,
inventory/allocation constraints, spending-guard denial, size/price/spread checks,
active executors and cooldown. `spot_outflow_blocked` is a guard denial, not proof
that the spending ceiling was reached: inspect `/status.spot_outflow`, including
coverage, pending terminal orders and the last BUY denial, for its specific cause.

Proposal diagnostics expire after five seconds. Their original UTC
`proposal_observed_at` stays visible; an outdated or absent diagnosis becomes
`quote_reason_unavailable` instead of repeating an old balance denial as current.
Fresh controller readiness reasons remain useful during guard/data waits.

Each missing side may also carry an optional `diagnosis` (old stored snapshots
return null). Render `next_action` as English operator guidance alongside existing
`reason_codes`; it never authorizes a restart, changes YAML or bypasses a guard.
Show that guidance by default; keep `stage`, `source` and `error_type` in an
expandable technical-details panel for maintainers.

| Diagnosis field | Meaning |
|---|---|
| `stage` | `submission_pending`, `cancel_confirmation`, `executor_report`, `executor_activity`, `cooldown`, `proposal_confirmation`, `trading_conditions` or `unknown` |
| `source` | Local `order_tracker`, `executor_event`, `controller_observation`, or explicitly `unavailable` evidence |
| `observed_seconds` | Consecutive sampled time in the same stage/reason, including a missing side when the other side works; not individual order age or measured downtime |
| `next_action` | English guidance, escalating persistent unresolved pipeline waits to contacting support at the existing monitoring threshold |
| `error_type` | Fresh proposal exception class only, such as `ValueError`; null when absent, outdated or invalid. Exception messages are never included |

Local pending creation does not prove that transport started or OKX accepted an
order. A pending executor report does not identify a particular failed task.
`unknown` is retained when there is no reliable stage evidence; do not invent a
root cause. Working/idle/unavailable sides have no active diagnosis. Gaps, owner/
generation changes, restart, invalid samples, stage/reason changes and observed
recovery reset the sampled age. Repeated API reads do not advance it. Stop and
stale-owner projections clear diagnosis rather than repeat an old wait as current.

`MM_NO_QUOTE_WARNING_SECONDS` is a process environment setting, default **120**;
valid values are `0` (disable warnings) or 5–86,400 seconds. It cannot be changed
by the command API. It controls monitoring only. During a running, started session,
`no_quote_seconds` accumulates only consecutive valid samples of complete order
absence within five seconds, for the same owner and session generation. Any
working side, unknown sample, gap, restart or generation change resets that
continuous window. Stop/expiry clears a current warning. API reads do not advance
the sampled duration. A brief refresh gap can show `no_quotes` before it warrants
a warning; do not show an error toast on each poll.

At the threshold, `warning_active=true` and the existing service logger emits
**WARNING** `mm_no_quotes_warning`, with bot/session identity and a market-specific
`scope`. Repeated warnings are limited to once per minute per market. Clearing the
warning emits **INFO** `mm_no_quotes_warning_cleared`; its `reason_code` may be
`unavailable` or `idle`, so this log alone does not prove that orders returned.
The warning message includes bounded `BUY_stage` and `SELL_stage` values, also
available as JSON log fields `buy_wait_stage` and `sell_wait_stage`. No additional
order scan, exchange request or API database query is added for these diagnostics.
These are API/log signals, not email/Slack notifications or automatic repair.

All diagnostic paths reuse existing quote decisions and the order tracker. They
do not restart sessions, reset deadlines/HALT, change limits or submit trades.

### Reviewing quote availability

Keep service health, working-order availability and fill activity as separate UI
signals. Review each market and each side using the existing fields above:

| Situation | Evidence and operator interpretation |
|---|---|
| Session admitted, fresh data, both sides funded and unconstrained | Fresh acknowledged working orders on both sides are the expected MM outcome. A running process alone does not satisfy this check. |
| Replacement after refresh, fill or a changed safe quote | Show pending creation/cancellation, executor-report or cooldown progress and sampled stage age. A brief empty sample is possible; do not treat an old order's age as an engine delay or allow a pending stage to explain an indefinite wait. |
| Guard, inventory, collateral or spending limit restricts a side | Show the affected side, fresh reason and `next_action`. One-sided/no orders can be intentional; do not weaken the guard or automatically restart to obtain two-sided orders. |
| Observer/feed/owner evidence is stale or incomplete | Show unavailable/stale. Neither a last-good book nor HTTP200 establishes present working orders. Unknown samples are not proof of either continuous quoting or continuous absence. |
| Session stopped/expired | Show idle and inspect outstanding orders/exposure separately. This status does not by itself prove that an account is flat. |

Filled-entry handoff (source fix after revision52) uses the existing position-hold
shutdown. Exact locally observed fills must cover the whole acknowledged entry,
and a fresh native account observation must cover its last fill timestamp. The
old level remains reserved until terminal bookkeeping; then the controller can
propose a new quote without waiting for the scheduled refresh age. Partial fills,
missing account observations and independently configured exit barriers retain
their existing lifecycle. This adds no API configuration override or timer change.
The revision52 post-fill gap remains historical evidence until a changed image
is deployed and verified; a running process is not proof that every level works.

Ordinary age-based refresh can optionally use YAML
`quote_refresh_max_delay_seconds` (default0, maximum10, no longer than the refresh
interval). With multiple levels on one side, it retires one level while retaining
another acknowledged, still-valid entry; a pending replacement is insufficient.
The last valid level waits only until its original refresh age plus this bounded
delay. Single-level/default batch behavior, risk withdrawals and full-fill
handoff remain separate. A safety withdrawal or an expired delay can still leave
a side empty. The bound covers controller deferral, not venue cancellation
latency. This reduces routine replacement gaps; it does not guarantee99%
coverage, extend an exchange order's validity, or authorize an API override.

For a slow-probe review, correlate the actual transition timestamps with engine
ticks/controller cycles, submission/cancellation outcomes and independent venue
observations when retained. Distinguish a method attempt/return from exchange
acceptance or terminal readback. Samples before/after an incident do not prove
what happened between them; missing/overflowed captures remain unknown. The local
activity diagnostic observes a simulator on a shared clock, not a remote OKX
server, and does not retroactively add activity evidence to another load run.

These are interpretation/acceptance criteria for the existing API contract.
`MM_NO_QUOTE_WARNING_SECONDS` (default **120 seconds**) remains a monitoring threshold, not a
maximum quote-replacement time or a production quote-coverage SLO. Numeric
coverage/absence targets, eligible-market periods and guard/bootstrap exceptions
still require an explicit operating policy; no new numeric SLO is silently agreed
or enforced by this documentation.

#### Initial operating targets (approved 2026-11-09)

The operator approved the following initial targets on 2026-11-09. Agreement on
the policy does not claim that a three-minute diagnostic qualifies its
thirty-minute measurement window:

| Target | Measurement and interpretation |
|---|---|
| At least 99% sampled two-sided availability over 30 minutes | Evaluate each market separately while the session is admitted, both sides are funded, and no fresh explicit guard/inventory/bootstrap restriction applies. Ordinary replacement remains in this denominator. |
| Unexpected absence longer than 20 seconds | Warn and inspect pending submission/cancellation, cooldown and the current denial diagnosis. Do not infer an engine failure from an old order's age. |
| Unexpected absence longer than 60 seconds | Escalate for operator investigation; no automatic guard relaxation, repeated Start or blind order resubmission. |
| Stale/missing observations | Report unknown separately. Unknown time cannot count as successful quoting; coverage reports must show both unknown and explicitly restricted time rather than hiding them in an eligible-only percentage. |

Keep the total session-time availability view beside the eligible-period target.
An intentional restriction can preserve service availability while removing a
quote side, and must remain visible to the operator. A short diagnostic with
180/180 two-sided samples is finite point-sample evidence, not proof of continuous
exchange quoting or an approved listing configuration.

Deployments adopting these targets must intentionally select the warning threshold
through `MM_NO_QUOTE_WARNING_SECONDS=20`; the compatible default remains 120.
The 60-second escalation and 30-minute review window are operating/FE
rules, not new engine timers. No production or repository `.env` was changed by
this policy agreement. Policy acceptance and measured qualification remain
separate; the local diagnostic is not a thirty-minute qualification.

### Session freshness and operator messages

Use `server_time` and absolute expiry for a local countdown, periodically resynced;
do not require one HTTP call per displayed second. If the latest status is stale,
label the countdown as based on last observation. Countdown reaching zero does
not prove that shutdown/cancellations completed.
For `session.request.duration_mode=until_stopped`, show **Until stopped** instead
of an end countdown. Keep the random step countdown separately. A null expiry
alone is insufficient to infer this mode: timed sessions are also null before
their readiness clock starts.
Keep startup, market waits and risk blocks visible for continuous sessions too;
never infer that every elapsed random window traded.

Use `status.operator.label`, `detail` and `next_action` for the main English
message; `actions` gives advisory button availability and blocked reasons.
`process_identity` describes the responding revision; `owner_identity` describes
the published trading owner's configuration. These are configured identities,
not exchange-account verification. `observation` discloses stale/mismatched
session data. `recovery` contains safe flags and technical-review guidance.

Show text/icons along with color. Keep raw phase/reason codes, generations,
versions, hashes and request/revision IDs in Details. Unknown codes use a neutral
“Status needs review” fallback. Announce status changes without stealing focus
or displaying another toast on every poll. See W3C guidance on
[error identification](https://www.w3.org/WAI/WCAG22/Understanding/error-identification)
and [status messages](https://www.w3.org/WAI/WCAG22/Understanding/status-messages.html).

### Policy observations

Optional `status.markets[].quote_guard` reports strict guard admission, qualified
market price, deviation, UTC observation/recovery times and source quality. The
catalogue exposes opt-in market `quote_guards` and profile `sideways_range` through
the existing `/profiles` contract; trading selection still uses existing commands.
See [strict quote guards](life-mm-controls.md#quote-guards) for bounds and recovery semantics.

Optional `status.drawdown` exposes the covered V2 report's limits, stable scope,
peaks, observed PnL/drawdown and readiness/HALT reason. Stopped or stale current
PnL is `null`; known peaks and confirmed breach remain visible. This metric
includes reported trade fees and excludes funding; it is not account equity.
See [managed MM drawdown](life-mm-controls.md#drawdown) for configuration and bound policy.

Optional `status.spot_outflow` exposes the bound rolling spot USDT policy,
committed/reserved/remaining capacity, original non-USDT fees and BUY-only
coverage/denial reasons. Capacity loss does not stop funded SELL or perpetual
admission. See [spot outflow](life-mm-controls.md#spot-outflow) for provenance, window and
terminal-proof semantics. Owner-unavailable observations are marked stale.

Optional `status.markets[].spread_policy` reports static/dynamic/fallback/wait
state, maker fee-floor enablement, cached NATR multiplier, UTC candle freshness
and effective per-side spreads. Session anchors and reference formulas are
unchanged. See [fee/NATR spread policy](life-mm-controls.md#fee-and-volatility-spreads).

Optional `spread_policy.defense` explains volatility restrictions in English:

| State | Suggested operator presentation |
|---|---|
| `normal` | Normal order sizing |
| `reduced` | Trading with smaller orders |
| `static_fallback` | Trading with smaller orders using fallback spreads |
| `paused` | Waiting for volatility to settle |
| `waiting` | Waiting for market data |
| `checkpoint_pending` | Waiting for recovery state confirmation |
| `unavailable` | Volatility status unavailable; wait for a fresh snapshot |

Use the returned `label`, `next_action`, `reason_code` and global operator state.
`raw_natr` is a fraction from fresh completed candles, not a percent string or
the clamped spread multiplier. Money/factors remain Decimal strings and times
remain UTC. `effective_allocation_quote` describes the current proposal budget;
it is not remaining wallet cash, cumulative session spending or maximum loss.
Startup still applies the existing conservative check of full declared selected
market budgets against available USDT. The BUY/SELL split or reduced allocation
does not lower that admission requirement.
Show `cancellation_pending` separately from observed working orders: a request to
cancel does not prove that orders have disappeared. Null means unknown.

Recovery progress exposes distinct sampled candles and elapsed qualification
time, not a countdown promise. `automatic_recovery_expected` is conditional on
fresh data, active session and existing ownership/risk gates. Global HALT,
operator Stop or expiry takes precedence. Do not suggest restarting or switching
profiles to clear a volatility pause. An active saved-policy change returns
`volatility_pause_policy_bound`; ask the maintainer to restore the saved YAML
policy. Thresholds, size factors and fallback remain YAML-only.

Stale observations mark spread status stale and defense unavailable, clear fresh
NATR/spreads and make cancellation/latch confirmation unknown. Never present the
last cached sizing state as live health. Continue the existing bounded polling;
these fields need no additional exchange request or new endpoint.

Cold starts/profile changes can initially show smaller orders while fresh data
qualifies normal size. During a known volatility pause, automatic random profile
replacement waits for recovery so it cannot repeatedly reset qualification.
The displayed step may have expired while quotes remain blocked: send authority
has still expired, and elapsed random windows are skipped on recovery. Explicit
Stop/Switch and overall session expiry remain effective.

`normal` permits normal sizing for new proposals. Existing smaller safe orders
may remain until ordinary refresh; it does not promise that current working
orders instantly consume the full proposal budget. Show observed order amounts
alongside the policy state.

## Session command workflow

### Operator form and review

Read `/status` and `/profiles` when opening the review. Preserve the current
`control_version`, session ID and reviewed `catalogue_hash`, while retaining draft
edits if another operator changes state. `/profiles` supplies configured
`market_modes`, `duration_modes`, process identity and `configuration_policy`.

| Input | API fields | Operator behavior |
|---|---|---|
| Market | `market_mode: spot | perpetual | both` | Offer only configured modes; show shared wallet once and market allocations separately |
| One profile | `selection: fixed`, `profile_id` | Choose one deployed profile; display its references/weights read-only |
| Random profiles | `selection: random` | Use the YAML pool and step duration; equal probability, with consecutive repeats allowed |
| Timed | `duration_mode: timed`, `duration_seconds` | Default mode; YAML bounds apply and omitted seconds use the YAML default |
| Until stopped | `duration_mode: until_stopped` | Offer only when advertised; omit `duration_seconds` and retain a separate random-step countdown |

Use these plain labels for shipped profiles; other deployed IDs need a readable
fallback. These describe quote calculations, not a promised exchange chart path.
Show the actual configured references; do not hard-code BTC/ETH/AVAX in the FE.

| Profile selection | Operator label | Description |
|---|---|---|
| Fixed `follow_market` | Follow market basket | Quote around the configured weighted reference basket |
| Fixed `sideways` | Quote around session anchor | Quote around the session's starting price anchor |
| Fixed `follow_token` | Follow configured token | Follow the one reference shown in Managed settings |
| Random | Rotate profiles | Choose from the displayed YAML pool at its configured interval |

The form edits only Market, Profile/Random and Duration. References, weights,
pool/step timing, budgets, spreads, leverage, target pair and guards are read-only
**Managed settings**. Sideways has no reference basket. Review the exact proposed
change alongside the active session. For an active session use `switch`, which
creates a replacement session; it does not patch the old timer or flatten exposure.
Do not generate a giant editable form from every controller field.

Older servers omitting `duration_modes` fall back to Timed. Keep hidden timed
input drafts separate so Until stopped never submits stale seconds. Its request
may omit or null `duration_seconds`; mixing a duration with this mode returns
`duration_mode_conflict`. Without YAML permission, new continuous requests return
`duration_mode_not_allowed`. Show “Runs until you stop it; market and risk limits
still apply.” Clock/restart semantics are defined [above](#restart-and-rollout-examples).
Start a new session explicitly through the API. Until stopped removes overall
expiry; loading its YAML permission does not start trading on an empty database,
auto-renew a session or reset HALT.

`GET /reference-pairs` is searchable/paginated OKX spot USDT instrument discovery
for maintainers preparing YAML, not an editable session reference field. It uses
the existing public parser/throttler, a 60-second metadata cache and shared
refreshes. A failed refresh retains stale observations. Metadata freshness does
not establish price readiness or allow a pair outside the deployed profile.

### Submit and follow the command

POST `/commands` with Basic Auth and one new `Idempotency-Key` per intentional
operation. Include `expected_version` from `/status`; start/switch also include
the reviewed `expected_catalogue_hash`. Switch/stop require the current `session_id`.
The hash precondition is optional for old clients, so FE must include it to obtain
catalogue-change protection. It never overrides YAML.

Example Start body; replace version and the all-zero sample hash with values
from the reviewed responses:

```json
{
  "action": "start",
  "expected_version": 12,
  "expected_catalogue_hash": "0000000000000000000000000000000000000000000000000000000000000000",
  "market_mode": "both",
  "selection": "random",
  "duration_mode": "until_stopped"
}
```

A `202` receipt means accepted. Poll `/commands/{command_id}` for `accepted`,
`applying`, `succeeded`, `failed` or `superseded`, showing the current readiness
reason. A version acknowledgement does not complete the command; connector/feed/
account admission must succeed. Runtime startup failure produces a failed
receipt; readiness waits remain visible. Session timing starts at joint readiness.
A succeeded receipt is a past outcome, not proof of current working orders.

On a network timeout the outcome is unknown. Poll a known receipt; otherwise
retry the identical POST with the same body/key. Never silently refresh its
version, body or key. Matching retries return the original receipt before
stale-version checks, even after deployed YAML changes. Another body under the
same key or a conflicting version returns 409. A new reviewed decision after a
conflict uses a new key. Invalid static settings return 422.

Recover retained receipt metadata after a page reload before creating another
operation. Store only non-secret drafts/receipts locally, scoped by API origin
and bot; clear them when changing connections. `/commands` and `/sessions` support
shared history/operator handover. Keep Stop available during a pending start or
switch: refresh current state and explicitly stop the current session, which can
supersede a pending operation.

### Errors and next actions

Errors contain `error`, stable `code`, English `detail`, safe field paths, `retry`
and a server-generated `request_id` (also `X-Request-ID`). Raw exceptions/invalid
input values are not returned. Use `fields[].path` for inline validation and
show the request ID/raw envelope only in Details. Do not treat every 409 as retryable.
Reconciliation cooldown also returns `retry_after_seconds` and `Retry-After`.

| `retry` guidance | FE behavior |
|---|---|
| `fix_request` | Highlight fields; remove read-only YAML overrides rather than retrying |
| `refresh_state` | Refresh status/profiles and require a new reviewed decision |
| `same_request` | Preserve the original body/key; outcome may be unknown |
| `wait` | Respect Retry-After/cooldown while preserving visible progress |
| `reconnect` | Ask the operator to sign in; suspend automatic submissions |
| `contact_support` | Show reason/request ID; offer no bypass/reset |

### Stop and held exposure

Keep **Stop orders** accessible with one short confirmation showing the affected
session/markets and exposure: **“Stop MM; positions remain open.”** Avoid nested
confirmations. If Stop fails or its outcome is unknown, retain that state rather
than showing success. Keep positions/orders and their timestamps visible afterward.
`/orders?state=open` is recorder history, not an exchange-wide live snapshot.

Position freshness has two scopes. `/positions.markets[].stale` describes the
last complete pair/account position snapshot; `items[].stale` describes each
known position observation, which WebSocket updates can keep fresh between REST
snapshots. A stale market snapshot can therefore contain fresh, accurate items.
Show these badges separately: stale full-scope coverage cannot prove that no
other position exists or that exposure is flat. It does not, by itself, mean the
trading engine stopped. Compare quantity agreement and freshness independently.

Stop and reconciliation never automatically flatten positions or clear HALT.
Checks may be unavailable when no appropriate connector remains; they do not start
one. Admins can use the [reviewed recovery flow](#admin-recovery) to inspect verified
repairs for supported technical HALTs. Unsupported stops or incomplete evidence
remain blocked and require “Contact support” via the
[technical recovery runbook](postgres-runtime.md).
Google-authenticated calls record the individual approved actor and enforce their
current role. Basic bootstrap records its configured shared username. Recovery
leaves the bot stopped; Start is separate. The separate Disconnect workflow above
closes managed perpetual positions; ordinary Stop retains this section's semantics.

## History, balances and statistics

### Session and command history

`GET /commands` discovers control receipts, with optional `action`, `status` and
`session_id` filters. `GET /sessions` discovers recorded session creations, their
latest recorded transitions and links to events/configuration. Both accept
`limit` (1–500) and opaque `cursor`; reset the cursor when changing filters.
Session coverage explicitly excludes legacy sessions with no creation event,
while `current_session_id` remains available. These reads do not contact OKX.

### Session history and liquidity

Transition history begins at migration 016; older sessions can have incomplete
creation/event coverage. Missing history is not replaced with current YAML.

- `GET /api/v1/admin/sessions/{session_id}/events`: newest-first keyset pagination, `limit` 1–500, optional `event_type` and `source`. Cursors bind all filters and bot/session identity. `history_complete` indicates that a session-start configuration was recorded; it does not imply exact historical market coverage. History begins at migration 016.
- `GET /api/v1/admin/statistics/liquidity`: current session observations, optional `session_id` and `market=all|spot|perpetual`. Historical sessions without a current checkpoint return unavailable. Use existing fills/fees/daily endpoints for financial history.

Events include immutable resolved configuration at creation, profile/step transitions,
stop/expiry and observed guard changes. Control transitions share their original
transaction and fencing. Duplicate command retries and owner takeover do not create
duplicate transitions. Authenticated username or scheduler/observer source is
recorded; credentials are never part of the event payload. Identical guard ticks
create no event. Global HALT remains visible after controllers have stopped.

Liquidity uses existing exchange-acknowledged limit orders in OPEN/PARTIALLY_FILLED
state. Remaining quantities determine notionals; pending creation/cancellation and
unacknowledged orders are excluded from the live working-order rows. Each market's
`working_orders` rows expose exact decimal `price`, `remaining_quantity`, and
`remaining_notional`, plus side, order type and OPEN/PARTIALLY_FILLED state. `null`
means the current or legacy observation cannot provide rows; `[]` means a fresh
observation found no acknowledged own limit orders. Per-side `pending_create_count`
and `pending_cancel_count` come from the order tracker and remain separate from
exchange-acknowledged rows. Counters use consecutive fresh owner samples
within five seconds and persistent bid/ask identities for two-sided coverage.
They measure sampled observations, not guaranteed continuous exchange availability.
Zero quotes on a valid sample means zero two-sided coverage. Feed gaps, restart,
owner or profile generation changes are unknown intervals. Elapsed time includes
unknown intervals, so gaps cannot inflate the reported uptime fraction. Stop/expiry
freezes elapsed time. Only spot/perpetual counters are retained, without book history.

`eligible=true` and `eligible_seconds` describe controller/market-data readiness;
they do not certify permission to place both BUY and SELL. A fresh market can be
ready while inventory, sizing or another per-side guard prevents an order. Use
`quote_activity.sides` to distinguish those restrictions from missing working
orders. For a two-sided operating target, report guard-constrained samples and
unknown coverage separately; a zero eligible denominator is not a passing SLO.
Do not label a strategy-wide `running=true` as proof of two-sided liquidity.
These readiness counters are not the approved rolling eligible two-sided SLO
denominator: they do not identify historical intervals excluded by explicit
inventory/guard restrictions or prove that both sides were funded. The API keeps
the cumulative session fraction and latest side diagnosis separate; until an
interval-level eligibility history is available, do not derive rolling 30-minute
coverage or historical restricted duration from the cumulative counters.

During a joint readiness transition, fresh liquidity observations use the shared
service reason when the controller has no local reason. Local restrictions and
unavailable observations retain their own reasons. Other non-running phases use
`session_not_admitted` when no local reason exists. Older images can return a
null market `reason_code` while `/status.service.reason_code` reports a global
restriction such as `exposure_limit` or `insufficient_collateral`. For those images,
show that service reason alongside the market state when both reads are fresh, refer to
the same session and describe the same transition. Previously acknowledged
orders can remain visible while withdrawal settles; their presence does not
prove renewed admission. If the observations cannot be correlated, show unknown
instead of inventing a market reason or counting a successful quote sample.

The liquidity response's `markets` is a map keyed by `spot`/`perpetual`, not an
array. Render each side's `quote_activity` alongside status. In the real Cloud
test, a93.66% BTC account weight against the50% inventory target produced
buy multiplier0/sell multiplier2; BUY then showed `order_size_rejected` while
SELL orders could work. Explain this using the published inventory-skew values
and reason, rather than calling it a disconnected exchange or inventing an
insufficient-funds error. Sizing/spread policy remains YAML-owned.

An individual protected quote rejection (`spread_policy_quote_rejected`) does not,
alone, prove service failure or an exchange-accepted order. The revision48 test
logged these as ERROR. The subsequent source fix classifies a fresh spread-check
rejection as `QuoteRefreshRequired`: the connector emits a bounded WARNING
(first occurrence and thirty-second summaries per pair/side/level), while
the executor keeps per-order DEBUG diagnostics. The configured entry retires,
keeps its level reserved through shutdown, and lets the controller propose a
fresh quote. Missing controller/policy data and other safety failures remain
errors. This does not guarantee every replacement will pass a later fresh gate.
Queued managed LIFE entries re-evaluate price and size before creating their
first order identity, preserving the original base/notional ceiling. Retries and
durable order intents retain their original parameters. Quiet WS books may recover
through bounded public snapshots with real venue timestamps; stale REST still
blocks readiness. Expected unavailable drawdown valuations warn with summaries
and recovery, without making old report values ready. These changes add no API.
The first-entry maker bound also preserves the current allocation if it shrank.
An accepted post-only request can immediately become `OrderCancelled` on the
venue; neither its receipt nor exchange ID proves a working quote. A confirmed
zero-fill managed maker cancellation retires its executor so the controller can
build a new passive quote; explicit Stop and partial/unknown history keep their
existing handling. Empty-book bootstrap retains its explicit seed policy.
Keep available API quote reasons and repeated no-quote duration visible, and
use observed runtime/receipts/venue reconciliation for operational state.
Platform-log correlation is a maintainer diagnostic; the frontend does not gain
a Cloud Logging event stream from these APIs. Do not relax guards through the
UI or silently dismiss repeated rejections.

Stale owners or observations hide quote values and extend unknown time; optional
projection errors report unavailable and do not change trading intent. API reads
reuse PostgreSQL/status snapshots and issue no exchange calls. Events take two
bounded queries per page, independent of result count; indexes cover keyset and
single-filter pagination. Liquidity takes one existing state read. There is no
new worker, external cache, ledger or exchange client.

### Orders and fills

`/orders`, `/orders/{id}` and `/fills` read the existing recorder tables. History
has bounded keyset pagination (`limit` 1–500), UTC half-open `from`/`to` timestamps
and market/pair filters. Keep `connector`, `order_id` and the native fill ID together:
exchange trade IDs are not global IDs. Cursors bind the original filters. Order
details expose a bounded status timeline and a link to paginated fills.
Order pages also expose `recorded_filled_amount`, `recorded_remaining_amount` and
`progress_basis`. Aggregates use three SQL queries regardless of page size.
Remaining means original amount minus recorded fills, not an exchange live/open
quantity: cancelled orders can have a positive recorded remainder. Provisional
or inconsistent recordings have null remainder. Do not infer exchange completion
from zero recorded fills or build a live order book from recorder history.

Managed order history preserves BUY/SELL intent before submission. A private
cancel with an exchange ID can resolve a fully recorded intent before a Created
callback, while retaining its canceled status. A row built only from a partial
fill stays provisional until original order parameters arrive. A known side
alone does not prove exchange admission; cancellation without an exchange ID
still requires reconciliation.

`market=all` returns distinct spot and perpetual data even for the same LIFE-USDT
pair. Switching the current trading mode does not hide recorded history.

### Shared balances, positions and performance

`/balances` reports actual shared account observations, deduplicated by OKX request
account UID/environment/currency. It never adds the same USDT collateral across
connectors or splits wallet amounts into configured market budgets. Cash fields
(`cashBal`, `availBal`) and equity (`eq`, `availEq`) are separate; missing fields
are null. Unknown account identities are explicit and are not persisted as daily
account snapshots. A currency omitted from a newer snapshot cannot look fresh.
Configured budgets remain available separately in `/profiles`.

`/positions` reports actual perpetual quantities, sides, leverage and available
margin/liquidation fields from the connector's existing REST/WS loop. Spot
positions are `not_applicable`. `/performance` reuses the existing V2 controller
report cache: realized/unrealized PnL and volume cover its stored history and
active exposure, not an exact daily PnL or an exchange-wide accounting statement.
Daily recorded fee/funding statistics are linked separately. Neither FE polling
nor these endpoints fetch account data again. Stopped/disabled-market observations
remain visible and stale; owner heartbeat and observation timestamps remain explicit.

### Daily statistics, fees and funding

`/statistics/daily` defaults to seven reporting dates and accepts `from` (inclusive)
and `to` (exclusive), with a maximum 90-day range. `REPORTING_TIMEZONE` defaults to
`Asia/Ho_Chi_Minh` for day boundaries only. Every PostgreSQL connection explicitly
uses UTC, timestamps are `TIMESTAMP WITH TIME ZONE` or UTC epochs, and local dates
are labels with their timezone recorded. Changing the reporting timezone does not
relabel old balance snapshots.

Daily statistics use a bounded 32-entry in-memory cache to coalesce concurrent
dashboard polls. `ADMIN_STATISTICS_CACHE_TTL_SECONDS` defaults to 5 (0 disables;
maximum 60). Responses retain the aggregate's original `server_time` and balance
observation timestamps. Cache expiry triggers a fresh consistent database read.
The public Demo environment preset explicitly uses 15 seconds to reduce daily
aggregation pressure. Display the original observation time; repeated polls may
return the same observation during that interval. This setting belongs to the
environment configuration and cannot be changed through the admin API. Current
balances, positions, controls and trading decisions use their separate paths.
An acknowledged PostgreSQL statement timeout may retry one complete consistent
read before responding. The retry uses normal bounded report admission; queued
controls retain priority. This can lengthen a statistics refresh. Keep the prior
chart and its observation time visible while loading; do not infer a trading
stop from the loading indicator. Operator cancellations, discarded sessions and
other SQL errors do not receive this extra retry. Statement/I/O limits and the
existing cache TTL remain unchanged.
On an identifiable transient reporting failure, the endpoint may return HTTP 200
with `freshness: "stale"`, `observation_age_seconds`, an English `message` and
`retry_after_seconds` (1–5). This requires a successful matching observation
younger than 60 seconds; failed retries never extend that deadline. The short
retry cooldown coalesces failed concurrent refreshes. Cache size remains 32,
and timezone, resolved reporting dates, market and pair isolate observations.
Relative date ranges roll over at reporting midnight, including during a refresh.
Setting the cache TTL to zero disables both caching and fallback.

With caching enabled, simultaneous cache misses for the same canonical
timezone/date/market/pair share one in-flight refresh. Different filters retain
independent observations but execute serially in the heavy-report/history lane.
The other of the existing two database workers is reserved for operational reads
and session controls; controls overtake queued operational reads. At most10 distinct daily refresh keys
are outstanding. Further uncached keys receive the existing503/unavailable
response, or an eligible explicitly stale observation. Disconnecting one client
does not cancel a shared physical query. TTL0 also disables in-flight sharing.
Completed keys leave the registry; the cache still holds at most32 entries.
Heavy work is capped at10 running/queued jobs, leaving two of the existing12
non-control admission slots for operational reads. Total pending remains32.
Daily refreshes and history pages/order timelines above100 rows share the heavy
lane. Default-sized history pages (up to100), pinned session configuration,
status, balances, positions, performance, liquidity and stored reconciliation
receipts use the operations lane. A running job cannot be
preempted. History/statistics may take longer while current-state panels remain
independently admissible within these bounds. Overload and shared DB/CPU pressure
can still cause503 or delay; this is not an unconditional latency guarantee.
Daily-report transactions disable PostgreSQL parallel workers locally to limit
shared-database CPU/memory fan-out. The2s statement deadline still applies;
a million-fill report can time out. This does not change trading settings or
prove full isolation from shared database resource pressure.

Render stale daily charts as **"Statistics updating"** and show their original
observation time; retry after the supplied delay. Keep the financial values
qualified as last observed. Do not infer trading health, current balances or
available funds from this response. Use `/status`, `/balances` and `/positions`
for those separate panels. If no usable observation exists, the read still fails
normally (for example HTTP 503); show **"Statistics temporarily unavailable"**
and never replace missing values with zero. Validation, schema/authentication
errors and cancellation do not trigger fallback. This cache is never used for
trading, ownership, controls or risk admission.

The response contains separate market day series and one account balance series.
Only observed days appear; missing days are gaps. Fees include original-currency
paid/rebate/net amounts, recorded USDT conversion coverage and explicit unavailable
or ambiguous legacy conversions. Percentage-only original fee rates are counted
as incomplete original-amount coverage. Funding is observed-only for perpetual
and not applicable to spot. PostgreSQL trade amounts use unscaled NUMERIC;
`trade_storage_scale` is null. Identified funding bills preserve exact original
USDT amounts; legacy float events remain explicitly unverified, since Decimal
output cannot recover their lost precision. Unobserved funding buckets are null.

`/status.funding_sync` and `/statistics/daily.funding_sync` expose the account/
environment, covered interval, last successful sync, pending backfill, stale/error
state and any interval outside the supported history window. Coverage describes
completed committed reads, not an estimate from current positions. Unknown or
incomplete coverage must remain visible in the UI. Reporting reads use PostgreSQL
and the existing daily cache; they never initiate exchange requests. Funding
checkpoint changes alone do not invalidate trading reconciliation; changes to
HALT, orders, fills or other recovery state still do.

Account bill backfill can populate days **before this service/session first ran**,
as observed with an existing Demo account on a fresh cloud ledger. Label the
series account/environment funding with its covered interval. Do not attribute
all imported funding to the current session or combine it with that session's
fills as a session profit calculation.

The owner upserts the latest fresh account observation at most once a minute per
account/environment/currency/timezone/day inside its successful fenced status
transaction. Duplicate observations and restarts do not create extra points.
These are last-observed balances, not exact midnight valuations.

## Financial and chart semantics

### Available charts and unsupported history

| UI/chart | Current source | Interpretation |
|---|---|---|
| Daily buy/sell volume, fill counts and fees/rebates | `/statistics/daily` | Separate spot/perpetual series, with original-currency/conversion coverage |
| Daily funding | `/statistics/daily` | Observed perpetual funding with explicit backfill/coverage; unknown is null |
| Daily LIFE/USDT cash/equity observations | `/statistics/daily` | One shared account series; last observed that day, not exact midnight NAV |
| Current own bid/ask/spread/coverage | `/statistics/liquidity`, `/status` | Current/session sampled own working orders, not exchange-wide order-book depth |
| A small live line chart | FE samples of current observations | Label “since this page opened”; break at gaps and session changes |
| Historical market candles (OHLCV) | Not exposed | No admin endpoint supplies per-minute/hour market open/high/low/close/volume |
| Historical target/reference price curves | Not exposed | Do not substitute daily statistics or bot fills for a market price history |

Daily operating statistics are ready for charts. OHLCV/history would need a
separate data-source/API addition; the existing reporting endpoints do not
provide a candle chart. A live client-collected series must not imply historical
coverage or persistence through a page reload.

### Money, coverage and timestamps

- Preserve Decimal strings for calculations and payloads. Round only for display;
  use an appropriate decimal library, not JavaScript floating-point sums for money.
  Chart plotting may use display numbers without changing authoritative amounts.
- Label order progress “Recorded fills,” with `progress_basis` in Details;
  use the recorded-versus-working-order semantics defined above.
- Show account cash, available cash, equity and available equity separately; never
  add shared USDT collateral from Spot and Perpetual twice. Perpetual position
  amount is exposure, not owned spot LIFE. Show LONG and SHORT separately.
- Unknown, not observed, stale and not applicable are different from zero. Display
  gaps in chart lines instead of synthesizing zeros or interpolating across outages.
- Keep the reporting timezone visible. Render UTC instants in the selected
  display timezone without relabelling persisted daily balance buckets.
- Retain the observation/coverage labels from the history/statistics contract;
  account observations and controller PnL are separate metrics.
- Show daily buy/sell volume and paid fees/rebates first. Funding is a separate
  perpetual series. Put controller PnL in a secondary panel with its scope label.

## Observational reconciliation

POST `/reconciliation/check` with an `Idempotency-Key` and optional market/pair,
`lookback_seconds` (60–86,400), `max_orders` (1–200) and `max_pages` (1–10).
At most one check runs at a time, with at least 30 seconds between new checks.
Each market's exchange reads have a 20-second deadline and reuse connector
throttlers/read helpers. Fill pagination uses OKX `billId`; position quantities
use the existing contract metadata conversion. Active and held perpetual exposure
are compared as separate long/short legs.
Order comparisons include instrument, side, original quantity, type and limit
price, plus base-currency sizing for spot market orders. Missing required terms
make a check incomplete; changed terms between reads invalidate its snapshot.

GET `/reconciliation` reads the latest stored result; polling does not contact OKX.
GET `/reconciliation/{check_id}` returns the specific receipt and report requested,
even after a newer check starts. Use this lookup while following an operator's
check; missing IDs and IDs belonging to trading commands return 404.
After a cold start with stopped intent, managed credentials can be Connected while
reconciliation reports `authenticated_connector_unavailable`: the live trading
connectors have not been started. Show the check as unavailable, separately from
credential validity; do not ask for replacement keys or auto-start trading merely
to obtain a matched report.
Report states are `not_checked`, `checking`, `matched`, `mismatch`, `incomplete`
and `unavailable`. Receipt execution success is distinct from a matched report.
`matched` means only the complete checked scope agreed. Truncation, overlapping
or missing fill pages, the endpoint's three-day history limit, concurrent local
or exchange changes, timeouts and unavailable markets are explicit. One successful
market never implies the other succeeded. Differences link order/fill/pair identity.

Checks do not start a strategy to obtain a client, mutate orders/trackers/ledger
rows, run corrective startup recovery, bump control versions or clear HALT.
Unavailable owners/clients produce unavailable reports. Owner fencing protects
claim/completion; a new owner can reclaim an interrupted check.

OKX field and pagination references:
[account configuration](https://app.okx.com/docs-v5/en/#trading-account-rest-api-get-account-configuration),
[transaction details](https://app.okx.com/docs-v5/en/#order-book-trading-trade-get-transaction-details-last-3-days).

Use an explicit Check button. A completed receipt with `report.state=mismatch`
is a completed check with discrepancies. Show scope, times, incompleteness,
counts and differences. Follow `/reconciliation/{check_id}` for the requested
check. Never trigger exchange reconciliation on each dashboard poll or label
a bounded spot match as “Account reconciled.”

## Polling and availability

These are initial FE scheduling suggestions, not measured optimal intervals:

| Read | Suggested initial cadence |
|---|---|
| Status + current liquidity | Every 2 seconds on the active overview |
| Pending command receipt | Every 1 second initially, back off if still applying |
| Balances/positions | Every 5 seconds while visible |
| First page of orders/fills/events | Every 5–10 seconds while visible; older pages on demand |
| Command/session history | On opening Activity, after a command, or explicit refresh |
| Daily statistics/controller performance | Every 15–30 seconds while visible; refresh on filter change |
| Profiles/pinned configuration | On review, connection/session/revision change; pinned configuration on demand |
| Schema | Generate types offline from this repo; production HTTP docs are hidden |

Share requests across widgets, with at most one in flight for each query key.
Pause nonessential hidden-tab polling, add jitter/backoff on failures and refresh
status on foreground/reconnect. Preserve each widget's last-good data and original
observation time; a fresh HTTP response or cached daily response is not necessarily
a fresh account observation. Ignore responses from obsolete filters/connections.

Do not issue per-row balance, position, fill or reconciliation calls. Open one
order drawer on demand. Cursor changes must reset when filters change. Charts
and history failures should not prevent Stop/status controls from rendering.
The service's optional reporting remains independent of trading admission.
API database work is bounded. Commands and individual command receipts are
dispatched before pending reports, with capacity reserved for control admission.
Already-running queries finish first. On HTTP 503, back off optional polling and
keep the same idempotency key when checking/retrying a command; a failed HTTP
response does not prove that an earlier command was never accepted.

## Browser acceptance checklist

The API/backend and simulator results do not constitute browser acceptance.
Before handing the dashboard to the operator, exercise these cases against local
service fixtures, then verify the intended hosted route with trading stopped:

- [ ] Wrong credentials, preflight/CORS failure, reconnect and loss of API connectivity.
- [ ] Invalid fields, forbidden YAML overrides, stale catalogue hash and two operators changing the session.
- [ ] Duplicate clicks, timeout after acceptance, same-key retry, page reload with a pending receipt, and failed/superseded commands.
- [ ] Preparing/running/waiting, no orders versus orders waiting for fills, stale owner and HTTP 503 process-only diagnostics.
- [ ] Spot/perpetual/both, one profile/random, timed expiry and Until stopped with a separate random-step countdown.
- [ ] Stop during pending startup/switch; retain held exposure, cancellation progress and command outcome.
- [ ] Partial/unavailable/mismatched reconciliation, missing daily data, incomplete fee/funding coverage and null versus zero.
- [ ] Shared wallet shown once, separate spot/perpetual exposure, Decimal calculations and visible reporting timezone.
- [ ] Primary English labels/next actions understood by a non-technical operator; raw codes stay in Details.

Backend implementation evidence is in the
[operations implementation report](../reports/README.md#historical-records) (Git: `reports/implementation/admin-operations-api-implementation.md`).
The [original API/UX review](../reports/README.md#historical-records) (Git: `reports/implementation/admin-api-dashboard-ux-review.md`) records
its historical baseline; this guide and the checked OpenAPI describe current
integration behavior. Strategy/load/Demo results retain their own image and test
scope. The linked Cloud Run Demo Google/connection lifecycle qualifies its named
route/account/date only. The full dashboard/browser checklist above and live
production-account acceptance remain external delivery work.


## Admin recovery

Recovery is available only to `admin` (`GET /access/me` exposes
`permissions.manage_recovery`). It does not alter YAML or risk limits. When
`halted=false`, hide manual Check/Recover and inspect `automatic_recovery`
before showing **No recovery needed**; `recovery_not_halted` is an availability reason, not a new
service fault. Stop the session first and wait for its control version to be
acknowledged before checking an existing HALT. Recovery never
starts trading, cancels orders or closes exposure. This statement concerns the
reviewed manual API, not automatic startup's owned-order drain. If exchange orders remain,
resolve them through the existing cleanup/runbook before retrying.

A send error after physical transport (including OKX HTTP429/50013) can leave an
unresolved intent. Show **Order outcome unknown — verifying**, not
"order rejected" or "safe to retry". A managed connector retains the pending
native tracker entry so late acceptance/fills can still be recorded; it does not
emit a terminal failure event without proof. One order-not-found51603 is not
proof that the request had no effect. Supported PostgreSQL MM maker entries use
the automatic flow below. A replacement engine with durable manual HALT
keeps its owner/control API alive without constructing a new strategy: startup
and live may be200 while ready remains503. This does not mean trading resumed.
After Stop, Check returns `recovery_pending_intents` before position replay or
credential loading when a pending send exists. Keep Recover disabled and show
the supplied technical-review action; no force/reset or automatic Start.

### Automatic order reconciliation

`GET /status` adds optional `automatic_recovery`. This is an observation, not a
new reset endpoint or permission. Keep the existing error/control envelope and
render `stale=true` as unknown. `running=true` can mean the engine is alive while
order admission is paused; use quote activity to show actual working quotes.

| `phase` | Display / available action |
| --- | --- |
| `idle` | No automatic work pending; ordinary guard/session admission still applies. |
| `reconciling` | Verifying order outcome; new shared-account risk is paused. Keep Stop available. |
| `bootstrapping_recovery` | Replacement owner is restoring journaled effects before trading. |
| `waiting_for_readiness` | Order proof is complete; waiting for current market/risk readiness. |
| `settled_stopped` | Proof cannot override Stop, expired session or changed control. No automatic Start. |
| `manual_required` | Conflicting evidence needs the existing reviewed recovery procedure. |

Show `reason_code`, `started_at`, `last_progress_at`, `next_retry_at`, `order_id`
and `operator_required` where present. After five minutes without resolution the
operator flag requests investigation; it does not discard the reservation or
prove absence. Start/Switch during an active running reconciliation return
`automatic_recovery_in_progress`; follow the returned action availability.
An explicit Start from stopped may initialize a pending cold proof, never bypass it.
Use `phase` as the current state: an idle result may retain the last order/timing
context; render a retry countdown only while automatic work is active.

`cold_recovery_supported=true` means a verified financial/account checkpoint is
available for supported journaled effects. False does not mean a current warm
reconciliation failed; legacy ledgers need a reviewed baseline for cold recovery.
The flag does not bypass current account, ownership, risk or control checks.
During a verified owner shutdown, `recovery.halted` can briefly remain true as
the admission fence. When `recovery.next_action=wait`, render the returned
`operator` guidance instead of a manual-HALT banner: Start/Switch remain disabled,
Stop remains available, and Check/Recover are unavailable. A stale owner remains
unavailable; this is not proof that quotes are working. Risk stops, unresolved
mutations and an unsupported baseline retain the ordinary recovery block.
The worker issues authenticated reads, never repeats the uncertain POST. It
rechecks current ownership/control/session/credentials/readiness before admission.
Cancel ACK alone is not terminal proof; complete terminal quantity and original
fills must be durable before retiring a managed order.

Audit actions `automatic_recovery_paused`, `automatic_recovery_order_verified`
and `automatic_recovery_resumed` use a runtime owner actor, generation and order
correlation. Financial proof/context stays private; no secret is returned.

### Reviewed manual recovery

| Endpoint under `/api/v1/admin` | Purpose |
| --- | --- |
| `GET /recovery` | Current blockers, check/reset availability and latest operation |
| `POST /recovery/check` | Queue exchange verification and preview; keeps HALT |
| `POST /recovery/reset` | Confirm reviewed preview; revalidate and apply verified repair |
| `GET /recovery/operations/{id}` | Poll this exact operation; HTTP 200 is a receipt read, not proof of success |

1. Show **Check recovery** to admins when `check_available=true`. Read `/status`
   for the fresh control version. POST `/recovery/check` with
   `{"expected_version":16}` and a new `Idempotency-Key`.
2. HTTP 202 returns an operation receipt. Poll its operation URL about once per
   second. Keep Start/Switch and connection changes disabled while recovery runs.
   `accepted`/`applying` means pending; `succeeded` can still have `stage=blocked`
   and `can_recover=false` when a check completed with blockers.
3. When `stage=review_required` and `can_recover=true`, display the proposed
   before/after changes and ask the admin to confirm. `verification` reports the
   verified order/fill counts. A confirmation is valid for five minutes and only
   for the same owner, generation, control version, credentials and ledger.
4. POST `/recovery/reset` with a new Idempotency-Key and the returned fields:

   ```json
   {"expected_version":16,"check_id":"<check operation id>","confirmation_token":"<returned 32-character token>"}
   ```

5. Poll the reset receipt. Only `status=succeeded` and `stage=recovered` means
   recovery completed. Display **Recovery complete — trading is stopped**.
   Refresh `/status`; Start remains a separate, deliberate action.

A network retry reuses the same key and identical body. A new attempt uses a new
key. Never send financial overrides or `force` fields. A stale/expired preview,
owner change or local drift returns a stable conflict code and requires a new
check. Availability is advisory; POST always revalidates. Confirmation is a
review binding, not an authentication token; do not log it.

Every blocker has English `code`, `detail`, `next_action`. Progress has `stage`,
`label`, `detail`, `expires_at` and `trading_state=stopped`. Errors retain the
existing HTTP/error envelope. Read/write roles receive 403 for all recovery
endpoints. Admins can review `recovery_check_requested/completed/failed`,
`recovery_reset_requested/completed/failed` and `recovery_position_repaired` in
`GET /access/audit`; `operation_id` links the atomic before/after receipt.
Secrets and private proof fingerprints never appear in recovery responses.

Initial supported scope is deliberately conservative: managed (`postgres_kms`)
credentials bound to one dedicated OKX account; USDT spot and cross/hedge USDT
perpetual; complete local history within the three-day per-order lookup window;
at most 5,000 orders, 2,000 fills and 100 existing unambiguous position rows.
Original flat USDT fees must be exactly verifiable. Spot fees paid in the pair's
base asset are also supported only when their conversion at the verified fill
price exactly matches the recorded quote fee; no current price is substituted.
Other fee currencies and percentage-fee encodings remain blocked. Gross filled
position quantity and fee-deducted wallet cash are distinct. A position can only be
extended from a known prefix of its verified fills; unknown opening balances,
ambiguous controller attribution, unsupported fee conversion, missing pages,
external fills/orders and unmatched perpetual exposure block recovery. Bound
risk policies/peaks/latches and all order/fill/funding history are preserved.
The exchange is read twice around the proof; a changing account remains blocked.
A missing local create event is tolerated only for a positively identified,
confirmed canceled/expired zero-fill order, with exact price/size and no fill in
either complete venue scan. The local BUY/SELL field stays unknown; conflicting
local sides still block. `verification.unfilled_terminal_orders_without_local_side`
reports the count. Missing exchange detail never uses this exception.

Native whole-second event-clock timestamps may differ from REST execution time
by less than 1,000 ms; all order/trade IDs, quantities, prices and original fees
still match exactly. Non-quantized mismatches, future times, differences of at
least 1,000 ms, and changes between the two REST scans block recovery. Recorded
timestamps are preserved. The three-day cap is a local maximum, not a guarantee
that the exchange retains every per-order record.

`unrealized_pnl_quote=0` in a repaired position has
`valuation_basis=reconstructed_average_entry_not_live_pnl`: it is reconstruction
at the average entry, not a current market valuation or a claim that losses were
recovered. Use the existing fresh valuation panels for live PnL. Do not relabel
risk stops as recoverable technical incidents. The total exchange-check budget
is 120 seconds, with bounded GET requests and no background trading admission.
