# FE integration

This is the single handoff for the LIFE MM operations dashboard, implemented in a
separate FE repository. It combines the API contract, operator UX and configuration
ownership rules. The checked [OpenAPI contract](api/openapi.json) is the source for
request/response types; generate TypeScript types from it. Runtime API responses,
Swagger annotations and UI copy are English.

The primary operator may be non-technical. The dashboard should answer: is the
service available, are there working orders, what is blocking trading, how much
capital/exposure remains, and what should the operator do? Profit is secondary.
No dashboard application or browser acceptance ships in this repository.

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
separate. Configure `ADMIN_API_ENABLED=true`, `ADMIN_API_USERNAME` and
`ADMIN_API_PASSWORD` through the existing `.env` loader or deployment secrets.
There are no usable credential defaults. All admin endpoints require Basic Auth.
The API does not create login sessions, issue frontend tokens or persist FE passwords.

Use TLS for the hosted dashboard. Credentials stay out of FE builds, URLs, logs
and persistent browser storage. Direct browser requests send an explicit Basic
`Authorization` header and `credentials: "omit"`. A gateway route needs its own
browser authentication and forwarding behavior; that deployment decision is not
implemented by the bot API.

For the internal Cloud Run service, the proposed integration is browser → FE
backend/gateway → bot. The gateway authenticates its users, keeps Basic Auth
credentials server-side and uses an ingress path permitted by the deployment.
When Cloud Run IAM invocation is also required, send the platform ID token in
`X-Serverless-Authorization: Bearer …` and retain `Authorization: Basic …` for the
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

Generate the offline schema with `make swagger`; CI checks drift with
`make swagger-check`. Handler annotations and Pydantic models supply the contract.
Protected `/docs`, `/openapi.json` and `/docs/assets/{asset}` exist only when
`ENV=dev` or `development`, case-insensitive. The default is dev; other values
return 404 for these documentation routes. The production template explicitly
sets `ENV=production`. Swagger uses packaged assets without a CDN/remote validator
and does not persist authorization. Production FE builds use the checked schema,
without depending on the production HTTP documentation routes.

`ENV` controls documentation availability. `OKX_TRADING_ENV` independently selects
exchange routing. All timestamp responses use UTC `Z`; monetary values use Decimal
strings. Keep one small API client for authentication, errors, idempotency and retries.

## Configuration ownership

API commands control sessions. Maintainers change strategy/profile settings in
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
| Credentials, database, auth/CORS, logging, runtime intervals | Process environment / local `.env` / deployment secrets | No | New process with reviewed environment |
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
| HALT/operator action required | Show reason and runbook; do not offer an unimplemented reset/resume action |

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
These are API/log signals, not email/Slack notifications or automatic repair.

All diagnostic paths reuse existing quote decisions and the order tracker. They
do not restart sessions, reset deadlines/HALT, change limits or submit trades.

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

Stop and reconciliation never automatically flatten positions or clear HALT.
Checks may be unavailable when no appropriate connector remains; they do not start
one. For unresolved recovery, the operator action is “Contact support”; maintainers
use the [technical recovery runbook](postgres-runtime.md).
All calls use one configured Basic Auth identity. Events record that username
where available; shared credentials do not provide per-person audit/read-only
RBAC. No recovery-reset/resume endpoint is implemented.

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
unacknowledged orders are excluded. Counters use consecutive fresh owner samples
within five seconds and persistent bid/ask identities for two-sided coverage.
They measure sampled observations, not guaranteed continuous exchange availability.
Zero quotes on a valid sample means zero two-sided coverage. Feed gaps, restart,
owner or profile generation changes are unknown intervals. Elapsed time includes
unknown intervals, so gaps cannot inflate the reported uptime fraction. Stop/expiry
freezes elapsed time. Only spot/perpetual counters are retained, without book history.

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
On an identifiable transient reporting failure, the endpoint may return HTTP 200
with `freshness: "stale"`, `observation_age_seconds`, an English `message` and
`retry_after_seconds` (1–5). This requires a successful matching observation
younger than 60 seconds; failed retries never extend that deadline. The short
retry cooldown coalesces failed concurrent refreshes. Cache size remains 32,
and timezone, resolved reporting dates, market and pair isolate observations.
Relative date ranges roll over at reporting midnight, including during a refresh.
Setting the cache TTL to zero disables both caching and fallback.

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
[operations implementation report](../reports/admin-operations-api-implementation.md).
The [original API/UX review](../reports/admin-api-dashboard-ux-review.md) records
its historical baseline; this guide and the checked OpenAPI describe current
integration behavior. Strategy/load/Demo results retain their own image and test
scope. A dashboard, public Cloud Run route, browser acceptance and real-account
acceptance are external delivery work.
