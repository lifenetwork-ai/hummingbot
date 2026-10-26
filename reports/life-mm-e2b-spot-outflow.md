# E2b: rolling spot USDT spending admission

Date: 2026-10-26 (Asia/Ho_Chi_Minh). Baseline: `4e005cfd9`.
Scope: E2b of the [enhancements plan](../docs/plans/LIFE_OKX_MM_ENHANCEMENTS_TDD_PLAN.md).
Status: E2b complete and verified locally.

## Changes and reuse

An opt-in `spot_outflow` catalogue policy bounds recorded spot net USDT outflow
plus pending BUY limit notional and explicit fee allowance. Original USDT fees
and rebates affect cashflow; other fee currencies remain original amounts.
Perpetual fills, funding, transfers and account-equity changes are outside this
metric. Default `null` preserves existing behavior.

The controller uses a cheap proposal filter, while the existing owner gates
reserve by client order ID before intent and recheck after REST throttling.
Repeated checks count that ID once. SELL/perpetual admission, cancellation,
ownership, session expiry, drawdown and shared readiness retain their ordinary
checks. BUY-only cap or coverage denial does not globally pause the MM session.

The existing recorder notifies the projection only after successful commit,
including its original persisted timestamp on duplicate events. This prevents
tracker progress or a differently timestamped retry from releasing capacity
early or moving a fill between windows. Startup streams a bounded current window
through the existing market/pair/timestamp index; subsequent updates and heap
expiry are in-process. No new fill table, migration, index, Redis dependency,
service, ledger or environment variable was added.

Terminal proof reuses the existing connector's authenticated/throttled reads,
pagination and extracted REST fill parser. Cumulative quantity and stable final
order details must match, and recovered fills use the existing recorder/PKs.
An acknowledged cancel alone does not release pending risk. Read failures retain
reservation and block BUY; proven unsent intents release only after the normal
durable rejection path. The existing bounded shutdown preserves inventory and
positions, and uncertain recovery retains HALT.

One verified account/environment/pair and immutable policy are bound in the
existing bot recovery state. Unbound spot history waits a full observed window;
it is not relabelled with current credentials. Account/environment mismatch
retains HALT. Profile/random/market selection and clean restart retain policy,
coverage and window spending; ordinary commands cannot disable it or bypass it
through legacy strategy selection. See the [guide](../docs/life-mm-spot-outflow.md).

Status/Swagger include binding, policy, UTC window/observation times, exact
Decimal strings for recorded/reserved/remaining quote, original non-USDT fees,
coverage and last BUY denial. Owner-unavailable capacity is stale/unknown. Cold
stopped state shows the durable binding without inventing current amounts.

## Red/Green and measurement

Initial projection/ownership contracts failed before their modules existed;
the controller test failed until a BUY-only proposal filter was added.
Boundary tests explicitly demonstrate:

- Committed buys/sells, USDT fees/rebates and original base/third-currency fees;
  multiple same-timestamp identities, duplicates and late/out-of-order fills.
- Half-open UTC window edges. A committed upper-edge debit without a live
  reservation originally allowed excess new capacity; its Red/Green fix retains
  pending risk until that debit enters the window.
- Repeated pre-intent/final checks, partial/delayed commits, unknown fees,
  terminal timeout, complete terminal backfill, unsent release and SELL admission.
- Verified account/demo-live mismatch, unbound legacy origin, immutable armed
  policy and reconstruction after a market switch.
- Long-lived partial-order deduplication. A failing test demonstrated retention
  beyond the terminal reader's 10000-fill scope; its fix stops growth and blocks
  new BUY without forgetting committed quantity or freeing uncertain remainder.
- Final startup-authority review found a confirmed initialization race. A failing
  real-PG test shows account-setup permission ending before asynchronous outflow
  reconstruction finishes, and a new binding HALT could be followed by an
  unconditional `allow_new_orders = True`. The enabled-only owner branch now
  retains setup authority during reconstruction, keeps ordinary orders closed,
  and rechecks durable HALT before opening order permission. It does not retry
  private mutations or change the disabled path.

The large-window PostgreSQL case reconstructs 100001 current-window fills in
about 2.4 seconds with a constant number of queries. One thousand subsequent
reservation/status pairs take about 19ms and add **zero SQL**. The final repeat,
overlapping native tests and Docker compilation, measured 3.21 seconds / 34.26ms
with the same zero-query hot path. These are local diagnostic observations, not
Cloud Run latency guarantees. Existing ownership,
recorder and intent SQL are still required; this does not claim the entire order
send path is SQL-free. Two data-generation attempts were corrected: a missing
required leverage field, then the fixture's bulk-insert statement timeout. The
test-only seed receives a longer timeout; production queries/gates are unchanged.
The final twelve-case selection also checks a late actual-fee commit between
pre-intent admission and final transport: the queued BUY is rejected and durably
released without blocking a funded SELL or creating recovery HALT. That run
measured 1.92 seconds / 20.18ms, again with no additional hot-path SQL.

A separate native pure-helper diagnostic expires 100000 same-timestamp fills
in 234.47ms (construction 403.37ms), with no DB access. This measures the bounded
worst-shaped expiry burst locally; it is not a 1-CPU container latency gate.
The final E6 workload should include an active-window expiry burst with all
enhancements enabled before release. No extra cache/index is justified by these
measurements; the projection already retains only the needed window/pending risk.

## Verification

Final native regression passed 1452 tests and nine subtests (151 / 11 / 974 / 316).
Focused verification passed 93 tests with RuntimeWarning treated as errors.
The final full PostgreSQL suite passed 161 tests, including thirteen outflow cases.
It includes late-commit final-send rejection and setup authority/HALT after
reconstruction. Broad suites retain existing mock/deprecation warnings; focused tests treat
RuntimeWarning as errors. Scoped hooks and generated Swagger checks passed.
The initial focused image case passed BUY-only levels, perpetual/sell continuity,
partial BUY fees, actual SELL rebate, terminal release, restart and market-mode
changes. It predates the additional per-order memory bound, so it is diagnostic.

Image regression
`sha256:1bd523ed8b06459520cbaca23b84ad0696bb8ee7b059b94e479bf6c45296dc82`
passed all ten E2E groups, including the complete outflow case. Artifact:
`build/e2e-offline/20261026-213731-88e20c/result.json`.

Final image
`sha256:cad5a536266c58aa61dfad61afa04ff3df601e329af4dbe408f07f0bb5b62cff`
is 1364671166 bytes; twenty-five runtime/config/contract hashes match packaged
paths. Only the enabled outflow initialization branch in `bot_ownership.py`
changed from the regression image. Final native/PG suites, enabled outflow image
scenario and combined load were rerun after that change. The final focused image
case's startup took 8.58 seconds, restart 8.73 seconds and clean shutdown
0.80 seconds. It proves one spot BUY fits the configured cap while two funded
SELL levels and perpetual entries continue, partial fill/fee deduplication,
terminal release, cold restart, perpetual-only then spot selection, and resumed
BUY after committed SELL proceeds/rebate. Artifact:
`build/e2e-offline/20261026-221854-4372df/result.json`.

The final sixty-second probe enabled outflow, drawdown and strict quote guards
together. It passed unchanged budgets: p95 **52.18ms**, p99 **327.89ms**, 1200
HTTP 200 responses, no unexpected errors and no violations. A 1 CPU/2 GiB bot ran
under ARM-to-amd64 emulation with 100000 historical orders and 100000 fills;
measured rates were 19.97 completed reads/s, 99.98 market updates/s and one
fill/s. Command acceptance p95 was 34.17ms and observed RSS was 326565888 bytes.
Sixty unique fills and positions matched the independent ledger, with two
identified funding bills and three daily snapshots. The empty test DB binding
was verified before adding yesterday's synthetic history; legacy warmup was not
bypassed. Test-only settings are recorded, not promoted to production defaults.
Artifact: `build/mm-load/20261026-222038-3b1cbc/result.json`.

Immutable identities, measurements and source/fixture hashes are in
[portable evidence](life-mm-e2b-spot-outflow.json).

E2c–E6 remain open. No new sixty-minute gate, previous-image upgrade, Cloud Run
deployment or real OKX Demo operation is claimed. Raw logs and ledgers remain
under ignored `build/e2b`, `build/e2e-offline` and `build/mm-load`.
