# LIFE / OKX implementation progress

Updated: 2026-10-25 (Asia/Ho_Chi_Minh).
Delivery checklist: [current plan](../plans/LIFE_OKX_IMPLEMENTATION_PLAN_TDD.md).

## P1 — Controller and configuration foundation

Implemented locally. The service/session integration is P2; admin endpoints are
P3. No exchange account, external order or deployment was used for this phase.

### Reuse map

| Existing component | Gap and smallest extension |
|---|---|
| `PMMSimpleConfig` / `PMMSimpleController` | Subclass as `LifeMMConfig` / `LifeMMController`; retain V2 level sizing and PositionExecutor actions, add weighted/fixed references and explicit MM policy |
| YAML loader and Pydantic models | Package `life_mm_profiles.yaml`; validate profiles, durations, market templates, instrument membership and combined declared budgets through `MMProfilesConfig` |
| V2 controller discovery and periodic reload | Existing loader discovers `life_mm`; API-managed effective configs resist periodic YAML replacement; ordinary updates still use the original validated path |
| `MarketDataProvider` dynamic books | Wrap existing initialize/remove operations with consumer tracking, retain shared/preexisting target feeds and propagate initial-book timeout correctly |
| Existing OKX position conversion metadata | Convert Decimal contract count with `_contract_sizes` for REST and WS; remove dependence on notional/entry price and integer net-position parsing |
| OKX spot instrument filter | Require live SPOT metadata; keep malformed-live-rule regression meaningful by supplying `state: live` in that fixture |
| PositionExecutor refresh/hold | Use post-only entries, disable automatic rebalancing/exit barriers, refresh with `keep_position=True`; reuse the existing held-order helper and retain cancelled partial fills |

Runtime controller checks include fresh source/target books, quote bounds, actual
spot balances, separate long/short/gross perpetual exposure, pending executor
entries and available funds for a batch. Partially filled pending entries can be
counted conservatively until their executor enters trading state. Held exposure
is read from actual connector balances/positions rather than added twice.
Perpetual setup requires confirmed HEDGE mode and a successful CROSS leverage
request; account-mode changes with existing tracked orders/positions are rejected.

An explicit `initial_anchor` is a fallback for a fresh empty/one-sided listing
book. A usable midpoint takes precedence. It cannot bypass stale book data or
replace a missing benchmark source. Once captured, controller anchors remain
fixed; durable session capture/recovery belongs to P2.

The YAML spot template exposes its inventory cap; the perpetual template exposes
long/short/gross caps and CROSS/HEDGE settings. Values are examples requiring
instrument/budget review before use. Loading the catalogue does not start trading.

### Red evidence

Meaningful failures were run before the corresponding implementation/fix:

- `test_position_quantity.py`: 12 failures for wrong `0.6 -> 1` rounding,
  zero/empty entry price, fractional signed net contracts, zero-position removal
  and absent contract metadata. An initial fixture event-loop issue was corrected
  before counting this Red run.
- Initial `test_life_mm.py`: 16 failures / 1 pass for live-price drift instead of
  saved references, invalid profile acceptance and non-post-only entry defaults.
- `test_mm_profiles.py`: missing deployment catalogue assertion first; then six
  failures for the missing selection/override/budget behavior.
- `test_reference_subscriptions.py`: three failures for duplicate subscriptions,
  premature shared/target removal and non-idempotent release; a separate failing
  timeout assertion exposed the existing false-success startup return.
- Instrument eligibility: five failures for suspended/preopen/unknown spot state.
- PositionExecutor: cancelled `0.4` partial fill incorrectly became `EARLY_STOP`
  without held exposure; a real `InFlightOrder` and `TradeUpdate` reproduce it.
- Additional controller Reds covered inventory/pending/gross caps, stale-basket
  handling, filled-position refresh, account setup, empty listing fallback,
  cancellation on quote bounds and midpoint precedence over the fallback anchor.

### Green evidence

Python: `/opt/homebrew/Caskroom/miniforge/base/envs/hummingbot/bin/python`.

Focused new suites: **68 passed**. Broader affected suite: **510 passed**:

```sh
python -m pytest -q --disable-warnings --tb=short \
  test/hummingbot/strategy_v2/controllers \
  test/hummingbot/strategy_v2/executors/position_executor \
  test/hummingbot/strategy_v2/executors/test_executor_orchestrator.py \
  test/hummingbot/data_feed/test_market_data_provider.py \
  test/hummingbot/client/config/test_mm_profiles.py \
  test/hummingbot/connector/derivative/okx_perpetual \
  test/hummingbot/connector/exchange/okx \
  test/hummingbot/strategy/test_strategy_v2_base.py
```

Repository pre-commit checks pass. The broad suite reports existing deprecation
warnings and an unawaited AsyncMock warning; the focused new suites have no
RuntimeWarning. These results cover unit/regression behavior, not image, load or
exchange acceptance.

## P2 — Current session and random selection

Implemented locally after P1 commit `952eb5d49`. HTTP command admission/receipts
remain P3; the image/simulator matrix and Demo acceptance remain later gates.

### Reuse map

| Existing component | Gap and smallest extension |
|---|---|
| `bot_state`, SQL runner and `BotStateStore` | One nullable `mm_session` JSONB column via replayable `008_add_mm_session.sql`; transactional control CAS and owner/generation-fenced current-session updates |
| V2 runner's existing config loader | Accept host-generated effective controller dictionaries through the same module/model discovery, without writing YAML or creating another runner |
| `ServiceHost` observer/start/stop lifecycle | Apply session control and random-step changes through existing core shutdown/reconciliation/start; observe expiry independently of market readiness |
| `BotOwnership` and final REST gate | Bind executor session/generation to connector tasks with ContextVar; reject missing/expired/superseded authority before intent and again after throttling/auth, while allowing cancellation |
| Existing order `expTime` | Clamp its deadline to the session/step boundary as well as the existing transport limit |
| P1 controller and `MarketDataProvider` | Restore saved anchors; withhold executor creation until all selected markets' readiness/anchors are committed; reuse public trading-rule fetch for public-only reference connectors |

`MMSession` stores the effective request, configuration hash/snapshot, chosen
profile, current random window/generation, anchors and deadline. It is current
operational state, not a session journal or editable database configuration store.
Deployed YAML still supplies defaults for a new request. The hash covers both
those defaults and the effective API request, including basket and duration.

The first clock starts only after joint admission. Random steps keep that overall
deadline, initially draw uniformly by scenario then profile (revised by the P3
configuration clarification below), retain the current draw on
restart and skip elapsed windows without catch-up orders. Old executor retries
retain their original scope, even if the owner's control version is unchanged.
Real database time is used for activation and final authorization. Session/step
transitions retain the existing recovery HALT and owner fencing.

The service restores anchors before starting the current controller generation.
A new step captures each market's LIFE anchor and one shared benchmark basket
only after old-core cleanup and joint readiness. PostgreSQL records that capture
before local controllers are admitted. One stale market blocks/cancels both;
readiness recovery retains the saved clock and anchors. Shared collateral is
selected conservatively from observed adapters and never added twice.

### Red evidence

- Eight fake-clock/state failures preceded session selection, activation and
  random-window implementation; a later failing hash assertion caught omission
  of the effective request from the configuration hash.
- Four real-PG failures preceded session control persistence/CAS and migration
  replay behavior. Follow-up cases cover concurrent starts, owner transfer and
  stale generation/owner updates.
- Two real-PG final-gate failures reproduced acceptance of an old random step and
  expired authority; cancellation remains permitted after revocation.
- Executor retry context initially reached both connector tasks as `None` rather
  than the original session/generation.
- Controller tests reproduced quoting before PG session admission and recapturing
  anchors after restoration; effective-runner loading initially produced no
  controllers; four service tests initially failed joint readiness/admission.
- Public-only reference connectors do not run the private trading-rule polling
  loop; a failing startup assertion preceded reusing the existing public metadata
  request for that path.

### Green evidence

**1,178 unit/regression tests, 2 subtests passed**:

```sh
python -m pytest -q --disable-warnings --tb=short \
  test/hummingbot/strategy_v2 \
  test/hummingbot/strategy/test_strategy_v2_base.py \
  test/hummingbot/client/config test/hummingbot/cli test/hummingbot/model \
  test/hummingbot/core/test_mm_session.py
```

**82 PostgreSQL tests passed** on disposable, loopback-only PostgreSQL 14:

```sh
python scripts/test_postgres.py --disable-warnings --tb=short
```

The full PG run exercises migrations, recorder/ownership/HALT regressions and the
new session CAS/replay/owner-transfer cases. Existing recovery tests retain order
and fill-race coverage; the image-level scenario matrix remains P4.

Two legacy test setup issues surfaced in the broadened gates and were corrected:
the connection-manager bootstrap test now explicitly enables migration on its
empty disposable DB; SecurityTest owns/restores its loop and scheduler instead
of depending on another suite's leftover event loop. Runtime defaults and secret
handling were not changed. Existing deprecation/AsyncMock warnings remain visible.

## P3 — Admin API, reporting and observational reconciliation

Implemented on the existing aiohttp/owner/recorder paths. Operational details and
response semantics: [admin API guide](../admin-api.md). No OKX account, live order
or deployment was used. The following evidence covers local P3 behavior; the
deployment image/scenario matrix, load/soak and Demo gates remain P4–P6.

### Reuse map

| Existing component | P3 extension |
|---|---|
| aiohttp service and environment defaults | Basic Auth for all admin routes/docs; optional API enablement, CORS and reporting timezone |
| `BotStateStore.request_mm_session` and owner publication | Caller-owned transaction for atomic idempotency receipts; accepted/applying/final outcomes require actual state |
| Recorder ORM tables and existing indexes | Bounded market-scoped keyset history and batched order-side projection; no per-row queries |
| Connector balance/position REST and WS loops | Retain raw cash/equity and position observation times; cache verified account UID |
| V2 `controller_reports` cache | Expose existing performance definitions and valuation scope without a second PnL engine; the CLI `PerformanceMetrics` daily-slice assumptions do not match this endpoint |
| Existing status JSONB and fenced publication | Preserve last observations after stop/mode switch; one daily account snapshot upsert per minute with actual observed timestamps |
| Existing SQL runner and recorder events | Idempotent receipt/snapshot migrations, event-day fee/rebate/funding SQL aggregates using one repeatable-read snapshot |
| Existing connector readers/contract conversion | Bounded observational reconciliation with durable receipts, owner fencing and separate market coverage; no corrective recovery call |

### TDD and validation evidence

- Actual HTTP Reds covered missing handlers, authentication/CORS, malformed
  commands, receipt retries/CAS and protected local Swagger. Real PostgreSQL HTTP
  tests exercise spot/perpetual/both, superseded start, random switch, failed
  startup and stop completion; acknowledgement alone remains applying.
- History reads preserve identical spot/perpetual pair names, Decimal money and
  original fees. Query count remains constant (at most three) as order page size
  grows. Runtime reference discovery shares one metadata refresh for concurrent reads.
- The recorder fee-conversion Red observed a false zero. New unavailable
  conversions now stay null with original fees intact; ambiguous legacy zero
  conversions remain explicit in reads/daily coverage.
- Daily PostgreSQL tests cover local midnight boundaries, charges/rebates,
  unconverted fees, perpetual funding, late fills, shared-wallet scope, snapshot
  upsert/replay/rate limits, owner fencing and missing dates. A concurrent-fill
  Red returned fill count from before the insert but fees from after it; the
  repeatable-read aggregate now reports one consistent snapshot.
- UTC regression first observed `Asia/Bangkok` from a database default. Every
  runtime connection now explicitly uses `timezone=UTC`; timestamps/response
  offsets preserve the instant and serialize UTC. Reporting dates remain labels.
- Reporting Reds distinguish engine equity from wallet cash, keep stopped and
  disabled-market observations stale, and flag assets absent from a newer read.
  Successful empty position snapshots remove closed legs; failed snapshots retain
  exposure, and a newer WS event is protected from an older REST response.
- Reconciliation tests cover actual fee/quantity/price differences, bounded/missing
  pages, concurrent local/exchange fills, per-market timeout isolation and actual
  long/short contract sizing. Active and held exposure both contribute. Real-PG
  tests prove checks do not change control version, desired state, session or HALT;
  old owners cannot complete reports. Receipt success is distinct from report match.
- A real foreground service with external networking disabled serves statistics
  and completes an unavailable observational check while stopped, without starting
  a strategy. FE polling reads stored reports rather than exchange endpoints.
- Swagger annotations generate deterministic OpenAPI with separate input/output
  schemas, Decimal-string responses and fixed/random/runtime-reference examples.
  Protected UI assets are pinned, bundled and checksum-verified. No CDN or remote
  validator; CI checks schema drift.

Final unit/regression gate: **1,362 passed, 2 subtests passed**. Existing upstream
deprecation/AsyncMock warnings remain visible. Swagger drift and all changed-file
pre-commit hooks pass. The full disposable PostgreSQL gate passes **100 tests**.

### P3 configuration clarification — explicit profile selection

Default profile IDs now match the three scenarios: `follow_market`, `sideways`
and `follow_token`. Selection remains `fixed` (one `profile_id`) or `random`,
independent of `market_mode: spot | perpetual | both`. Existing configured
reference pairs and market budgets are preserved.

YAML random defaults now contain `profile_ids` and `step_duration_seconds`.
New random sessions use that explicit pool; API overrides may narrow it. Each
profile has equal draw probability, including when several profiles use the same
scenario. Saved requests retain their effective pool, current draw and deadlines.
Archived session catalogues with the old `random.scenarios` format are decoded
on restore; new YAML accepts only the explicit profile-pool format.

TDD evidence: three initial failures demonstrated rejection of the new format,
ignoring the YAML pool, and scenario-first weighting (1,506/3,000 draws for the
single sideways profile). A fourth failure demonstrated that an old saved
catalogue could no longer restore. All pass with the revised pool/restore logic.
The targeted configuration/session/service/HTTP/Swagger suite passes **44 tests**;
the disposable PostgreSQL session/command-receipt suite passes **11 tests**.
This clarification is included with the completed P3 reporting APIs.

## Next — P4

Wire and test explicit OKX Demo/live transport selection using local transports,
then extend the deployment-image simulator matrix. P5 load/soak and P6 dedicated
OKX Demo acceptance follow in order.
