# LIFE / OKX implementation progress

Updated: 2026-10-27 (Asia/Ho_Chi_Minh).
Delivery checklist: [current plan](../../docs/plans/LIFE_OKX_IMPLEMENTATION_PLAN_TDD.md).

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
response semantics: [admin API guide](../../docs/fe-integration.md). No OKX account, live order
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

## P4 — Local image and Demo transport integration

Wire and test explicit OKX Demo/live transport selection using local transports,
then extend the deployment-image simulator matrix. P5 load/soak and P6 dedicated
OKX Demo acceptance follow in order.

Environment contract: [OKX live/Demo routing](../../docs/okx-operations.md#environment-and-credentials). Both existing
adapters share process-level mode/site validation and a REST header preprocessor;
public reference discovery and candle business feeds follow the same selection.
Dedicated Demo credential variables never fall back to live values. Existing
AWS live routing remains separate and conflicting combinations are rejected.
Affected WS routes now use default TLS port 443.

Transport Reds caught the perpetual public feed's hard-coded live host and missing
Demo REST header. Real local HTTP/WS tests exercise authenticated GET/POST requests,
login and reconnect for both connectors across global/US/EEA route selections.
Credential Reds demonstrated live-key fallback; dedicated Demo keys are now required.
Changed connector/candle/config regressions pass (305 tests), broader isolated
connector/config routing regressions pass (379 tests), and all 100 real-PG tests pass.

The deployment image exercised all three scenarios in spot/perpetual/both plus
weighted references and clean restart. Its reconciliation read then exposed a
runtime interface error: `ExecutorBase` uses `is_closed`, not `is_done`. A new
regression with an actual `PositionExecutor` reproduced it; the reader now uses
the upstream interface. The complete image gate is still open while rerunning it.
No exchange account or external order has been used.

Further image-level Reds found account-wide pending orders being mislabeled as
out-of-scope orders for the other market. Observational comparison now filters
the returned instrument type before checking ownership/scope; a regression
reproduces the shared-account response.

The paused-owner gate also exposed an idle publication transaction retaining row
locks after its separate advisory ownership session had been terminated. Runtime
connections now bound `idle_in_transaction_session_timeout` to five seconds;
a real PostgreSQL test verifies automatic rollback and successor row-lock access.
This affects only idle open transactions, not the committed idle owner session.

With held exposure, failed startup recovery occurred before the strategy's first
clock tick; upstream position persistence attempted `int(NaN)` during cleanup.
A regression reproduces that exact path. Held-position metadata now uses the
provider's actual UTC observation time when the strategy timestamp is not finite;
it does not change MM session/step clocks or authorizations. Orchestrator and
observational comparison regressions pass (51 tests). Final image gates remain open.

The updated recovery rehearsal passes with two independent fill records and actual
held exposure: weighted references, clean restart/anchors, shared stale-feed stop,
separate market fills/fees, one shared wallet, matched observational reconciliation,
finite random steps/expiry, and DB-loss restart retaining HALT. The port's older
pause/lost-ACK/DB/recorder fault sequence also passes after the idle-transaction fix.
These rehearsals use a derived image to iterate on Python-only fixes; they are not
the final deployment artifact acceptance. The standard Dockerfile artifact is
undergoing the full matrix and upgrade/rollback gate separately.

Latest broad gate: **1,557 tests + 2 subtests pass**; disposable PostgreSQL:
**101 tests pass**. `make test-cloudrun-regression`, Swagger drift and pre-commit
checks pass. Existing upstream warnings remain visible. The offline Make target
now defaults to Demo routing with public dummy keys; local transport checks and
LIFE controller tests are included in the dedicated regression target.


### Final P4 artifact acceptance

The standard Dockerfile image passes the complete isolated-network E2E gate in
Demo routing mode using only public dummy credentials. All six scenarios pass:
schema rejection/validation, V1 spot lifecycle/faults, V2 spot, V2 perpetual,
LIFE MM and upgrade/rollback between distinct compatible images. LIFE covers all
nine profile/market combinations, runtime AVAX selection, shared weighted
references, anchor/deadline restart, stale-both cancellation, separate unique
fills/fees, deduplicated shared cash, matched reconciliation, random steps/expiry,
held positions and DB-loss restart retaining HALT. No Cloud Run or OKX account
was contacted. All test containers/networks were removed afterward.

- Report: `build/e2e-offline/20261025-052617-198127/result.json`
- Image ID: `sha256:262fe296c57e6b0d143d6d9224f6df1f3b96dcf929b0cb1b5e9a843eafbd2fe1`
- Registry manifest: `sha256:fa93ef205ba289cf6cb820d83cf4ba2cdbccd2e7d4a82e67c0a2b14ac58a78fd`
- Uncompressed image size: 1,364,444,310 bytes (under the 1.5 GiB budget).
- Baseline config hash: `c9897fd4a3894c86c2724e5fab9cc615d837c35d615a307ebd8e2d8197ff7cfc`;
  per-fixture hashes, including the tested LIFE YAML, are retained in the report.
- Host is macOS arm64 with the Linux amd64 image under Docker emulation; the
  test bot is limited to 1 CPU / 2 GiB. This gate is functional acceptance, not P5
  load/soak capacity evidence.

Final gates: 1,557 unit/regression tests plus 2 subtests; all 101 PostgreSQL tests;
all four groups in `make test-cloudrun-regression` (143 + 11 + 886 + 88 tests);
Swagger drift and pre-commit checks. The Make target now stops on the first
failing group instead of allowing a later group to mask failure.

## P5 — Load, recovery fixes and acceptance

Run opt-in local mixed load, bursts, faults and a real 60-minute soak on the
accepted deployment artifact. Record offered and achieved rates and the budgets
in the plan before final dedicated-account OKX Demo acceptance (P6).

The large-history probe exposed real query cost and a populated-performance API
serialization error. Two failing query-plan tests now pass with the additive
global pagination indexes in SQL `012`; a bounded five-second daily statistics
cache coalesces concurrent reads using the existing cachetools dependency.
The original report timestamp remains visible and trading never reads this cache.
A failing populated-report HTTP test now passes after rehydrating JSONB enum keys
for the original upstream PerformanceReport model. No second PnL engine was added.

The opt-in runner, declared assertions and diagnostic failures are documented in
[the load gate](../../docs/life-mm-load-tests.md). Short probes are not final acceptance.
The standard image rebuild, complete load durations and fault windows remain open.

The short burst rehearsal now passes 20 reads/s (aggregate p95 107 ms / p99
660 ms), 5x books, concurrent retry/conflict checks and independent fills/fees/
positions. It exposed another upstream reuse gap: PositionExecutor's HEDGE
entries were netted because its config has no position_action. A failing test
now passes with separate LONG/SHORT holds and storage/restore coverage; all
82 orchestrator/position executor tests pass. A separate real-PG failing test
now verifies cleanup skips only proven-unsent failed creates while retaining
remote readback for sent failures. These rehearsals use a derived image for
iteration, so final artifact load acceptance remains open.

Latest validation after the hedge/cleanup fixes: **105 disposable-PostgreSQL
tests pass**; `make test-cloudrun-regression` groups pass (149 + 11 + 895 + 88).
The rebuilt standard image is `sha256:32570e999d340ea8cb1b885fc614d11c387296998860b468cd1ca5afe2e477f7`
(1,364,471,788 uncompressed bytes). Final image E2E/load acceptance is in progress.

P6 prerequisite is unavailable locally: the `.env` has no dedicated spot or
perpetual Demo credential set and no explicit Demo environment selection (only
presence was inspected; no secret value was printed). No OKX account calls have
been attempted. The P6 checklist remains open until that dedicated configuration
is supplied after local gates pass.

All five fault-under-load cases pass on the standard P5 image: basket feed
interruption, owner restart, expiry with queued books, real DB lock delay and
complete DB outage. Both DB cases fail closed and retain HALT after restart.
Evidence: `build/mm-load/20261025-062535-7b127e/result.json`; fault response errors
are classified separately from healthy latency capacity. The full image matrix
and sustained mixed/soak remain in progress.

The rebuilt standard image passes the full six-scenario E2E matrix, including
LIFE's nine profile/market combinations and upgrade/rollback to the accepted P4
artifact. Manifest: `sha256:764f6c43e76916cb5ee33676e533165d30ae6223ec1570844ae974ca8a102723`.
The final `make test-mm-load` all-profile run has started after other workloads
and builds finished; it is not marked passed until its full durations finish.

The first all-profile attempt stopped before warmup during rapid start/stop:
the simulator incorrectly returned HTTP 500 for cancel-not-found, and a tracker
transition from FAILED to CANCELED lost the earlier proof that a create never
passed transport. The simulator now returns item code 51400, and ownership keeps
explicit no-transport rejection evidence until the next clean prepare. Sent or
uncertain effects are still read back. Both terminal states have a failing-then-
passing PostgreSQL regression; the full PG suite now passes **107 tests**.

The corrected 60-second probe passes at 20 reads/s, ~100 books/s and 1 fill/s:
p95 61 ms, p99 537 ms. This uses dispatch-inclusive latency and actual server
counter timing. A newly rebuilt standard image
`sha256:dd9b125a04d16c73a6feec7c5458f116fadd5aeb438fbce3142e7a8649b8ee29`
(1,364,472,526 bytes) is running the sequential final E2E/load pipeline.
Neither short probes nor the aborted pre-warmup attempt count as P5 completion.

The first full mixed window met rate/latency budgets but independent comparison
rejected invented simulator fills after a terminal cancel. Three failing simulator
regressions now pass: terminal orders reject injection, and a perpetual fill's
publication snapshot cannot be overwritten by a later cancel. The rerun passes
15-minute mixed with **18,000 reads / 900 new fills**, p95 **78 ms**, p99 **551 ms**,
all recorder identities/units/fees matching the independent ledger.

Its following switch exposed a real V2 partial-cancel recovery bug: cancel events
move the entry out of the live slot into `_failed_orders`, which hold collection
ignored. The earlier partial-cancel unit test had omitted that callback. After
adding the real callback the test fails, then passes when the existing collector
includes old tracked orders with order-ID deduplication. All 82 orchestrator/
position executor tests pass. A five-second-refresh container rehearsal now
passes burst, switch/stop/start and separate held LONG/SHORT recovery.

The runner now additionally compares the existing reconciliation projection of
held-plus-active exposure, and rejects HALT during healthy profiles. Final image
rebuild/E2E and the complete load/soak rerun remain open; no full P5 completion
has been claimed from the intermediate measurements.

Implementation/regression checkpoint committed as `1b7ca57b4`. The newly rebuilt
standard image `sha256:3c096d7852aed5c490fe0d70742003259b0136c24ecfc8d4dbc42913ff8a06ba`
(1,364,475,211 bytes) passes the complete six-scenario E2E matrix. Its current
load evidence is `build/mm-load/20261025-080606-4367d6/result.json`.

Warmup, mixed and bursts all pass, including independent recorder comparison and
the existing V2 held-plus-active exposure projection. Mixed: 18,000 reads and
900 added fills, p95 73 ms / p99 545 ms, 20 reads/s, ~100 updates/s, 1 valid fill/s.
Bursts: p95 85 ms / p99 616 ms, concurrent retries/conflicts and actual command
application meet budgets, positions restore correctly after long fill history.
Daily snapshots, bounded observational checks and constant page query counts pass.
The real 60-minute random/both soak is running; final fault rerun and completion
evidence remain open. This entry does not mark P5 complete.


### Final P5 acceptance — 2026-10-25

**P5 passes.** Standard image
`sha256:3c096d7852aed5c490fe0d70742003259b0136c24ecfc8d4dbc42913ff8a06ba`
passes the entire six-scenario E2E matrix and the full opt-in load gate:
5-minute warmup, 15-minute mixed, 90-second bursts, 60-minute random/both soak,
and all five fault windows. All test containers/networks were cleaned up.

Mixed: p95 73 ms / p99 545 ms. Soak: 72,000 completed reads, 3,600 valid fills,
p95 74 ms / p99 584 ms, zero unexpected healthy HTTP errors and no healthy HALT.
Peak RSS 341 MiB; first/last median RSS growth 14.55 MiB, median queue 2 → 2 and
pool checkout p95 1.70 → 1.58 ms. Burst backlog drains in 0.53 seconds; actual
switch/stop/start and concurrent retries/conflicts meet the declared budgets.
All 4,890 native test fills/fees/units and both account/V2 exposure projections
match the independent ledger. Random steps retain their session start/deadline.
DB-delay and DB-loss restart both retain HALT.

The plan checklist is updated. Evidence: [load report](../../docs/life-mm-load-tests.md),
[portable sanitized data](../life-mm-load-acceptance.json), and full local
`build/mm-load/20261025-080606-4367d6/result.json`. All 107 PG tests and regression
groups 149 + 11 + 898 + 88 pass; Swagger and pre-commit pass. Daily cache-miss
latency (~1.1 s) and upstream teardown pending-task diagnostics are reported
explicitly; aggregate acceptance does not erase those follow-ups.

### Previous gate at P5 completion — P6 was not configured

At P5 completion, dedicated spot/perpetual Demo credentials and an isolated
Demo DB were absent, and no OKX account or Cloud Run deployment had been
contacted. P6 therefore remained open pending account/instrument checks,
spot/perpetual/both workflows and the 30-minute conservative observation.

### Completed — Cache, SQL and task-lifecycle optimization follow-up

The [separate optimization report](../life-mm-optimization-review.md) records
before/after results, code changes, rejected slower aggregate trials, image
hashes, test commands and remaining limits. Valid cache hits bypass unrelated
misses; the daily transaction avoids measured JIT overhead; balance snapshots
batch all currencies into one SQL. Native medians improve 812 → 504ms for
100,000-fill daily reports and 184 → 28ms for 100-currency snapshots (100 → 1 SQL).

Controller generations, reference release, action-listener retirement and public
connector closure are awaited, including immediate repeated stop/start/stop.
Asyncio traces identified the residual upstream funding-payment poller that
network stop never cancelled; that poller is now cancelled/drained before its
notifier is replaced. Final-image LIFE E2E covers nine market/profile cases and
recovery; both its debug logs and the burst logs have zero pending-task or
unawaited-coroutine diagnostics.

All 1,249 native regression and 111 PostgreSQL tests pass; Swagger and pre-commit
checks pass. The standalone 90-second burst passes, with 1,800 reads, aggregate
p95/p99 64/518ms, 90 independently matched fills and actual stop 0.53s. Daily-route
p95 is still 570ms in that short window. The original full soak and six-scenario
rehearsals retain their own image provenance; this follow-up does not recertify
a 60-minute soak of the new artifact. P6 dedicated OKX Demo acceptance remains
pending configuration. Implementation commits: `dd68d41a8`, `5c56e7869`,
`e4afbcac6`, `c00285ab7`, `742290063`.

### P6 in progress — authenticated Demo setup and reference-book regression

Dedicated Demo credentials authenticate for spot/perpetual with Read/Trade
permissions. BTC/ETH/AVAX metadata is available; BTC-USDT is the explicit trading
substitute and follow_token uses AVAX-USDT. Futures, Hedge and Cross 1x are
confirmed. The isolated PostgreSQL Demo database uses UTC and replays all 13
scripts twice successfully.

The reconstructed standard image passes the selected nine-case LIFE offline E2E
and recovery. The first real spot/follow_market case instead exposed an existing
reference-book reuse defect when BTC is both target and benchmark. The run
stopped before any order was recorded. Two new regressions fail before and pass
after a small provider change that borrows an already tracked book. All 140
affected tests pass; the reference tests are included in the Makefile gate.
The corrected standard image passes the selected nine-case LIFE offline E2E
and recovery. The previous failed attempt's durable HALT was reviewed separately
against an empty exchange/DB and unchanged initial balances before explicit local
Demo recovery; startup never clears it automatically. Real Demo workflows and
the 30-minute observation remain open. See
[the Demo report](../okx-demo-acceptance.md) for evidence and gaps.

The next real run completed all three spot profiles, including AVAX following:
12 exchange-confirmed post-only buy/sell orders were recorded and cancelled,
with same-key receipt retries verified. No fills occurred. Perpetual startup
then exposed an unrelated `TESTING-USDT-SWAP` preopen row with empty `ctVal` in
Demo metadata. Its float conversion prevented initializing BTC's order book;
the service retained HALT without placing a SWAP order. The datasource now
reuses the existing active/linear/SWAP filter and rejects unusable contract
values. The affected suites pass 201 tests and seven subtests after Red evidence.
Commit `c9b858e36` records this fix. The final standard image passes the selected
LIFE offline E2E/recovery. After reviewing all 12 closed order IDs, unchanged
balances and no open orders/positions, the known failed-startup HALT was resolved
explicitly with history preserved. The corrected datasource initializes a real
Demo BTC SWAP snapshot, and the final image now places/cancels perpetual maker
quotes. Remaining market cases and the observation are in progress.

### P6 Demo protocol completed — 2026-10-26 (Asia/Ho_Chi_Minh)

The observation image `sha256:8401c10c2c5c50f4d426db6f2e8529506923fa77b02b0090f028e56c11c04fc2`
passes the selected LIFE offline E2E/recovery and the nine real profile/market
cases. Seventeen start/switch/stop receipts complete with matching idempotent
retries. The random/both session ran for its real 1,800-second duration
(1,801.585 seconds observed through cancellation), crossed both step boundaries,
and preserved its anchors/start/deadline through a graceful minute-15 restart.
Both scoped reconciliations matched spot and perpetual (12 orders per market in
each window). FE probes return 200 and deduplicate the shared account correctly.

The observation image created 288 orders (144 spot/144 perpetual), all cancelled. The
database retains those plus the earlier 12 cancelled pilot orders. Independent
post-stop reads confirm zero pending orders, zero perpetual positions, unchanged
BTC/ETH/OKB/USDT cash, UTC, an expired/stopped session, released owner and no HALT.
The bot service is stopped and the dedicated PostgreSQL data/container retained.

Natural fills/fee accounting, filled-position restart and funding payments are
unverified on Demo because no fill/payment occurred; their checkboxes remain
open. The long random sample drew follow_market three times from the full pool;
the separate nine-case matrix covers all profiles. No new full load/soak or Cloud
Run deployment is claimed. See [the completed report](../okx-demo-acceptance.md)
and [portable sanitized evidence](../okx-demo-acceptance.json).

Final log review retains 40 duplicate-cancel `51400` errors from that observation,
each paired with a successful cancellation event and terminal REST/DB readback.
The perpetual adapter's missing response mapping is fixed. A new regression also
exposed late cancel acknowledgements overwriting CANCELED/FILLED state; the shared
cancel path now preserves terminal observations. Commit `32285f5c8` records the
fix, and full Cloud Run regression passes 1,259 tests/nine subtests. The retained
standard image `17d530e26018...` passes selected local E2E/recovery, a real Demo
`51400` probe with PG ownership/new risk disabled, and idle service startup/clean
shutdown. The probe confirms pending-before-readback and terminal-state retention
after a repeated reply, with no new orders. History remains 300 cancelled orders,
zero fills, an expired/stopped session, no HALT and released ownership. The completed
30-minute observation keeps its original image identity and 40 log entries; no
new full load/soak or long Demo observation is claimed for the cancellation fix.

### P6 natural fills/fees and filled-position recovery — 2026-10-26

The retained `17d530e26018...` image, without source changes, produced five
natural Demo maker fills: three spot/two perpetual. A temporary private
40-USDT-per-market, single-level, 0.002% spread catalogue enabled observation;
the original private catalogue is restored. Independent authenticated fill
reads match all five trade IDs and original fee amounts/currencies, including
the BTC-denominated spot buy fee. No self-matching or synthetic fills were used.

A real service restart preserved SHORT 0.0002 BTC, session identity, anchors and
deadline; readiness and both-market reconciliation passed. A subsequent natural
sell leaves SHORT 0.0004 BTC. Current state is 320 orders/five fills, no pending
exchange orders, no HALT and released ownership. The bot is stopped; dedicated
PostgreSQL and the held Demo position remain. Follow-up service logs contain no
ERROR/WARNING entries. No new runtime code, image build or full regression was
needed. Evidence: [focused report](../okx-demo-acceptance.md) and
[portable JSON](../okx-demo-fill-acceptance.json).

Legacy scaled-six storage truncates the three spot quantities by
0.00000016–0.00000018 BTC. Existing reconciliation allows 0.000001 BTC, so its
matched result does not claim lossless quantity storage. Original fee JSON is
exact. This limitation remains explicit instead of silently changing financial
precision during acceptance.

Funding remains the only open Demo observation. A real smoke verified the
existing controller can stay ready with gross capacity fully consumed by the
held SHORT, admitting no new orders. A bounded local watcher is scheduled for
23:57 UTC–00:06 UTC around the 2026-10-26 00:00 UTC settlement (07:00 Vietnam).
It checks stopped/no-HALT/unchanged position and config before startup, compares
the real USDT funding bill with the recorder, then stops and restores config.
Absence/mismatch leaves the funding checkbox open. No funding payment has been
fabricated or marked passed in advance.

### Post-Demo PostgreSQL decimal precision fix — 2026-10-26

The owner authorized the correction before deployment. Red tests reproduced
truncated BTC quantities, conflicting duplicate-fill replay and a one-satoshi
quantity/price difference incorrectly reported as matched. The existing
`SqliteDecimal` adapter now uses unscaled PostgreSQL NUMERIC with Python Decimal;
its upstream SQLite format is unchanged. No new persistence framework or cache
was added. SQL `013_preserve_decimal_precision.sql` converts the 15 old BIGINT
fields once, without reconstructing lost digits. Daily reporting uses natural
units, and reconciliation compares quantities/prices exactly. Existing funding
and executor summary FLOAT columns are outside this scaled-decimal fix.

Validation passes: **1,271 regression tests/nine subtests**, **113 PostgreSQL
tests**, and three extended checks for migration replay, recorder partial-fill
replay/reporting and all-model round trips beyond 18 fractional digits. The
first full PG run exposed a diagnostic JIT-enabled EXPLAIN exceeding the runtime
2s budget; only that deliberate baseline measurement now has a separate 30s
budget. Actual application query budgets are unchanged.

Rebuilt image `sha256:1710edfec3fb317d8435fc7957046d7454c210aa6fd68c5f963db323d771a648`
is 1,364,481,469 bytes and its four runtime/migration hashes match the source.
All five applicable offline scenarios pass across combined/selected runs:
schema and spot (including 8-decimal partial fills and 18-decimal fees), V2 spot,
V2 perpetual and the nine-case LIFE matrix/recovery. Initial image smoke/V2
assertions still expected 13 scripts/scaled integer units; those fixtures were
updated to 14/natural units before the affected reruns. The initial combined
report remains marked failed; passing scenario evidence is linked separately.
No compatible-old-image hot rollback is claimed for this schema change.

A **60-second diagnostic load probe** with 100,000 historical fills passes at
20 reads/s, ~100 market updates/s and one fill/s: read p95 **120.52ms**, p99
**622.44ms**, no violations. Sixty exchange/recorder fills and positions match;
orders use at most three queries, fills/performance one, with no new N+1. Native
recorder p95 is **30.97ms** over 25 transactions (budget 100ms). Cold 100,000-fill
daily statistics p95 is 1,289.85ms/11 samples and six queries. These local numbers
overlapped test/image preparation; no full soak or Cloud Run performance claim
is made. [Portable evidence](../postgres-decimal-precision.json) includes source
hashes, individual offline reports, scope and probe metrics.

The existing Demo database and five historical fills are unchanged. Its stopped
container still uses the original `17d530e26018...` image so the scheduled
07:00 Vietnam funding check can finish against a consistent image/schema. The
precision image is for subsequent fresh runtime use; no new real Demo order or
Cloud Run deployment was submitted during this correction.

### Planned P7 — funding observation while idle and restart backfill

The owner requested a plan update for durable funding collection in the existing
service. [P7](../../docs/plans/LIFE_OKX_IMPLEMENTATION_PLAN_TDD.md#p7--durable-funding-observation-and-restart-backfill-tdd)
is added with all implementation/acceptance items unchecked. It covers retained
positions after stop/expiry/spot-only switching, catch-up even after a position
closes, late bills, exact original amounts/currencies, account/environment/bill-ID
deduplication, owner-fenced transactional checkpoints and explicit API coverage.
The design reuses the existing connector readers, recorder, bot state and SQL
runner; no separate watcher container or scheduler is planned. Verification is
Red/Green unit + real PG, internal-network E2E and a bounded local load probe,
followed by opt-in Demo settlement/backfill checks. The existing original-image
07:00 Demo watcher remains a separate observation and cannot close P7.

This update changes documentation only. No funding observer, backfill migration
or runtime behavior has been implemented by this planning change.

### P7 local implementation — storage, reader and service lifecycle

The new tests first reproduced missing durable bill identity, transactional
cursor behavior, exact daily totals and GET-only transport contracts. The initial local
implementation passed **1,285 regression tests/nine subtests** and **120
real-PostgreSQL tests**. It reuses existing connector auth/throttlers, the service
owner loop, bot-state JSON checkpoints and the decimal adapter. Native funding
event persistence/polling is suppressed only for service-managed PG perpetual
connectors; original file-mode behavior remains unchanged.

Implementation detail refined from the initial plan: add one narrow
`funding_bills` companion table instead of altering the upstream event table's
primary key/columns and requiring a SQLite migration. Script
`014_create_funding_bills.sql` has exact amounts, nullable historical rate, scoped
bill identity and a composite account/environment/time/pair index. Daily reports
prefer identified bills over overlapping legacy event groups and label legacy
history unverified; no old row or bill ID is fabricated. FE status/daily coverage
is explicit, and the earlier stale API metadata claiming trade scale six is
corrected to unscaled NUMERIC.

The reader uses only authenticated GETs, two bounded pages per batch, resumable
committed windows and a one-hour overlap. Older supported windows use the bills
archive reader under its existing SDK throttler pattern (5 requests/2s); history
outside the conservative 89-day window has an explicit gap. Idle/expired/HALT/
spot-only lifecycle tests show no strategy activation or mutation of send/HALT
intent. Actual advisory-backend loss and commit failure cannot advance the
checkpoint. Default cadence is 120s through centralized
`FUNDING_SYNC_INTERVAL_SECONDS`; FE polls do not trigger exchange requests.

Opt-in Demo GET preflight succeeds for account config, recent bills and archived
bills with zero payments returned. It made no trading command or database write
and does not certify actual settlement/backfill. Evidence is under
`build/funding/`; image E2E/load and final real-bill acceptance remain open.

### P7 image/local acceptance and Demo observer upgrade

Final source verification passes **1,286 regression tests/nine subtests** and
**121 full PostgreSQL tests**. A subsequently discovered integration defect was
reproduced Red against real PostgreSQL: background funding checkpoints polluted
the trading-reconciliation fingerprint. The fix excludes only `funding_sync`,
keeps HALT/other recovery changes significant, and passes **17 targeted PG tests**
including the new regression. Swagger check passes.

Image `f6af2bf561c6...` is **1,364,511,171 bytes** and its 15 checked runtime/SQL
hashes match source commit `7dee5ffe4`. All seven offline groups pass in one
combined run: schema, spot, V2 spot/perpetual, nine LIFE profile/market cases,
funding and compatible predecessor upgrade/rollback. The funding scenario
verifies six exact bills, two distinct bills sharing a timestamp, paginated
unrelated instruments, legacy overlap, stopped/expired observation, catch-up
after position closure, late bills, owner handover, API failure and DB loss.
No new trading mutation occurs after stop.

The final image's isolated **60-second diagnostic probe** passes with 100,000
historical orders/fills, a five-second funding interval, 20 completed reads/s,
~100 book updates/s and one fill/s. Read p95 is **262.63ms**, p99 **676.13ms**;
there are zero unexpected errors or budget violations. Independent ledgers match
60 fills, held positions and two exact funding bills. No new full soak, Cloud Run
benchmark/deployment or exchange load test is claimed. The earlier overlapping
probe failed latency/error budgets and remains recorded; isolated runs pass with
unchanged budgets. No additional cache is justified by this bounded workload.
[Portable evidence](../funding-backfill-evidence.json) retains image/source
identity, original artifacts, metrics and unsuccessful attempts.

The dedicated Demo service now uses this final P7 image with desired state
`stopped`. The old quote-based scheduled helper was retired. Startup auto-migrated
the retained DB; a private pre-migration pg_dump is saved, and normalized hashes
of all order/fill/position rows match before/after: **320 orders, five fills,
three persisted position records**. UTC and control version are retained. The
service's own read-only funding coverage is fresh, with no strategy command or
new quote. Its initial authenticated bill reads contain no actual settlement.
The local bounded acceptance helper will compare the 07:00 Vietnam settlement
with exact DB/daily totals, cold-restart the stopped service to verify replay,
and then stop/release ownership. Actual Demo funding items remain open until a
real bill passes those checks; a successful empty read is not payment evidence.

### P7 actual settlement acceptance

P7 funding settlement verified at 2026-10-26T00:01:32.620179+00:00: Demo bill 4042315866684412230 reports 0.0032045728 USDT at UTC epoch-ms 1792972800651. Exact funding_bills and daily API totals match. The same stopped service was cold-restarted and the bill was ingested again without duplication. Orders/fills remain 320/5; no strategy or exchange trading command was submitted. The service stopped and released ownership after observation.

[Portable evidence](../okx-demo-funding.json).

### Post-completion logic-flow review — 2026-10-26

Eight confirmed flow defects were reproduced Red and fixed using existing
service/control/controller/reconciliation/funding helpers. Added regression cases:
35 native and five real-PostgreSQL cases. Coverage includes partial-fill/cancel
exposure reservation, exact spread boundaries, superseded start/switch, CLI/MM/
legacy transitions, stopped bootstrap with missing old YAML, working-order terms,
funding retention coverage and configured cadence.

The image run additionally exposed cold MM-model loading inside the CLI control
row lock. A real-PG heartbeat regression fails before moving import outside the
transaction and passes afterward. The final consolidated suites pass **1,321
native tests/nine subtests** and **127 PostgreSQL tests**, with Swagger and scoped
hooks green. Existing deprecation warnings remain.

Final runtime source `ed98eaf18e`, image `sha256:92e9e94f1606e6aac623ba9a8e4e8e4ea15b30917f5d791cc3c7e759a62ac4fe` (1,364,516,628 bytes), matches
all 19 checked runtime/SQL hashes. Seven combined offline groups pass, including
nine MM cases, exact wire quotes and the real CLI-stop/legacy/new-MM handshake.
The isolated 60-second probe passes at 20 reads/s, ~100 updates/s and one fill/s:
p95 **164.07ms**, p99 **756.60ms**, with exact fill/funding/position ledgers.

The first V2 fixture kept the old float-rounded 98.99 expectation; it now verifies
the exact 99 order/fill/held-position price. Both failed image attempts remain
retained. No new full soak, cloud deployment or real Demo account operation was
performed; original Demo evidence keeps its own image. Conservative full-entry
reservation can temporarily reduce quoting until terminal hold. See the
[review](../life-mm-logic-review.md) and [portable evidence](../life-mm-logic-review-evidence.json).

### Incremental enhancements E0 — 2026-10-26

E0 isolates optional report/daily-checkpoint/reconciliation failures, retries only
classified read-only initialization, supervises retained trading tasks and exposes
sanitized incidents plus observer progress through the existing authenticated API.
Required ownership, recorder, command-receipt and uncertain-transport failures
retain their existing fail-closed behavior. No automatic HALT reset was added.

Red/Green tests also fix an observer-ack/early-clock gate regression and a queued
create checkpoint race found during rapid-switch probe setup. Optional daily
checkpoints now run once per minute or on identity/currency/day changes, with
backoff on invalid samples; live reporting/control cadence remains intact.

Verification: 1,361 native tests/nine subtests, a final nineteen-test recorder
recheck, 134 PostgreSQL tests and seven final-image offline E2E groups passed.
The sixty-second diagnostic probe passed unchanged budgets: p95 50.40ms, p99
232.05ms, 1,200 HTTP 200 responses and no unexpected errors; sixty fills, positions
and funding match the independent ledger. Final image:
`sha256:1791cdc64d2117f587cee15303d9d9b98a6c89321653af23b6042d776f087695`
(1,364,564,090 bytes); fourteen runtime/contract hashes match source.

Earlier failed probes, the setup race and the obsolete eleven-query benchmark
assertion remain documented. E1–E6 have not started; no new sixty-minute, previous
image upgrade, Cloud Run or real OKX Demo acceptance is claimed. See the
[E0 report](../life-mm-e0-availability.md) and [evidence](../life-mm-e0-availability.json).

### Incremental enhancements E1 — 2026-10-26

E1 adds opt-in strict market deviation and profile-specific manual sideways ranges
to existing controller/admission/transport paths. Known own quantities are bounded
and removed from book depth; partial/pending/terminal observations use original
quantity as a conservative upper bound. Recovery requires elapsed time and distinct
control-loop book samples. Repeated readiness checks cannot advance recovery.
Existing shared admission, cancellation, retained positions, HALT and expiry remain
authoritative. API guard observations use Decimal strings and UTC timestamps.

A non-unit-contract Red/Green test also corrects perpetual WS snapshots to use the
same existing base-quantity conversion as REST snapshots and diffs. No new service,
tracker, database schema, cache backend or environment variable was added. Guards
remain disabled by default; deployment needs explicit reviewed thresholds to enable.

Verification: 1,406 native tests/nine subtests, 99 overlapping focused cases with
RuntimeWarning treated as errors, 137 PostgreSQL tests and eight final-image E2E
groups passed. The guard matrix includes own-only depth, joint cancellation,
breakout/deviation, a real partial fill retaining spot inventory and a cold restart
that resets recovery delay while preserving anchors/deadline. The initial harness
null-reason predicate failure is retained and corrected.

Final image `sha256:52a7a27f8062f02f47a71140474e185bd2741e6bbeffe0729975547fc8cbe095`
(1,364,587,950 bytes) matches eighteen source/config/contract hashes. Guard-enabled
sixty-second probe passed unchanged budgets: p95 54.99ms / p99 437.33ms, 1,200 HTTP
200 responses, zero unexpected errors and sixty matching fills/positions. E2–E6
have not started; no new sixty-minute, upgrade, Cloud Run or real Demo gate is claimed.
See [E1 report](../life-mm-e1-quote-guards.md) and [evidence](../life-mm-e1-quote-guards.json).

### Incremental enhancements E2a — 2026-10-26

Optional catalogue drawdown limits now use the existing V2 reports, stable
controller scope, owner checkpoints and position-preserving service shutdown.
Retained disabled-market positions stay covered through existing public
non-trading feed subscriptions. Missing valuation waits and recovers; confirmed
breach retains durable HALT across commands/restarts. Policy and monotonic peaks
cannot be reset by random generations, new sessions, market switches or legacy
CLI selection. No new table, ledger, cache, environment variable or reset API was
added. Limits remain disabled by default. The declared metric includes covered
trade fees and excludes funding; it does not claim exact account equity.

Red/Green covers report scope/fees, finite thresholds, strict exceedance,
checkpoint persistence, risk-stop behavior and report versus decision freshness.
The first image run found a final-shutdown Decimal serialization defect after
retained-feed recovery; a failing native shutdown test now protects the DTO
serialization fix. This failure is retained with its original image/run identity.

Verification: 1,437 native tests/nine subtests, 73 overlapping focused cases with
RuntimeWarning treated as errors, 147 full-suite PostgreSQL tests and a final
eleven-case selection passed. The selection adds explicit random-generation
coverage beyond the ten cases in that full run. All nine final-image E2E groups
passed, including read-only retained-market valuation, partial-fill preservation,
clean restart, persistent risk HALT and rejected post-HALT admin start.

Final image `sha256:84f4d33ae3153a049aaddaa55532a58a446a00a6656629f456e8e46ec7922dd0`
(1,364,627,702 bytes) matches twenty-one runtime/config/contract hashes. The combined
guard/drawdown sixty-second probe passed unchanged budgets: p95 74.81ms / p99
518.06ms, 1,200 HTTP 200 responses, no unexpected errors and sixty matched
fills/positions. Observed RSS was 328,478,720 bytes on a 1 CPU/2 GiB bot.
E2b–E6 remain open; no new sixty-minute, upgrade, Cloud Run or real Demo gate is
claimed. See [E2a report](../life-mm-e2a-drawdown.md) and
[evidence](../life-mm-e2a-drawdown.json).

### Incremental enhancements E2b — 2026-10-26

Optional rolling spot net USDT outflow now uses committed recorder fills,
original fee currencies, bounded in-process projection and client-ID BUY
reservations on the existing pre-intent/final-transport gates. Partial fills do
not release capacity before commit. Read-only terminal proof uses existing
connector APIs/parser and recorder deduplication. Funded SELL/perpetual admission
and cancellation retain their ordinary checks. No new fill table, index, ledger,
Redis service, environment variable or reset endpoint was added; defaults are null.

The existing recovery state binds verified account/environment/pair and policy.
Unknown legacy origin waits a full observed window; restart/profile/random/market
changes cannot reset the allowance. Red/Green also covers upper-edge committed
debit protection, per-order dedupe bounds and a final startup race: setup authority
must survive async reconstruction, and a binding HALT cannot reopen order permission.

Final verification: 1452 native tests/nine subtests, 93 overlapping focused cases
with RuntimeWarning treated as errors and 161 full-suite PostgreSQL tests passed.
Ten E2E regression groups passed on the prior image. The final enabled-only owner
startup fix was reverified with the enabled outflow image case and combined load;
the two artifact identities remain separate in the evidence. Twenty-five checked
runtime/config/contract files match final image
`sha256:cad5a536266c58aa61dfad61afa04ff3df601e329af4dbe408f07f0bb5b62cff`
(1364671166 bytes).

The final outflow/drawdown/strict-guard sixty-second probe passed unchanged budgets:
p95 52.18ms / p99 327.89ms, 1200 HTTP 200 responses, zero unexpected errors and
sixty matching fills/positions. A 100001-current-fill rebuild used bounded queries;
1000 reservation/status checks added zero SQL. Recorded native rebuild/hot-path
times ranged 1.92–3.21 seconds / 19–34ms; 100000-fill burst expiry took 234.47ms.
The E6 1-CPU active-window/expiry and full-soak gate remains open. Fixture errors
and diagnostic prior-image measurements are retained. No Cloud Run deployment,
new sixty-minute/upgrade gate or real Demo operation is claimed. See
[E2b report](../life-mm-e2b-spot-outflow.md) and
[evidence](../life-mm-e2b-spot-outflow.json).


## E2c — Bounded listing bootstrap (complete)

Added explicit `bootstrap_then_market` through existing LIFE MM observation,
PositionExecutor, owner pre-intent/final-send and session admission paths. Original
anchor/band, first clock and one-way qualification persist in the existing recovery
row; session/clock activation is atomic. Restart/profile/random/new session cannot
renew it. Both markets require joint readiness. Existing drawdown, spot outflow,
exposure and post-only limits remain authoritative; no new schema, service or cache.

TDD caught missing-book executor reads, sparse qualification/depth-limit errors,
a full-book crossing projection and a position-hold valuation gap. The first image
case then exposed unfilled market-guarded report/shutdown failure after outside
liquidity loss; Red/Green tests fixed it, and the final image repeats the full case,
including clean stop with the service still alive. Qualification injects no fills.

Final native regression: 1483 tests/11 subtests; focused: 93 tests/2 subtests with
RuntimeWarning as errors. Full PG: 166; final bootstrap PG: 10 (171 unique cases
across runs). The final Linux amd64 image is 1364711274 bytes with 29 matching
packaged source/config/contract hashes. The enabled 60-second diagnostic with
100000 historical rows and 1 CPU/2 GiB passed p95 57.74ms / p99 296.04ms, 1200
HTTP 200 reads, zero unexpected errors and sixty matching fills/positions.

Checklist and evidence: [enhancements plan](../../docs/plans/LIFE_OKX_MM_ENHANCEMENTS_TDD_PLAN.md),
[E2c report](../life-mm-e2c-listing-bootstrap.md), [JSON evidence](../life-mm-e2c-listing-bootstrap.json).
Defaults remain disabled. E3–E6/full final image matrix/soak remain open. No external
OKX account or deployment was used.


## E3 — Existing PMM spot inventory skew (complete)

Verified 2026-10-27. A small validated adapter calls the original compiled PMM
interpolation directly; original legacy/generic math files remain unchanged.
LIFE derives range from its actual baseline level quantities, values account spot
cash at bounded outside mid and applies size multipliers/Decimal budget scaling.
Existing PositionExecutor, post-only execution, refresh/hold, quantization and
funding/exposure/outflow/owner gates remain authoritative. Perpetual is unchanged.
No service, task, schema, wallet, ledger, cache or environment flag was added.

Red/Green cases protect mandatory stale account readiness, own-only valuation
and malformed optional cash snapshots. Optional failures use neutral sizing and
recover in place; known stale/corrupt account data blocks new quotes without
inventing a process restart. Partial active allocation stays reserved and rejected
allocation is never redistributed. The existing authenticated status/Swagger
expose the account-cash signal and fallback.

Final native: 1530 tests/11 subtests; focused: 58 with RuntimeWarning as errors;
full real-PG: 171. Final image E2E passes spot/both and restart: underweight
BUY/SELL proposals ~75.74/24.26 become ~37.31/62.69 after two simulated BUY
fills, all within the 100-USDT proposal budget and using post-only orders.
The final combined bootstrap/drawdown/outflow/skew 60-second diagnostic on
1 CPU/2 GiB and 100000 historical rows passes 1200 reads, zero errors, p95 49.36ms /
p99 225.97ms and sixty matching fills/positions. Thirty packaged paths match source.

See [E3 report](../life-mm-e3-inventory-skew.md), [evidence](../life-mm-e3-inventory-skew.json)
and [plan](../../docs/plans/LIFE_OKX_MM_ENHANCEMENTS_TDD_PLAN.md). Defaults remain
disabled. E4–E6/full final matrix/soak remain open. No external account or deployment.


## E4 — Transition history and sampled liquidity (complete)

Verified 2026-10-27. Reused atomic control/session updates and status snapshots;
added one idempotent event table with deterministic dedupe and bounded authenticated
history/liquidity APIs. Global HALT events survive stopped controllers. Observations
exclude pending/market orders and preserve unknown gaps across restart/generation
changes. No polling exchange calls or new worker/cache.

Native: 1538 tests/11 subtests. Full real-PG: 182; final targeted history: 12.
Migration/schema, rollback/idempotency, filter indexes, constant query counts and
HTTP/UTC/no-exchange-read contracts pass. See [report](../life-mm-e4-history-liquidity.md)
and [plan](../../docs/plans/LIFE_OKX_MM_ENHANCEMENTS_TDD_PLAN.md). E5–E6 remain open.


## E5 — Optional fee and volatility spreads (complete)

Verified 2026-10-27. Reused connector maker fees/conversion, existing candles/NATR,
configured-entry executors and owner/REST gates. Disabled fixed-spread parity,
explicit fallback/wait, shared-feed cleanup, ordered/capped spreads and Decimal
rounding pass. Red/Green cases protect SELL tick widening, flat-fee amount rounding,
perpetual OPEN fee signature and prompt recovery from a disconnected candle tail.

1559 unique native cases/11 subtests and 185 unique PG cases across full/focused
runs. Final image `ee3b3b0b40fe` is 1364799073 bytes; 35 packaged paths match.
Both-market image E2E passes normal/shock/outage/recovery/stop; recovery 7.20s,
zero restarts. See [report](../life-mm-e5-spread-policy.md),
[evidence](../life-mm-e5-spread-policy.json) and [plan](../../docs/plans/LIFE_OKX_MM_ENHANCEMENTS_TDD_PLAN.md).
Full final matrix/load/soak and external Demo remain E6. No deployment performed.



## E6 — Final artifact, full load and Demo (complete)

Verified 2026-10-27. Image `sha256:739ad5692355965dad5b1ad0f32de17935cbfd1f26c8967626e0d5b4fd5fd191`
is 1364804595 bytes; all 51 packaged paths match. Native: 1567/11
subtests; real PG: 196; Swagger/hooks and fourteen image scenario groups pass.
The unchanged full all-feature untraced warmup/mixed/burst/sixty-minute random
soak passes with zero healthy errors, independent exact accounting and five
separate fault windows. Median soak RSS growth: 11.81 MiB.

Reused existing lifecycle JSON publication, outflow initialization, recorder/ORM
ownership fence and bulk side lookup. Red/Green corrections cover executor
batch atomicity and a real Demo cancellation-before-ACK race, retaining uncertain
intent/provisional-row safety. No new production cache/service/schema.

Real Demo on an isolated restored copy passes all nine guarded quote cases and
restart identity/deadline preservation. New natural fills (spot: 2, perpetual: 6) match
exchange amounts, prices and original-currency fees. The natural-fill phase
explicitly disables only the optional spot fee floor; the nine quote cases
keep it enabled. Original private files/backup remain unchanged. Test quotes
are canceled, service/copy PG stopped, positions/data preserved. No push/deploy.

Cold traced large-window admission latency failures remain an explicit residual;
the ordinary passing gate is untraced. See [E6 report](../life-mm-e6-verification.md),
[evidence](../life-mm-e6-verification.json) and
[completed plan](../../docs/plans/LIFE_OKX_MM_ENHANCEMENTS_TDD_PLAN.md).


## Explicit Demo configuration preset (complete)

Added a BTC-USDT spot/perpetual preset, dedicated environment example, thin Demo
image layer and `make build-cloudrun-demo`. Reuses the existing runtime and
canonical catalogue path; no trading engine or runtime recovery logic changes.
Guard, drawdown/outflow, fee/NATR and spot inventory skew are explicit. The guide
explains enable switches, availability, reason-based recovery and durable HALT.
Legacy image E2E fixtures now explicitly disable admin routes before scenarios
that enable them, so image defaults cannot leak into legacy cases.

Nine profile/market selections validate on host and in the read-only,
network-disabled amd64 Demo container. Native config/packaging regression: 26.
Existing ABI/JIT/TA-Lib/TLS/SDK/SQL runtime smoke passes. Runtime layers are
inherited; the packaged catalogue hash matches source. The missing local base
image was rebuilt, so these new image IDs have configuration/runtime smoke
evidence, not the previous E6 artifact's full release certification. No connected
OKX request, trading session or deployment was started.

See [guide](../../docs/okx-operations.md#demo-preset) and
[sanitized validation](../okx-demo-preset-validation.json).


## Runtime flow optimization (complete)

Verified 2026-10-27. Reused the existing SQLAlchemy pool, reporting queries,
state store and rolling outflow projection. Four runtime commits retain the
five-connection ceiling while reusing readers, remove redundant fee JSONB/day
conversion, narrow fresh publication/owner reads and drain wholly expired heaps
in the same Decimal order. No new cache backend, service or schema migration.

Controlled native measurements: daily median 498.29 to 375.14 ms (about 25%),
daily p95 549.71 to 440.94 ms (about 20%); 50000-fill expiry CPU 177.74 to
143.94 ms (about 19%). Projection construction is about 5% slower; the detailed
report retains that trade-off and the rejected slower day-join experiment.

Image `c89ce9122a39`, 1366522531 bytes: 53 packaged paths match. Native: 1571
tests/11 subtests; real PG on final runtime: 204; Swagger/hooks and fourteen
E2E scenario groups pass. All four healthy windows retain the original budgets:
97800 primary plus 9780 separate auxiliary reads, zero unexpected errors.
Sixty-minute soak p95/p99: 84.25/461.65 ms; median RSS growth 10.07 MiB.
4884 actual fills match the recorder and independent fees/positions/funding.

The first combined load root remains failed: after four fault windows passed,
preparing the fifth timed out because the simulator public-feed loop could die
on a closing transport. Two Red/Green book/candle regressions and fixture commit
`9d6538343` protect that race without changing the production image. A separate
five-fault retest on the same image, with real simulated spot/perpetual fills and
retained positions, passes all five windows. This combined evidence is not a
passing monolithic rerun; both original and supplemental scopes are preserved.

Runtime commits: `d6fac719b`, `ce22de4e3`, `7594b1784`, `6aae523fc`.
See [optimization report](../life-mm-runtime-optimization.md) and
[portable evidence](../life-mm-runtime-optimization.json). All work is local;
historical E6 Demo and cold traced stress limits retain their original scope.


## Maintainability refactor (complete)

Work proceeds in small behavior-preserving commits on the existing runtime.

- [x] Share the 22 MM readiness reasons between controller and service.
- [x] Move persisted reporting views into a neutral core module; retain the old import for compatibility.
- [x] Improve command diagnostics and persist partial fault results.
- [x] Expose the all-feature load configuration through Make.
- [x] Replace cross-layer controller state mutation with explicit methods.
- [x] Extract snapshot and funding orchestration from ServiceHost without changing ownership/control ordering.

Shared reasons/reporting projection: 83 focused controller, service and reporting
tests pass. No response schema, freshness window or submission check changes.

Diagnostics: four Red cases reproduce terminal-command timeouts, missing
readiness detail and loss of a completed fault when a later case fails. All 18
focused diagnostics/exchange/budget/ledger tests pass after the change. Make
dry-runs confirm the explicit enhancement target adds the flag while the legacy
target preserves its configuration. No load rates, durations or budgets change.

Controller interface: 86 controller/session/drawdown cases pass. New Red/Green
tests protect idempotent revocation, generation fencing, copied shared baskets
and incomplete-anchor refusal. The host still persists joint anchors before
admission. Ordinary V2 controllers without the optional managed-session hook
retain their existing strategy stop path.

Service extraction: 50 focused service/funding/lifecycle tests pass. Snapshot
observations and funding task state have explicit in-process owners;
ServiceHost drops from 961 to 833 lines while keeping its control observer and
owner/version checks in place. Funding continues to reuse FundingSync. Shutdown
cancels funding before disposing the engine, and a new single-flight test checks
that a poll deadline cannot create overlapping batches. Trace instrumentation
follows the moved collector while preserving its existing span name.

Final verification, 2026-10-27: 1578 native tests/11 subtests, 204 real-PG tests,
Swagger and hooks pass. Image `b720c28a226c` is 1366524884 bytes; all 57 checked
runtime paths match the frozen source. `hummingbot-cloudrun:local` and
`hummingbot-cloudrun:refactored` are aliases for that same verified image.

Thirteen functional E2E groups pass, including nine profile/market combinations.
The combined matrix stopped before upgrade because its historical comparison
image disappeared from Docker's cache. That failed root is retained. Upgrade,
takeover and rollback then pass separately against a reconstructed fixture whose
53 Python/SQL source paths match baseline `917800b59`. The fixture inherits the
current dependency/runtime layers; this is not certification of the deleted
historical binary or a passing monolithic matrix rerun.

The all-feature untraced 150-second diagnostic uses 100000 historical orders and
fills, completes 3000 primary plus 300 separate auxiliary reads with zero
unexpected errors, and records p95/p99 55.22/289.74 ms. The active outflow history
falls from 50000 entries to 75 after expiry. All 150 actual fills, original fees,
positions and two funding bills match the independent ledger. All five fault
windows pass on the same image with retained spot/perpetual positions. During
that run, the root JSON already contained two completed fault cases while its
overall pass flag was still unset, confirming incremental evidence persistence.

A separate 30-second traced diagnostic on 1000 historical rows passes and retains
two slow `service.collect_funding` spans from the moved collector. These short
checks do not replace a new sixty-minute soak or certify cold large-window tracing.
The earlier optimized image's long-soak results retain their original scope.
No connected OKX account, Cloud Run deployment, schema change, cache backend or
new service is involved.

Implementation commits: `09dc7198d`, `1c405ba57`, `7400c296d`, `37cad9c0a`.
See [portable verification evidence](../life-mm-maintainability.json).

## Cloud Run logging severity (2026-10-27)

- [x] Add JSON stdout logging with UTC timestamps, bot/config/owner/revision and current session context.
- [x] Expose `LOG_FORMAT=json|text` and minimum `LOG_LEVEL=DEBUG|INFO|WARNING|ERROR|CRITICAL`; default JSON/INFO.
- [x] Promote service NETWORK calls before admission filtering, retaining original custom levels for legacy YAML/file logging.
- [x] Match operator incidents to ERROR; retain per-scope retry rate limiting and log severity transitions immediately.
- [x] Redact known configured secret values and encoded/decoded DSN passwords; preserve sanitized outer failure handling.
- [x] Verify console bootstrap, thresholds, custom levels, current owner/session context, redaction and early startup failures.

Eleven initial behavioral Red cases failed against the old text formatter,
missing environment thresholds, suppressed NETWORK records and incident severity
mismatch. Fifteen final tests pass, including additional threshold-bypass,
legacy-mode and subprocess startup-failure cases. The startup probes deliberately
reject configuration before database ownership or exchange I/O. Output remains
JSON ERROR/INFO even when log settings or the database URL are invalid; raw
configuration inputs are absent.

Full `make test-cloudrun-regression` passes 1593 tests plus 11 subtests
(158 + 11 + 1009 + 415). Swagger validation and all applicable pre-commit hooks
pass. Raw local output is under ignored `build/service-logging/`. No new logging
dependency, background service, database migration or API schema is introduced.

This verification covers source/native execution, not a rebuilt container,
Cloud Logging ingestion, a new load/soak run or a Cloud Run deployment. Existing
image tags and historical load evidence still refer to their previous source.
Build and verify a new image before deployment. HTTP request trace propagation
is not included in this change. See [the logging operations guide](../../docs/cloudrun.md#logs-and-severity).

### Image logging follow-up

The first rebuilt candidate, `bcd946aa3398`, matches all 61 frozen runtime paths
and passes its packaged ABI/logging smoke, but actual container log inspection
rejects it. Resetting all named logger levels removed SQLAlchemy's protective
WARNING threshold, enabling verbose SQL/row parameter logging at INFO. Pydantic
serializer warnings also bypassed logging and produced multiline stderr text.
The offline matrix was interrupted after preserving the affected logs; this is
not a passing full-image acceptance run.

Three Red cases reproduce SQL output at INFO/DEBUG and warnings bypassing JSON.
The fixes retain SQLAlchemy WARNING and route Python warnings through logging;
legacy YAML logging restores its original warning handling. All 18 targeted
logging tests pass. The reusable image smoke now also checks real SQL execution
without emitted parameters and a multiline, redacted Python warning. Rebuilt
image verification is summarized below. Full native regression after
these fixes passes 1596 tests plus 11 subtests (158 + 11 + 1012 + 415).

- [x] Rebuild and verify the corrected image against all 61 frozen runtime paths.
- [x] Extend the reusable image smoke to cover severity, warnings and quiet SQL behavior.
- [x] Pass all 13 functional offline groups on the corrected image.
- [x] Inspect actual service logs for JSON, UTC/severity, incident consistency and configured dummy credential leakage.
- [x] Preserve rejected-candidate evidence and remove the disposable test containers/rejected image.

Final image `df5d240f42c5` is Linux amd64, 1366580877 bytes. The matrix includes
nine profile/market selections and passes on the same image as its packaged
logging smoke. Inspection of 2531 records from 36 service log files finds no
non-JSON lines, verbose SQL records, incident severity mismatches or configured
dummy credential values. WARNING/ERROR records from intentional fault scenarios
remain visible. All test containers are cleaned up.

No new upgrade/rollback, load/soak, OKX account or Cloud Run run is claimed.
The historical comparison image is absent, so `release_scope_complete` remains
false. See [verification report](../cloudrun-logging-verification.md) and its
[portable evidence](../cloudrun-logging-verification.json).

## Pre-listing economic stress replay (2026-10-27)

- [x] Add conservative exogenous flow/queue/latency rules and exact economic ledger checks with Red/Green tests.
- [x] Reuse the real controller/catalogue, maker pricing, spread/inventory/guard/bootstrap helpers, outflow projection and upstream HEDGE holds.
- [x] Provide YAML assumptions, paired scenarios, CLI/Make targets and incremental retained artifacts.
- [x] Complete all 648 cases natively and in the packaged Linux runtime; compare outcome fields and provenance.
- [x] Publish costs, hold-relative marked PnL, liquidity, censored markouts and risk outcomes with explicit model limitations.

The final compared metrics, liquidity, market contributions, markouts, reasons,
tape hashes and effective configuration hashes match for all 648 cases. There
are twelve scenario/seed groups and eleven distinct price/flow tapes, not 648
independent market samples; the two empty/no-flow tapes have identical content.
17 focused economic tests, 1613 native tests plus eleven subtests, Swagger/hooks,
runtime ABI/logging smoke and targeted V2-perpetual real-PG E2E pass. Image
`54e2a1408e1e` is 1366591963 bytes; 61 production/runtime paths are unchanged and
match source. Economic test drivers are explicitly mounted; no production policy
or deployed credential/configuration changes are made.

The first matrix failed after 432 cases because the experiment assumed a
continuous rolling-outflow ceiling rather than the existing BUY-admission
contract. A targeted reproduction and regression now retain above-cap window
observations while checking every admitted BUY. Seven final cases observe such
transitions. Fifty-eight cases have negative fixed initial-margin headroom;
liquidation is not modeled and those results remain capacity warnings. An
initial image-tag lookup and a stopped-tmpfs export also failed; the final
unchanged Linux matrix writes directly to persisted host evidence. All failures
and their scope remain recorded, and test containers are removed.

No profitable/optimal strategy, new load/soak/full release matrix/upgrade,
real OKX trading or Cloud Run run is claimed. See [results](../life-mm-economic-stress.md),
[portable evidence](../life-mm-economic-stress-evidence.json) and
[the completed plan](../../docs/plans/LIFE_MM_ECONOMIC_STRESS_PLAN.md).

## Operations API and configuration ownership (2026-10-27)

- [x] Reject profile-setting overrides on new commands; preserve legacy saved sessions/receipts.
- [x] Add English operator/error guidance, request IDs, process/owner identity, freshness and pinned/deployed configuration provenance.
- [x] Make Swagger development-only with the requested `ENV=dev` default and explicit production template setting.
- [x] Add bounded command/session history, stable check lookup and constant-query recorded fill progress.
- [x] Document API/YAML/environment ownership, restart/rollout behavior and low-technical-literacy FE workflows.
- [x] Verify 1,634 native tests + 11 subtests, 210 PG tests, current-image LIFE MM matrix and a 60-second 100k-history polling diagnostic.

Image `a2d823c5a2c9` matches 18 changed runtime/schema files. The diagnostic completed
1,500 reads with no errors/violations (original read p95/p99 69.94/301.42 ms).
An obsolete 16-script packaging assertion was corrected to 17 before the same
image passed. This is scoped backend/image verification, not a new full release
or hour-soak result. FE/browser integration and Cloud Run access routing are still
external tasks. See [report](../admin-operations-api-implementation.md),
[evidence](../admin-operations-api-evidence.json) and
[completed plan](../../docs/plans/ADMIN_OPERATIONS_API_TDD_PLAN.md).

## Working-order activity and diagnostics (2026-10-28)

- [x] Record existing proposal rejection reasons per BUY/SELL without re-running exchange checks or changing order decisions.
- [x] Expose both-sided, one-sided, absent, idle and unavailable working-order states; scope fill observations to current working orders.
- [x] Add a deployment-only 120-second continuous missing-order warning, disabled with 0; do not count gaps/restart/generation changes.
- [x] Publish through existing status/liquidity reads and Swagger; use plain English working-order labels and WARNING/INFO log events.
- [x] Verify 1,661 native tests + 11 subtests, 42 final focused tests and 18 disposable PostgreSQL tests; retain one query per status/liquidity request and no control mutation.

Source changes are committed separately from the existing packaged-image results.
No new image/load/soak, real exchange, FE browser or cloud verification is claimed.
See [report](../mm-quote-activity.md), [evidence](../mm-quote-activity-evidence.json)
and [completed plan](../../docs/plans/MM_QUOTE_ACTIVITY_TDD_PLAN.md).

## Until-stopped sessions (2026-10-28)

- [x] Add explicit timed/until-stopped session selection, gated by read-only YAML permission; default remains timed.
- [x] Preserve finite random windows, skip elapsed windows on restart and keep fixed continuous anchors; reject malformed timed authority.
- [x] Keep Stop/Switch, ownership, HALT, risk and finite bootstrap/REST limits effective, with real-PG Red/Green coverage.
- [x] Fix random-boundary cancellation of startup reconciliation/controller initialization, discovered by actual image restart.
- [x] Publish English Swagger capabilities and FE/configuration ownership guidance; no API override of YAML.
- [x] Pass 1,673 native tests + 11 subtests, 215 PG tests, separate timed/continuous image E2Es and a scoped 60-second diagnostic load probe.

See [report](../mm-until-stopped.md), [evidence](../mm-until-stopped-evidence.json)
and [completed plan](../../docs/plans/MM_UNTIL_STOPPED_TDD_PLAN.md). This work does
not claim a new full release soak, real exchange, cloud deployment or FE browser verification.

## Long-running memory retention (2026-10-28)

- [x] Red/Green waiter cleanup and opt-in recent fill diagnostics with legacy balance safeguards.
- [x] Remove unused PG/OKX reconstruction history and retire persisted executor/order identities without losing aggregates.
- [x] Preserve executor state on persistence failure; keep a separate dirty position checkpoint signal.
- [x] Fix Stop during read-only startup without weakening uncertain-cancel or existing-HALT recovery.
- [x] Pass 1,691 native tests + 11 subtests and 219 PostgreSQL tests; verify the same image in a 10-minute churn/UI/fill diagnostic.
- [x] Record 15k reads with zero unexpected HTTP errors, 599/599 fills, matched positions and collectible retired strategies/providers.

See [report](../mm-memory-retention-review.md), [evidence](../mm-memory-retention-evidence.json)
and [completed checklist](../../docs/plans/MM_MEMORY_RETENTION_REVIEW.md). This is
scoped memory evidence, not a new release-hour/multi-day production guarantee.
Early private-stream contract-size initialization errors were recorded as a
startup follow-up; the later section below records their resolution. The old
HTTP-error result did not hide those runtime logs.

## OKX private-position startup ordering (2026-10-28)

- [x] Reproduce cold WS metadata ordering and verify warm reconnect, cancellation/retry, REST fencing and invalid sizes with TDD.
- [x] Reuse existing lazy instrument lookup/lock and publish symbols with validated contract sizes; preserve readiness, exact units and error handling.
- [x] Pass 1,703 native tests + 11 subtests, 219 PG tests and a 300-second current-image lifecycle/load diagnostic.
- [x] Verify zero contract-size/structured ERROR logs, 7,500 completed reads, 298/298 matching fills and correct positions through Stop/Start/Switch.
- [x] Close the earlier memory-review startup follow-up while retaining its historical four errors and original image evidence.

See [report](../okx-ws-position-startup.md), [evidence](../okx-ws-position-startup-evidence.json)
and [completed plan](../../docs/plans/OKX_WS_POSITION_STARTUP_TDD_PLAN.md). No real
OKX, cloud deployment, hour-long release gate or multi-day uptime claim is added.

## Weighted levels and volatility defense (2026-10-28; complete)

- [x] Characterize upstream weighted levels and preserve disabled spread behavior.
- [x] Implement reduced sizing, raw-NATR pause/recovery, per-level send checks and durable account-bound pause latches through existing components.
- [x] Cover cold/profile hysteresis, Stop racing first persistence, random-step recovery, weak controller retention and single stable-tick composition with TDD.
- [x] Publish English typed operations/status, Swagger and YAML-only policy guidance.
- [x] Pass 1,800 native tests + 11 subtests, 237 PostgreSQL tests and a declared 72-case synthetic comparison after the fallback/own-depth availability fixes.
- [x] Fix mutable fee-payload retention and reduce the rolling outflow GC graph with exact immutable values; pass 1,805 native tests + 11 subtests, 237 PostgreSQL tests, 72 scoped replays and all 16 current-image E2E groups.
- [x] Reproduce API pool exhaustion with a real-PG Red test; cap API DB work at two workers while retaining request context, cancellation bounds and cleanup (`174fee57b`).
- [x] Preserve 500-row HTTP history contracts while using Pydantic JSON directly; isolated one-CPU Linux encoding improves orders 4.780 → 3.097 ms and fills 5.976 → 3.557 ms (`dafe9387b`).
- [x] Add operator-requested `DB_POOL_SIZE` (default 5, range 5–50, restart required), with 13 Red/Green configuration cases (`f52f6ee3f`); retain API workers 2 and zero overflow.
- [x] Repeat final-source regression: 1,829 native tests + 11 subtests, 243 PostgreSQL tests and Swagger pass.
- [x] Verify all 26 selected files in image `c924030a…`; pass all 16 E2E groups, upgrade/rollback and defense log audit in one invocation.
- [x] Pass current-image unchanged warmup/mixed/burst/hour and six post-hour faults: hour 90k reads, zero errors, p99 459.19 ms, median RSS growth 11.73 MiB, matched 4,884 fills/positions. Keep failed earlier images/runs as history.
- [x] Pass bounded BTC spot/perpetual OKX Demo: twelve independently confirmed weighted post-only quotes, both real 1m feeds advance, Stop retains positions and removes quotes; container stopped, persistent DB kept.
- [x] Retain the first Demo startup-observer failure and its focused Red/Green correction; runtime/image/load scope is unchanged.

See [current report](../mm-volatility-defense.md) and
[ordered checklist](../../docs/plans/MM_VOLATILITY_DEFENSE_TDD_PLAN.md). The runtime
and final V5 acceptance are complete on `f52f6ee3f` / `c924030a…`. Earlier
images and failed observer attempts retain their original scope; no Cloud Run
deployment, live LIFE or multi-day uptime claim is added.

## Service availability audit remediation (2026-10-29; complete)

- [x] Fix all five findings with Red/Green coverage: shutdown send fencing, optional SQL isolation, established DB I/O deadlines, bounded priority API admission and required action-consumer supervision.
- [x] Preserve the ten-retry burst with 32 total / 12 reporting admissions, two workers and pool size five.
- [x] Pass 1,857 native tests plus 11 subtests, 257 PostgreSQL tests and Swagger; verify 29 selected runtime files in immutable image `be3efe46…` on `04cca6caf`.
- [x] Pass all 16 image E2E groups in one invocation, including predecessor takeover/rollback; classify all 55 induced structured ERROR records.
- [x] Add six Red / ten focused Green harness-continuity cases after the actual 02:35 clamshell interruption; retain its rejected 966.889s gap.
- [x] Complete the unchanged full load profile and six post-hour faults in `build/mm-load/20261029-051533-6462ca`: 90k hour reads, zero healthy errors, p99 674.21 ms, median RSS growth 9.46 MiB and 4,885 matching fills/positions.
- [x] Verify 58 continuous runtime samples (maximum gap 65.70s), unchanged thresholds and final runtime/harness hashes; clean test containers and retain the persistent Demo DB.

The first lid-open retry failed mixed p99 at 1,013.10 ms. Controlled traced and
untraced short diagnostics passed without reproducing that failure; its root
cause remains unconfirmed. No runtime/threshold change was made to obtain the
successful full repeat. Restart/DB fault HTTP failures are retained separately
from the zero-error healthy windows. This work adds no real exchange, Cloud Run
or multi-day uptime claim.

See the canonical [remediation checklist](../life-mm-availability-remediation.md)
and [portable evidence](../life-mm-availability-remediation-evidence.json), which
retain both earlier failed/interrupted attempts and the final accepted run.

## Cloud Run launch preparation (2026-10-29; local review complete)

- [x] Review deployment/template/runtime ownership and distinguish clean stopped
  startup from restoration of existing running intent.
- [x] Add explicit exchange/admin settings and pinned admin/perpetual secrets to
  the operator-filled service template; strengthen offline review with TDD.
- [x] Require an explicit cloud project in Make; prove missing project blocks and
  selected project forwards using a local gcloud argument-capture fixture.
- [x] Publish the first-deployment worksheet/checklist and FE gateway IAM/Basic
  Auth contract in existing guides; pass 55 focused tests and Swagger check.
- [ ] Fill actual operator project/region/DB/registry/secrets/access-route values;
  verify cloud stopped startup and FE browser access after reviewed deployment.

See [readiness review](../cloudrun-readiness-review.md). No cloud provisioning,
push/deployment, new image/load claim or trading occurred in this step.

## Local Demo API preparation (2026-10-29; stopped startup verified)

- [x] Reuse matching private Demo catalogue/environment and persistent DB after
  checking stopped/acknowledged intent and no HALT; no private config was changed.
- [x] Rebuild the runtime after operator Docker cleanup removed the accepted
  image; match 29 runtime hashes and pass the existing isolated ABI/logging smoke.
- [x] Run one local bot service at 1 CPU / 2 GiB, pool five, dev Swagger and
  localhost-only HTTP alongside the existing PostgreSQL container.
- [x] Verify stopped owner, health, auth and docs/profiles/schema; no trading POST.

See [local startup report](../local-demo-startup.md) and
[evidence](../local-demo-startup-evidence.json). Rebuilt image `143ddfe2…` has only
this local startup/smoke scope; the earlier release-hour result remains attached
to `be3efe46…`. FE browser acceptance and listing/cloud checks remain pending.

## Post-acceptance p99 investigation (2026-10-29)

- [x] Run matched 300s/900s traced and untraced diagnostics on rebuilt image
  `143ddfe2…`; reproduce mixed p99 failures at 1,817.24 / 2,044.06 ms.
- [x] Confirm one untraced warmup daily-statistics 503 is a real 2s PostgreSQL
  fee-aggregate statement timeout; retain the service's continued running state.
- [x] Correlate stalls, request/client backlog, transient quota pressure and GC;
  measure missing synchronous stages in a separate 600s test-wrapper probe.
- [x] Run isolated CPU replays and the existing real-PG 100k-fill benchmark
  (one selected test passes, 256 deselected); no full regression claim.
- [x] Pass the matched production-entrypoint 300s/900s control without the load
  wrapper: mixed p99 659.99 ms, zero errors, 1,200 matching fills/positions.
- [ ] Uniquely attribute the original historical spike or establish repeatable
  final-image healthy SLOs; one control cannot isolate instrumentation/GIL/host
  scheduling/emulation. No runtime fix or new release gate is claimed.

See [investigation](../mm-p99-investigation.md) and
[evidence](../mm-p99-investigation-evidence.json). The prior accepted gate keeps
its image scope. The real Demo remains stopped; all stress trading used the
isolated dummy exchange. No cache, pool, deadline or trading policy was changed.

## Reporting latency remediation (2026-10-29; R1)

- [x] Compare exact-fee SQL on 100k/1M repeated/diverse payloads, including a
  2 CPU / 512 MiB paired comparison; all result rows Decimal-equal.
- [x] Reject grouped payloads, text-rescan and filtered/derived-net changes after
  inconsistent gains or regressions. No runtime SQL change retained.
- [ ] R2 bounded, explicitly stale daily-only fallback and Red/Green tests.
- [ ] R3 submit-to-worker-start diagnostics, final-image regression/load controls.

See [report](../mm-reporting-latency.md), [evidence](../mm-reporting-latency-evidence.json)
and [plan](../../docs/plans/MM_REPORTING_LATENCY_TDD_PLAN.md). The confirmed 2s
reporting timeout and original historical p99 attribution are not declared fixed.

## Single-service architecture experiment (2026-10-29; R1A)

- [x] Keep one service/container/image and compare combined versus API/engine
  subprocesses at equal 1 CPU/2 GiB and 2 CPU/2 GiB, total pool slots five.
- [x] Reuse the existing API/store/command queue/engine; API child receives no
  exchange keys or ownership. Keep profiles and risk/recorder behavior unchanged.
- [x] Controlled 2.5s engine-loop stall: API p99 about 2.6s combined versus
  97–102ms split, 39+ admission failures versus zero in the paired cases.
- [x] Verify critical engine death exits the container; restart from acknowledged
  Stop, kill API during a new active session, cancel quotes, preserve positions
  and release ownership. Final fault exits 1.55s / 1.75s.
- [x] Retain longer healthy failures: 1 CPU p99 1875/1171ms; 2 CPU 2243/2555ms.
  Short 30s runs pass but do not replace mixed/hour acceptance.
- [x] Promote a reproducible diagnostic runner/Make target with nonzero healthy
  failure exit, excluding prototypes and runner from the deployment artifact.
- [ ] Adopt production process isolation: not justified as a blanket p99 fix;
  lifecycle, API-restart policy, supported pool/port handling and full gates remain.

See [experiment](../mm-process-isolation-experiment.md) and
[evidence](../mm-process-isolation-evidence.json). Existing priority-test scheduling
assumption corrected; no production executor behavior changed. Real Demo remains
stopped. R2 daily fallback / R3 queue diagnostics remain open in the reporting plan.

## Bounded daily fallback (2026-10-29; R2)

- [x] Red: missing last-good/coalescing behavior, two failures / ten passes.
- [x] Green: bounded 32-key daily cache, original observation age under 60s,
  canonical filters/day rollover, clock invalidity, cancellation and recovery.
- [x] Preserve explicit stale/retry/English metadata; permanent errors and all
  trading/current-account/control paths are excluded. TTL zero disables reuse.
- [x] Preserve healthy load strictness: HTTP 200 stale counts as an error.
- [x] 53 focused tests; 700 CLI/admin-config tests; 22 real-PG reporting/funding
  tests including actual timeout/recovery/schema errors; Swagger generation/check.
- [ ] R3 worker-start queue diagnostics and final-image E2E/load verification.

See [report](../mm-reporting-latency.md) and [evidence](../mm-reporting-latency-evidence.json).
SQL cost and historical p99 attribution remain open. No new production process
architecture adopted; real Demo remains stopped on its existing image.

## API worker timing (2026-10-29; R3a)

- [x] Red: three missing worker-start instrumentation failures.
- [x] Green: eighteen timing/trace/executor tests, covering copied context,
  physical queue wait, bounded samples, cancellation, errors and control dispatch.
- [x] Add one fixed test-only metric/trace event; production capacities and
  scheduler unchanged. SQL pool wait remains separately measured.
- [ ] R3b live profiling; candidate image and unchanged final-image gates.

See [report](../mm-reporting-latency.md) and [evidence](../mm-reporting-latency-evidence.json).

## Candidate verification and queue diagnostic (2026-10-29; R3b)

- [x] Native full regression: 1,938 tests plus eleven subtests and Swagger check.
- [x] PostgreSQL full regression: 259 tests; no relaxed runtime deadlines.
- [x] Build candidate `90490bab…` at 1,367,400,250 bytes; runtime ABI/JIT/TLS/SDK/
  logging smoke passes. The persistent Demo remains stopped on its existing image.
- [x] 180s all-feature traced probe: p99 541.95ms, zero errors, workload and
  independent ledger invariants pass. Worker/pool/loop metrics now distinguish
  different queues; individual >1s requests still exist and original attribution
  remains unassigned. No pool expansion or scheduler change.
- [ ] Full-image E2E, unchanged mixed/burst/hour/fault gates and production control.

See [report](../mm-reporting-latency.md) and [evidence](../mm-reporting-latency-evidence.json).

- [x] Candidate image full E2E: sixteen groups, including upgrade/rollback from
  `143ddfe2…`. Manifest `11dfdc61…`; packaged critical source hashes and runtime
  package manifest verified. Long performance gates remain pending.

- [x] Matched candidate production-entrypoint control: 300s warmup p99 574.80ms,
  900s mixed p99 465.60ms, zero errors and matching ledger/positions. No
  instrumentation or statistical speedup claim. Full all-profile gate running.

## Cache expiry race follow-up (2026-10-29)

- [x] Confirm an R2-introduced race, not the historical p99 cause: real cachetools
  lookup succeeds, TTL expires, direct deletion raises KeyError/API 500.
- [x] Two Red tests / 55 focused Green tests; safe pop treats expiry as a miss.
- [x] Stop pre-fix all-profile gate before soak; retain warmup pass and an
  explicit superseded annotation. No incomplete window is counted as acceptance.
- [ ] Rebuild/reverify the corrected final artifact; previous 90490bab results
  remain image-specific. Real Demo stays stopped and unchanged.

- [x] Corrected image `58356746…`: 1,940 native tests plus eleven subtests,
  Swagger check, 259 PostgreSQL tests, runtime smoke and packaged-source checks
  pass. Runtime package manifest unchanged.
- [ ] Corrected artifact E2E/control/full all-profile load; no historical-image
  result substitutes for this verification.

## Single-service supervision follow-up (2026-10-29)

- [x] Clarify priority: additional RAM acceptable; healthy latency, bounded
  retention and process recovery drive the architecture decision.
- [x] Red/Green for independent restart/reaping/backoff/watchdog/shutdown,
  including stop arriving during launch; 28 selected tests pass.
- [x] Real API/engine crash and hang faults pass twice, retaining owner/session
  and positions, preserving HALT without extra orders after engine death.
- [x] Record failed 300s/900s healthy windows rather than accept a 30s pass.
  Healthy p99 1754.99ms; memory samples show no sustained growth in this window.
- [ ] Trace the intermittent latency; production adoption and the hour-long
  memory/final-image gates remain open. Persistent Demo remains stopped.

See [process experiment](../mm-process-isolation-experiment.md).

## Process-trace measurement follow-up (2026-10-29)

- [x] 300s first trace p99 433.70ms, zero errors; preceding untraced warm/mixed
  failures retained. Partial API-GC overlap does not prove the historical cause.
- [x] Red/Green: heartbeat could advance between threshold and serialization,
  causing invalid negative lag. Snapshot once; 28 selected tests pass.
- [x] Corrected 300s trace p99 358.09ms, zero errors, seven valid initial API
  loop samples; all recovery fault checks pass again. No GC/pool policy change.
- [ ] Production promotion, stable healthy latency, continuous-hour memory and
  final architecture/image gates remain open. RAM is not the rejection reason.

No auto-reset HALT or operator Resume endpoint is implemented. Persistent Demo
remains stopped on its original image; no Cloud Run deployment.

## Longer request-correlated diagnostic (2026-10-29)

- [x] Red/Green for separate traced warmup/healthy evidence, real HTTP/worker/SQL
  correlation, safe parameter metadata and callback/worker cleanup. Commit
  `989c640e2`; subsequent analyzer/policy comparison at `8d29cf951`.
- [x] 35 selected regression tests pass. Reporting cache and production runtime
  behavior are unchanged by these test-only probes.
- [x] Complete matched 300s/900s repeat: p99 1861.62/2266.57ms, zero errors;
  preserve both latency failures. Selected requests distinguish client backlog,
  expensive active aggregates and worker waiting from small pool queues.
- [x] Repeat all independent crash/hang faults: identity/session preserved for
  API replacement; engine reaped before replacement, HALT/positions preserved
  and no additional orders. Parent shutdown 1.82s, ownership released.
- [x] Existing daily-cache TTL15: separate 300s p99 220.63ms; matched 300s/900s
  p99 631.47/206.92ms, zero errors and passing latency budgets. Public Demo env
  example uses 15s explicitly; shared default stays 5s, private/running Demo unchanged.
- [x] Retain the longer TTL15 run as failed overall: the third API fault's
  immediate live-order assertion coincides with normal sixty-second refresh.
  Subsequent engine faults were not reached in that run.
- [x] Two Red / 59 selected Green native tests for bounded quote recovery;
  focused real-container forced-refresh/full-fault retest passes at
  `20261029-210516-4f66fe`. Engine/session identity, HALT, positions and absence
  of additional orders remain checked. Original long failure is not reclassified.
- [ ] Cold million-fill query cost, production supervision integration,
  ordinary-entrypoint/full final-image load and continuous-hour memory gates
  remain open. Current passing latency windows are not final release acceptance.

## Production supervision integration — native phase (2026-10-29)

- [x] Share the tested child policy with runtime; add fatal-startup, termination
  race and signal-during-launch Red/Green coverage.
- [x] Ordinary entrypoint launches API/engine roles with public/loopback health,
  total pool partitioning, one migration owner and child dotenv/credential
  isolation. Existing engine/recovery/control handlers remain reused.
- [x] 95 selected native checks pass, including initialization interruption,
  read-only API schema validation, retryable bootstrap, readiness and safe logs.
- [ ] Update actual-role offline instrumentation, build/reverify the final image
  and complete the unchanged load/hour/recovery gates. No Demo replacement or
  Cloud Run deployment has occurred.

See [current runtime verification](../mm-service-supervision.md).

## Supervised runtime verification follow-up (2026-10-29)

- [x] Focused native 124 checks, actual-role overlay lifecycle and measurement
  preflight verified. Retain failed 60s latency probe alongside passing traced
  90s probe; neither certifies the final image/hour gate.
- [x] Complete native regression: 2,009 tests plus 11 subtests pass; Swagger and
  offline deployment-template review pass. Shared DB pool remains total five:
  API two, engine three. A configured ten means API two, engine eight.
- [ ] Final Dockerfile build, real PostgreSQL regression, full final-image E2E,
  continuous-hour load/memory and ordinary-entrypoint control are in progress.
  No persistent Demo replacement, real trading start or Cloud Run deployment.

Final Dockerfile image `4da2f008…` is built and fingerprinted; packaged ABI,
connector/SQL assets and logging smoke pass. PostgreSQL initially has 257 passes
and two helper/CLI argument failures; corrected real-process helper reruns all
three API/service/schema lifecycle checks successfully. Full image E2E is now
running with distinct previous image `58356746…` for compatible rollback. The
combined final-image/hour/control acceptance item above remains open.

The first image E2E finds an idle API shutdown bug during funding: engine exits
cleanly, API needs forced termination, parent correctly reports failure. Preserve
that failed run. A real OS-signal Red/Green test confirms direct asyncio Event
setting does not wake an idle selector; use the loop's thread-safe wakeup path.
95 focused tests pass; a replacement image/full gates are required. No HALT reset,
trading start, persistent Demo replacement or deployment occurs.

Replacement image `f5e7ab32…` is built from `6de4001cd`. Full native regression
now passes 2,012 tests plus eleven subtests; the three real-PG lifecycle/API/schema
checks, Swagger and image smoke pass. Identical Linux idle-signal probes confirm
old-image exit 1 after 3.12s versus fixed-image exit 0 after 0.72s. New full E2E
`20261030-000203-0744c6` has passed the funding shutdown/recovery case that failed
before and is continuing through protection/upgrade scenarios. Final full E2E,
actual-role fault replay, continuous-hour load/memory and production control are
still pending; no partial result closes their checklist.

Bootstrap replay confirms a test orchestration issue: the fixed 30s episode can
expire during cold restart. Harness `wait=False` submits accepted intent before
supplying qualification depth, then requires the same receipt to complete; no
runtime deadline/policy changes. 29 focused checks and real bootstrap-case E2E
pass. Full16 E2E repeats at `20261030-003620-4745f4` on unchanged image `f5e7…`.
Actual shipping-role crash/hang replay now passes at
`20261030-003706-4b55b3`, including preserved identity/positions/HALT, no extra
orders, engine-only migrations and clean 3.28s parent shutdown/ownership release.

- [x] Corrected full16 E2E `20261030-003620-4745f4` passes on `f5e7…` / manifest
  `cee15b5a…`, including compatible image rollback. R6 image/regression phase is
  checked complete; both earlier full failures are retained.
- [ ] Start unchanged all-feature all-profile load, burst and continuous-hour
  per-role memory/fault gates, then matched uninstrumented production control.
  Two CPUs/2GiB, total pool5 (API2/engine3), daily TTL15; no parallel performance
  jobs. No persistent Demo replacement or Cloud Run deployment.

The first long load `20261030-011239-d491a4` is interrupted during mixed: its
warmup passes (p99 408.83ms, zero errors) but the emulated process sampler retains
cross-child auxv descriptors. A no-DB/no-trading two-child replay proves twenty
old samples add twenty FDs per child; corrected samples add zero. Known role/PID
mapping avoids recurring cmdline reads; ambiguous/missing roles still fail the
unchanged FD gate. Two Red / 31 Green checks and a real-wrapper 180s probe pass
(p99 295.37ms, zero errors; FDs fixed 5/31/21). Runtime/image remain `f5e7…`;
the full hour/load/control gates restart from scratch, original run stays failed.


Long run `20261030-013705-7a6a10` passes warm300/mixed900/burst90 with zero API
errors (p99 336.36/656.92/155.26ms), then its first random rollover exposes an
actual late-fill checkpoint race. Old shutdown snapshots 0.011 LIFE before a
valid cancellation-time fill changes it to 0.012. Existing guard correctly
HALTs; interrupted run remains failed, and no HALT is reset. TDD work changes
supported live PositionExecutors to cancel/read terminal fills before persisting
holds. Unknown/unsupported/post-checkpoint changes remain guarded. A replacement
image, full regression/E2E and unchanged hour/control gate are required.

Checkpoint fix native phase: 2,026 full Make regression tests plus eleven
subtests, 91 focused cases (including the added SHORT case), and eight targeted
real-PG cases pass. Deterministic ordinary-entrypoint Red on old image `f5e7…`
at `20261030-024209-940a65` reproduces HALT and recovery position mismatch. Full
PG regression continues; build a replacement image and require image-level
Green/full E2E before the unchanged load/hour/control gates.

Replacement `80a34c69…` from `c2a22c0ae` passes nine packaged module hashes,
full PG266/266, Swagger/runtime smoke, and exact shipping-image cancellation-fill
Green `20261030-025503-780bad`. Spot/perpetual retain exact base/fees across
random rollover, duplicate trade events, same-session cold restart and Stop;
normal shutdown 2.986s. Mark the checkpoint-race checklist done. Full16 E2E and
actual-role faults are running on the same manifest; long/hour/control stay open.

First replacement full E2E `20261030-025650-d4ba2b` is retained failed: the
cancel-before-create ordering fixture holds ACK while reading FE history, crossing
the unchanged five-second REST timeout and HALTing. No new checkpoint mismatch.
Release the injected ACK hold after durable cancellation proof, before FE page
validation, and record its duration; no runtime guard relaxation. Controlled
replay/full E2E are required before starting the long gate.

ACK-hold harness replay `20261030-031749-6c613e` passes on unchanged `80a34c69…`,
including restored quotes/random expiry/funding/DB-fault HALT. Native harness24
pass. The focused recovery variant omits the nine-profile matrix; repeat full16
with compatible rollback before long load. Runtime timeout and safety guards
remain unchanged; retain the failed full run.

Replacement full16 E2E `20261030-032310-8526be` passes on `80a34c69…`, including
full nine-mode/profile matrix and compatible rollback. ACK hold is 976ms in this
full replay; original timeout/HALT policy stays unchanged. Actual-role faults
`20261030-025652-fcd9de` also pass (preserved identity/positions, physical reaping,
no extra orders, engine-only DDL, clean 1.65s parent shutdown). Mark replacement
image/regression/E2E/lifecycle done, then start the unchanged all-feature load/hour
and ordinary-entrypoint control alone. Those last acceptance boxes remain open.
