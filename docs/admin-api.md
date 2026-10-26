# LIFE MM admin API

Optional `status.markets[].quote_guard` reports strict guard admission, qualified
market price, deviation, UTC observation/recovery times and source quality. The
catalogue exposes opt-in market `quote_guards` and profile `sideways_range` through
the existing `/profiles` contract; trading selection still uses existing commands.
See [strict quote guards](life-mm-quote-guards.md) for bounds and recovery semantics.

Optional `status.drawdown` exposes the covered V2 report's limits, stable scope,
peaks, observed PnL/drawdown and readiness/HALT reason. Stopped or stale current
PnL is `null`; known peaks and confirmed breach remain visible. This metric
includes reported trade fees and excludes funding; it is not account equity.
See [managed MM drawdown](life-mm-drawdown.md) for configuration and bound policy.

Optional `status.spot_outflow` exposes the bound rolling spot USDT policy,
committed/reserved/remaining capacity, original non-USDT fees and BUY-only
coverage/denial reasons. Capacity loss does not stop funded SELL or perpetual
admission. See [spot outflow](life-mm-spot-outflow.md) for provenance, window and
terminal-proof semantics. Owner-unavailable observations are marked stale.

The existing aiohttp service exposes `/api/v1/admin`. Configure
`ADMIN_API_ENABLED=true`, `ADMIN_API_USERNAME` and `ADMIN_API_PASSWORD` through
the existing `.env` loader or injected deployment secrets. There are no usable
credential defaults. Health probes remain separate. All admin endpoints,
including `/docs`, `/openapi.json` and bundled Swagger assets, require Basic Auth.

`ADMIN_CORS_ORIGINS=*` permits an FE on any origin. Send an explicit Basic
`Authorization` header with `credentials: "omit"`. Wildcard responses omit
`Access-Control-Allow-Credentials`; an explicit comma-separated origin allowlist
is also supported. The API does not create login sessions or persist FE passwords.

Generate the offline contract with `make swagger`; CI runs `make swagger-check`.
The protected `/docs` UI uses the packaged contract/assets with no CDN or remote
validator. Authorization persistence is disabled. Annotations live on handlers;
Pydantic request and response models supply the schemas. Response money fields
are Decimal strings. All timestamp responses use UTC `Z`.

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

## Selection and commands

Read `/profiles` for deployed defaults/limits and `/reference-pairs` for searchable,
paginated OKX spot USDT references and metadata freshness. Discovery uses the
existing public connector parser/throttler, caches metadata for 60 seconds and
shares concurrent refreshes. An unavailable refresh retains its last observation
and marks it stale. YAML reference pairs are defaults, not a closed allowlist.

Two controls are independent:

- `selection: fixed` chooses one `profile_id`: `follow_market`, `sideways`, or
  `follow_token`. Weighted basket or single-token references can be overridden.
- `selection: random` uses the YAML `random.profile_ids` pool and
  `step_duration_seconds`; an API `profile_ids` override may narrow that pool.
  Each profile has equal probability; consecutive draws may repeat.
- Either selection accepts `market_mode: spot | perpetual | both`.

POST `/commands` with `action: start | switch | stop`, an `Idempotency-Key` header
and the `expected_version` read from `/status`. Switch/stop also require the current
`session_id`. A `202` receipt confirms acceptance; poll `/commands/{id}` for
`accepted`, `applying`, `succeeded`, `failed` or `superseded`. Acknowledging the
control version alone does not complete a command. Runtime connector/feed/account
admission must succeed before quotes are enabled. Duration begins at joint readiness.

Matching retries return the original receipt before stale-version checks; a key
reused with another body or a conflicting version returns `409`. Invalid static
settings return `422`. An accepted command that fails runtime startup is reported
as failed; readiness waits remain visible in status. Neither commands nor checks
clear a recovery HALT. Swagger includes fixed, random and runtime AVAX examples.

## History and charts

`/orders`, `/orders/{id}` and `/fills` read the existing recorder tables. History
has bounded keyset pagination (`limit` 1–500), UTC half-open `from`/`to` timestamps
and market/pair filters. Keep `connector`, `order_id` and the native fill ID together:
exchange trade IDs are not global IDs. Cursors bind the original filters. Order
details expose a bounded status timeline and a link to paginated fills.

`market=all` returns distinct spot and perpetual data even for the same LIFE-USDT
pair. Switching the current trading mode does not hide recorded history.

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

`/statistics/daily` defaults to seven reporting dates and accepts `from` (inclusive)
and `to` (exclusive), with a maximum 90-day range. `REPORTING_TIMEZONE` defaults to
`Asia/Ho_Chi_Minh` for day boundaries only. Every PostgreSQL connection explicitly
uses UTC, timestamps are `TIMESTAMP WITH TIME ZONE` or UTC epochs, and local dates
are labels with their timezone recorded. Changing the reporting timezone does not
relabel old balance snapshots.

Daily statistics use a bounded 32-entry in-memory cache to coalesce concurrent
dashboard polls. `ADMIN_STATISTICS_CACHE_TTL_SECONDS` defaults to 5 (0 disables;
maximum 60). Responses retain the aggregate's original `server_time` and balance
observation timestamps. Cache expiry triggers a fresh consistent database read;
failures are not cached. This cache is never used for trading or command state.

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

## Verification scope

P3 uses local unit/HTTP tests, disposable PostgreSQL and real foreground service
processes with external networking disabled. Deployment-image scenario tests,
Demo transport wiring, local load/soak and final OKX Demo acceptance remain P4–P6.
P3 does not deploy to Cloud Run or use an OKX account.
