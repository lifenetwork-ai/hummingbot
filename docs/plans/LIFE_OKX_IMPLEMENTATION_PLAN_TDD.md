# LIFE / OKX: Lean Market-Making Implementation Plan

Updated: 2026-10-25 (Asia/Ho_Chi_Minh). Source baseline: `2d1dc0b7b`.

This plan replaces the previous broad LIFE architecture with the requested MVP:
reuse Hummingbot to run three configurable market-making scenarios, select them
through an authenticated admin API, and provide the data needed by a frontend.
It is a specification, not a claim that these features are already implemented.

The [previous audit](LIFE_OKX_IMPLEMENTATION_AUDIT.md) remains historical evidence
about the old scope and reusable components. Its P0–P9/A01–A39 numbering is
superseded by the phases below. The previous full plan remains in Git history.

## 1. Product scope

One logical bot trades LIFE-USDT using one existing MM engine. The scenarios
change the reference used to calculate bid/ask quotes. They do not guarantee
that the market price follows a chosen chart.

| Scenario | Behavior | Configuration |
|---|---|---|
| `follow_market` | Follow normalized percentage changes in a selected reference pair or configured basket, such as BTC-USDT and ETH-USDT | Named YAML profile with reference pairs, optional weights and sensitivity |
| `sideways` | Quote around a stable LIFE anchor with configured spreads and inventory limits | Named YAML profile with anchor policy and quote bounds |
| `follow_token` | Follow a specific token selected by the operator because it is trending | Named YAML profile using the same follow calculation with one reference pair |

`random` is a selection policy over these three scenarios, not a fourth pricing
engine. Select one eligible profile per step, run it for its configured duration,
and select again at the next boundary until the overall session ends.

MVP assumptions to keep implementation small:

- Initial delivery targets OKX **spot LIFE-USDT**. Perpetual quoting and joint
  hedging are deferred; the existing generic perpetual connector remains available.
- The operator chooses the trending token from the packaged YAML catalogue.
  Automatic market-wide trend discovery/ranking is deferred.
- API requests select existing profiles and permitted runtime fields. YAML files
  are uploaded with the code/image; editing the catalogue requires a deployment.
- A normal session has one profile and a finite duration. A random session has a
  finite overall duration and a finite step duration. Default selection is uniform
  across enabled scenarios, then uniform across their eligible profiles.
- Basic Auth protects all admin reads and writes. FE integration starts with
  polling, without requiring WebSockets or a separate API service.

## 2. Reuse first

| Existing component | Reuse | Small addition needed |
|---|---|---|
| [Pure market making](../../hummingbot/strategy/pure_market_making/pure_market_making.pyx) and its [startup/configuration](../../hummingbot/strategy/pure_market_making/start.py) | Maker orders, quote levels, refresh/cancel, inventory skew, tick/lot handling | Reference-price delegate and a session adapter |
| [V2 MM base](../../hummingbot/strategy_v2/controllers/market_making_controller_base.py) | Alternative extension point: `update_processed_data()` supplies `reference_price` | Use only if a small integration test demonstrates it needs less adaptation than PMM |
| [OKX spot connector](../../hummingbot/connector/exchange/okx/okx_exchange.py) | Market data, balances, placement/cancel, tracking and fees recorded on fills | Verify listing/tradable state and freshness before quoting |
| [Existing service](../../hummingbot/cli/service.py) | aiohttp server, startup, health, shutdown and owner loop | Admin routes, Basic Auth and session deadline checks |
| [BotStateStore](../../hummingbot/model/bot_state_store.py) | Versioned control requests, PostgreSQL connection and owner-published state | Transactional command receipt and current/session-history state |
| [Ownership/send guard](../../hummingbot/core/order_submission_guard.py) and existing recorder | One authorized sender, control-version checks, durable orders/fills and HALT | Check active session/deadline/reference eligibility through the existing send boundary |
| Existing YAML/env loaders, SQL runner and offline simulator | Configuration, secrets, idempotent migration and end-to-end tests | LIFE profiles, session/API tests and LIFE market fixtures |

Prefer the existing spot PMM engine; prove the price-delegate integration first.
Its delegate interface is a Cython extension type, so this is an integration task,
not an assumption that any Python object can be assigned. Choose **one** engine
and document that decision before extending the strategy. Do not build both paths.
If V2 is selected, explicitly configure maker entry, exit and inventory behavior:
its example defaults can issue market exits and automatic market rebalances.

New logic should be limited to profile validation/reference calculation,
session/random selection, and admin API adapters. Follow-market and follow-token
share one calculation. No new trading engine, generic workflow framework, message
broker, scheduler service, credential store, migration framework or Redis is needed.

## 3. YAML profiles and pricing

Use one packaged catalogue, proposed path
`deploy/cloudrun/conf/life_mm_profiles.yaml`, copied to
`conf/life_mm_profiles.yaml` in the image. Reuse the existing strategy YAML for
ordinary MM settings. A small validated profile model supplies scenario settings
without duplicating the entire PMM configuration schema.

Illustrative catalogue, **not yet runnable**; named token pairs and numbers below
are examples, not trading recommendations:

```yaml
version: 1
connector: okx
trading_pair: LIFE-USDT
profiles:
  market_btc_eth:
    scenario: follow_market
    references:
      - {pair: BTC-USDT, weight: "0.5"}
      - {pair: ETH-USDT, weight: "0.5"}
    sensitivity: "1"
  sideways_default:
    scenario: sideways
    anchor: session_start_mid
  trend_eth:
    scenario: follow_token
    references:
      - {pair: ETH-USDT, weight: "1"}
    sensitivity: "1"
random:
  scenarios: [follow_market, sideways, follow_token]
  step_duration_seconds: 600
```

The catalogue also defines permitted/default session durations, maximum duration,
profile eligibility and quote/reference limits. API responses expose these fields
so FE does not duplicate validation constants. Validate positive weights summing
to one, supported pairs, positive finite numbers and required fields per mode.

For follow profiles, the initial model is:

`reference(t) = LIFE_anchor × (1 + sensitivity × Σ weight_i × (source_i(t) / source_i_anchor − 1))`

Persist the LIFE and source anchors when the step starts. Never use the absolute
BTC/ETH price as a LIFE quote. Use Decimal and validate the resulting price before
passing it into the existing engine. A stable sideways reference uses the saved
LIFE anchor. Quote bounds and inventory controls restrict the bot's own orders.

Capture an anchor from valid available LIFE market data, or require an explicit
operator-configured initial anchor when the market has no usable history. Missing
or stale data and invalid/non-positive prices stop new quotes and trigger quote
cancellation; do not silently change the selected reference. Bound divergence from
an available LIFE market reference and the configured quote range.

## 4. Sessions, duration and random selection

- Session states: `starting`, `running`, `paused`, `stopping`, `stopped`, `expired`,
  `halted`. These describe application state, not only HTTP request acceptance.
- Save session ID, profile/catalogue hash, start/expiry UTC timestamps, selected
  random step, step deadline, anchors and control version before permitting quotes.
- A requested duration starts when the session first becomes eligible to quote.
  Restart, pause, reconnect and downtime **do not reset or extend** an existing
  deadline. Expose `requested_at`, `started_at`, `expires_at` and remaining seconds.
- Pause cancels quotes while the clock keeps running. Resume is allowed only
  before expiry with valid data and without an unresolved recovery HALT.
- A duration update is measured from the original `started_at`. Reducing it past
  the current time expires the session; it cannot revive a completed session.
- Switching profile stops/cancels/reconciles old quotes before starting a new
  session and anchor set. Inventory and fills remain in the existing account/ledger.
- Stop/expiry cancels bot-owned quotes and retains inventory. It does not imply an
  automatic market liquidation. Deadline checking runs even when market data is
  unready, and the final send guard blocks queued/retried orders after expiry.
- Random selection happens only at step boundaries. Persist a draw/selected profile
  and deadline once; restart resumes that selection rather than drawing again.
  Align step windows to the original session start; each ends no later than the
  overall deadline. After pause/downtime, skip elapsed windows without catch-up
  orders. Reuse a saved current-window selection; otherwise draw/persist once for
  the current window. Late eligibility never extends that window's end.
- If no configured profile is eligible, disable quoting, report `blocked_reason`
  and retry eligibility while the clock continues. This is distinct from an
  operator pause, which requires explicit resume. An already selected profile's
  data failure blocks its quotes without redrawing inside the same window.
  Random selection, start and resume must not clear an existing recovery HALT.

Use the existing owner loop and database transaction machinery. Add only narrow
command/session records needed for retry safety and FE history, with snake_case
names and simple idempotent SQL through the existing runner. Keep strategies and
profile catalogues in YAML. Reuse original local file-mode behavior; this new
admin/session MVP targets the existing PostgreSQL foreground service.

## 5. Admin API and frontend contract

Mount `/api/v1/admin` on the existing aiohttp service and port. The bot and admin
API run in the same container. Current source exposes only health endpoints;
these admin endpoints and session controls still require implementation.

### Authentication and environment

- Use HTTP Basic Auth for **every admin endpoint**, including read endpoints and
  the API schema. Reuse `.env` locally and injected environment/Cloud Run secrets.
- Proposed settings: `ADMIN_API_ENABLED=false`, `ADMIN_API_USERNAME` and
  `ADMIN_API_PASSWORD` with no usable credential defaults, and
  `ADMIN_CORS_ORIGINS` empty by default. Centralize non-secret defaults in
  `hummingbot/client/config/defaults.py`; no `LIFE_` prefix.
- Enabling the API requires valid non-empty credentials. Invalid/missing Basic
  credentials return `401` with `WWW-Authenticate`; use constant-time credential
  comparison. Do not include credentials/Authorization headers in logs or JSON.
- Use HTTPS for deployed requests. Keep `/startup`, `/live`, `/ready` available
  for existing platform probes with their existing minimal responses.
- FE supplies Basic credentials per request after operator login. Never bake the
  password into the frontend build; retain it in memory for the current UI session.
  No JWT, user-account database or separate login/token service is required.
- For cross-origin FE, allow only configured origins, methods and headers
  (`Authorization`, `Content-Type`, `Idempotency-Key`). Handle preflight without
  returning admin data; actual requests remain authenticated.

### Endpoints

Paths below are relative to `/api/v1/admin`.

| Method / path | FE purpose |
|---|---|
| `GET /status` | Desired/actual state, active session/profile, reference price/freshness, deadline/time remaining, control/ack versions, snapshot age and recovery reason |
| `GET /profiles` | Packaged profiles, reference pairs, scenario/default/allowed fields, catalogue hash and random selection options |
| `POST /commands` | `start`, `switch`, `pause`, `resume`, `stop`, `update_duration`; validated action-specific payload |
| `GET /commands/{id}` | Command state: `accepted`, `applying`, `succeeded`, `failed`, `superseded`; effective version/session and sanitized error |
| `GET /sessions/current` | Current session and random step, selected profile, start/expiry and terminal outcome; `null` when absent |
| `GET /sessions` and `GET /sessions/{id}` | Paginated session history and details |
| `GET /orders` | Bot-owned open/history orders, bounded pagination and status/session filters |
| `GET /fills` | Recorded fills/fees with time/session filters and bounded pagination |
| `GET /balances` | Owner-published total/available LIFE and USDT balances, snapshot timestamp/staleness |
| `GET /performance` | Existing fill-based PnL, fees and volume, with valuation method, timestamp and unavailable fields explicitly marked |
| `GET /openapi.json` | Documented request/response/error schemas and examples for FE integration |

Spot inventory is exposed through balances; do not introduce a perpetual positions
API for this MVP. Reuse existing ORM/history and performance calculations, with
structured data functions instead of shelling out to CLI commands. Preserve
Decimal strings. Recorded-fill PnL is not a claim of current capital NAV.

An illustrative start request:

```http
POST /api/v1/admin/commands
Authorization: Basic <operator-credentials>
Idempotency-Key: <client-generated-request-id>
Content-Type: application/json

{
  "action": "start",
  "expected_version": 12,
  "selection": "fixed",
  "profile_id": "market_btc_eth",
  "duration_seconds": 3600
}
```

For `selection: random`, submit allowed scenario/profile IDs, overall
`duration_seconds` and `step_duration_seconds`; do not accept arbitrary exchange
pairs, file paths or code through the API. Start requires no active session;
`switch` explicitly replaces the active session. Commands targeting an existing
session include `session_id` to prevent an old FE tab from controlling a new run.

Command behavior:

1. Authenticate and validate; atomically persist the command receipt and versioned
   intent using the existing control-store transaction. Return `202` with command
   ID, control version and status URL. Do not start another engine in the handler.
2. Require `expected_version` and `Idempotency-Key`. Same key/payload returns the
   original receipt without resetting duration; reused key with different payload
   or a conflicting version returns `409`. Check matching retries before CAS.
3. The current owner applies the command. `acknowledged_version` currently means
   the owner observed the request; success also requires the requested state to
   finish applying. Report failures and superseding commands explicitly.
4. FE polls command/status endpoints. Define stable error codes, field validation
   errors (`422`), not-found (`404`) and unavailable runtime/storage (`503`).
   Responses use UTC timestamps, Decimal strings and bounded cursor pagination.
5. Return unavailable/stale balances or performance as such, never silently zero.
   Retain command/session identity on recorded orders so session filtering includes
   late fills and fills arriving after a scenario switch.

No manual raw-order, arbitrary config-write or automatic clear-HALT endpoint is
needed for scenario control. Keep existing operator recovery authoritative.

## 6. Implementation phases and checklists

Complete phases in order; update this plan and commit each finished phase.

### P0 — Prove the smallest integration

- [ ] Verify PMM price-delegate/reference update and cancellation hooks with a focused contract test; choose PMM or V2, one path only, based on actual integration effort.
- [ ] Record chosen engine, profile schema and runtime command/state contract. Preserve existing maker/inventory behavior and identify only required shared-code changes.
- [ ] Confirm the MVP assumptions: spot first, manually selected trend token, finite random sessions, pause does not extend time. Record any user changes before dependent implementation.

### P1 — Profiles and the three reference modes

- [ ] Add one validated packaged YAML catalogue and reuse existing strategy/env loaders/defaults. Provide examples for market follow, sideways and token follow.
- [ ] Implement one normalized follow calculation plus fixed-anchor sideways; reuse connector market data and existing quote generation.
- [ ] Verify listing/tradable state, stale/missing reference handling, positive price, spread/size/inventory limits and post-only execution.
- [ ] Test basket weights/units, single-token equivalence, fixed anchors, invalid inputs and unchanged original PMM behavior.

### P2 — Durable sessions and random selection

- [ ] Add minimal session persistence through existing PostgreSQL models/migrations; keep existing ownership, recorder and HALT behavior.
- [ ] Implement start/switch/pause/resume/stop/duration update, independent deadline checks and final-send expiry validation.
- [ ] Add random selection at step boundaries; persist selection/anchors/deadline before quoting and preserve overall end across restart.
- [ ] Test with a fake clock/fixed random seed: pause/expiry, shortening duration, restart during a step, downtime across boundaries, cancel/fill races and no eligible profile.

### P3 — Basic-authenticated admin API

- [ ] Extend the current aiohttp server with Basic Auth and validated env settings; preserve health endpoints and configure explicit cross-origin FE access when needed.
- [ ] Expose profiles/status, versioned commands and command completion using the existing control/owner path. Persist idempotency receipts atomically with control changes.
- [ ] Implement paginated session/order/fill reads, balance snapshots and existing performance outputs, with session attribution and freshness.
- [ ] Publish an OpenAPI contract and request/response examples covering every FE operation and validation/error state.
- [ ] Test wrong/missing auth, secret redaction, preflight, stale versions, concurrent commands, duplicate retries, superseded commands, paused/expired/HALT states and decimal serialization.

### P4 — Offline acceptance and operator documentation

- [ ] Extend the existing simulator with LIFE spot and reference pairs; run the actual existing strategy/OKX connector/PG service with the new profile adapter.
- [ ] Verify each scenario and a finite random session end to end, including a mid-session API switch, expiry, restart and preservation of anchors/deadlines/fills.
- [ ] Exercise FE workflow through real HTTP Basic Auth: read catalogue/status, submit command, poll completion, pause/resume/stop and inspect orders/fills/balances/performance.
- [ ] Recheck existing ownership/HALT, listing rejection, stale feed, DB failure and shutdown behavior; use targeted regressions plus the established deployment-image E2E gates.
- [ ] Document setup, YAML deployment, Basic Auth/secret configuration, API examples and recovery. Update evidence and commit; Cloud Run staging is not required.

## 7. Deferred scope and completion criteria

Defer perpetual/spot hedge coordination, TWAP inventory execution, automatic trend
screening, a general reservation/economics/NAV framework, Redis, a second service,
WebSockets, a user/RBAC system and editable configuration storage. A measured need
or an explicit new requirement can bring an item back into scope.

Keep the already implemented ownership, recovery, persistence and final-send
protections. Small essential additions remain: tradable instrument validation,
fresh usable references, bounded inventory/quote size, post-only quotes, session
expiry/cancel and authenticated versioned control. Simplifying the plan does not
justify bypassing these existing contracts.

Completion means the three profile behaviors, timed/random sessions and admin API
work through the existing engine, are exercised offline with real PostgreSQL and
are documented sufficiently for FE integration. It does not mean guaranteed
profit or a guaranteed market price path. No live orders or cloud deployment are
part of updating this plan.
