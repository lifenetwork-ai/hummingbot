# LIFE MM logic-flow review — 2026-10-26

Later load rerun: see [current release results](../release/mm-release-closeout.md#2026-11-01-latent-fix-candidate-rerun).
The native/targeted-image verification below preserves its original scope.

Latest follow-up (2026-11-01): **B1–B4 fixed, committed and verified on image `3f5cbd0f`**.
See [the new findings](#2026-11-01-latent-bug-audit-and-fixes) and
[original audit evidence](life-mm-latent-bug-audit-20261101.json), with
[separate fix evidence](life-mm-latent-bug-fixes-20261101.json). They predate the last
three flow optimizations. This read-only review does not alter runtime or
retroactively change earlier passing release/test results.

Historical 2026-10-26 status: fixes, final native/real-PostgreSQL checks, rebuilt-image E2E and the
isolated load probe pass. All review checklist items are complete. The implementation
reuses the existing service, MM controller, control store, connector helpers and
funding checkpoint. No new framework, storage table or cache is needed.

## Confirmed defects and fixes

| Flow | Reproduced behavior | Corrected behavior |
|---|---|---|
| Partial fill and pending cancellation | The first fill sets `is_trading`; remaining live entry quantity drops out of exposure. `SHUTTING_DOWN` also stops being active before cancel acknowledgement. Another level can exceed inventory/gross caps. | Reserve each configured entry while active or shutting down, until its terminal hold/termination. Preserve separate perpetual long/short exposure. |
| Decimal quote boundaries | `Decimal(float_spread)` makes the theoretical 0.099/0.101 quotes fall outside those exact configured price bounds. | Convert the spread through its decimal string in the shared MM price helper. |
| Start/switch superseded during awaits | Cleanup or configuration loading yields to a newer stop/switch, but the old row can still initialize the previous strategy and cause a false recovery HALT. | Re-read owner/control/session state after both awaits; abandon superseded intent before `prepare`. |
| CLI/MM/legacy transitions | CLI stop leaves MM preparing/running; later admin start stays blocked. Explicit legacy YAML selection retains a stale MM snapshot. | Share the MM stopping/generation handshake with CLI stop, and replace the MM snapshot on explicit legacy selection. Preserve recovery/HALT. Load the MM model before the control transaction so cold CLI imports cannot block the owner heartbeat. |
| Stopped revision bootstrap | Missing previous YAML or credentials prevents a stopped service from exposing controls. | Validate the deployed selection when it is requested to run; stopped startup remains controllable. |
| Observational reconciliation | Matching IDs/status/fills can hide wrong instrument, side, original size, type or price. Spot market size can use quote units. Amendments can evade the final read. | Compare essential working terms using existing symbol/type/contract helpers. Missing terms are incomplete; differences are mismatches; changes between reads invalidate the comparison. |
| Funding history coverage | Once the first order ages beyond retention, already-observed history is marked missing. Downtime gaps start too early; an expired overlap can produce reversed bounds. | Preserve contiguous committed coverage. Start an actual gap at its last successful end, trim old overlap, and repair false gaps wholly inside proven coverage. |
| Configured funding cadence | A one-hour poll interval is marked overdue/stale after the fixed two/five-minute thresholds. | Persist poll cadence with the checkpoint; overdue uses that interval and stale uses max(five minutes, two intervals). |

## TDD evidence

Failing assertions were executed before the corresponding fixes. Logs remain in
ignored `build/logic-review/`; the portable result will retain their identities
and the final tested image.

| Area | Red evidence | Targeted Green evidence |
|---|---|---|
| MM exposure and price | Seven failures for partial-fill/price cases; six failures for the cancellation window | 163 controller, PMM, PositionExecutor and ExecutorOrchestrator tests |
| Service/control | Seven native failures plus four real-PG failures; one additional cold-import heartbeat failure | 26 native lifecycle/session tests, 47 initial PG tests and 53 targeted PG tests after the cold-import correction |
| Reconciliation/reporting | Eleven order-term/missing-field/amendment failures, then two market-size-unit failures | 63 admin/reconciliation tests and 11 PG reporting/reconciliation tests |
| Funding | Five failures for retention/overlap/downtime/cadence/repair | 19 funding and service-observer tests |

The consolidated native regression passes **1,321 tests and nine subtests**:
149 configuration, 11 performance, 931 CLI/model/connector/engine/V2 and 230
controller/session/transport/funding tests. Existing deprecation warnings remain.
The final **127 PostgreSQL tests** pass against a fresh disposable database,
including the cold-import heartbeat regression. The 13 funding cases also pass
after formatting-only corrections.

## Findings from the first image attempts

The first V2 fixture expected the old float-rounded quote of 98.99. The actual
ledger records a buy at 99.00, the exact 1% discount to a 100 anchor. The fixture
now checks 99 in the wire order, recorded fill and held position. Its failed run
remains retained rather than counted as acceptance.

The next run passed both V2 paths and all nine MM combinations, then its new CLI
stop case exposed a control-row lock timeout. The initial CLI/MM fix lazily
imported `MMSession` while holding `SELECT ... FOR UPDATE`; cold dependency loading
blocked the service heartbeat long enough to close the process. A deterministic
real-PG Red test now publishes from another connection during that import.
Loading the model before taking the transaction lock passes this test and the
53-case targeted control/ownership/service selection. The rebuilt final image
passes the same CLI/MM/legacy transition through the real service.

## Behavior and verification limits

Full-entry reservation is deliberately conservative. If account observations
already contain a partial fill, that filled amount can temporarily count twice
until the executor reaches its terminal hold. Quoting may therefore pause earlier
at tight caps. After termination, account snapshots remain authoritative; this
change does not add atomic sequencing between exchange fills and balance updates.

The review covers the declared OKX spot/perpetual/both MM paths. It does not claim
all possible upstream strategy/connector defects have been eliminated. The new
image is verified locally; existing real Demo funding/trading evidence retains
its original image identity. No real exchange orders or Demo database changes
are part of this follow-up.

Operational contracts: [admin reconciliation](../../docs/fe-integration.md#observational-reconciliation),
[funding observation](../../docs/postgres-runtime.md#funding-while-trading-is-stopped).

## Final image and load verification

Runtime source commit `ed98eaf18e`, image `sha256:92e9e94f1606e6aac623ba9a8e4e8e4ea15b30917f5d791cc3c7e759a62ac4fe`
(1,364,516,628 bytes), with all 19 checked runtime/migration hashes matching source.
All seven offline groups pass in one combined run: schema, spot, V2 spot,
V2 perpetual, LIFE MM, funding and compatible-image upgrade/rollback. The LIFE
matrix includes all nine profile/market combinations, exact quote levels, CLI
MM stop acknowledgement, legacy selection replacing MM state, and a new admin
MM start afterward. Existing recovery, funding and owner/fault checks remain active.

The isolated **60-second diagnostic probe** uses 100,000 historical orders/fills,
20 completed reads/s, approximately 100 book updates/s and one fill/s. Aggregate
read p95 is **164.07ms**, p99 **756.60ms**, with zero unexpected errors or
budget violations. Sixty unique fills and two exact funding bills match the
independent exchange ledger, and positions match. This supplements the logic
regressions; it is not a new full soak or Cloud Run benchmark.

[Portable evidence](life-mm-logic-review-evidence.json) retains image/source
identities, harness hashes, Red/Green logs and both unsuccessful image attempts.
The accepted image is available locally as `hummingbot-cloudrun:local`.

### Probe variability and comparison

The first probe of the final image had no errors or ledger differences but missed
the p99 budget: p95 452.14ms, p99 1,053.14ms against the unchanged 500/1,000ms
thresholds. A subsequent sequential comparison measured the previous image at
p95 207.03ms / p99 720.91ms and the fixed image at p95 164.07ms / p99 756.60ms;
both comparison runs pass. The earlier failure remains in the portable evidence.
These short observations show run-to-run tail variation; they neither establish
a cause for the first spike nor demonstrate a sustained performance improvement.
SQL query budgets remain bounded: orders at most three statements,
fills/performance one, daily statistics six. No extra cache was introduced.

## 2026-11-01 latent-bug audit and fixes

This targeted audit inspects intent admission/cleanup, partial-fill outflow,
session/Random transitions, owner recovery, shutdown and funding observation.
Four isolated reproductions at audit revision `bc2e4a626` confirmed the defects below. Ownership,
recorder or filesystem dependencies are mocked where stated; no connected venue,
Demo account, database or runtime configuration is changed. This is not an
exhaustive proof that the remaining code has no defects.

At audit time, the three affected files were byte-identical to application baseline
`fc0ee55b8`. These were pre-existing gaps, not regressions introduced by the three optimizations
committed as `9ffb4cf93`, `8fcf20fab` and `d3c34153a`. Passing earlier release
results retain their image/scope; they do not exercise every edge case here.

### B1 — P1: rejection before intent recording leaves spot reservation behind

[`before_order`](../../hummingbot/core/bot_ownership.py#L201) reserves spot outflow
before checking the final quote policy and recording the intent. If the quote
check rejects, no active intent was added. However,
[`order_failed`](../../hummingbot/core/bot_ownership.py#L233) releases unsent outflow
only inside its active-intent branch. The earlier reservation/tracked order stays.

Reproduction uses the actual owner/outflow methods with mocked healthy ownership,
identity and recorder, then injects a legitimate `spread_policy_quote_rejected`.
After the local FAILED transition, there are zero recorded/active/sent intents,
positive no-transport evidence, but10 USDT remains reserved and one terminal proof
is pending. The next BUY preview returns false with `spot_outflow_reconciling`.
No order was submitted to the venue in this reproduction.

The terminal reconciler cannot establish a positive venue proof for an order that
never existed. This can leave BUY waiting until explicit recovery/session cleanup;
the same obligation also reaches the shutdown proof path. The downstream shutdown
effect follows from source inspection, not a reproduced process crash.

Fix direction: unwind pre-intent side effects when no transport is possible;
preserve durable unsent rejection, owner fencing and uncertain-send HALT behavior.
Cover quote rejection after reservation, failed intent recording, failures after
a recorded intent, cancellation and sent/unknown outcomes. Merely clearing every
failed order would discard necessary uncertainty protection.

### B2 — P1, conditional: fixed-fee coverage reused for multiple future fills

In [`SpotOutflowProjection.add_fill`](../../hummingbot/core/spot_outflow.py#L174),
each future fill subtracts a coverage amount including the order's entire flat-fee
allowance. [`reserved`](../../hummingbot/core/spot_outflow.py#L192) includes that
flat allowance only once. Multiple future fills can therefore receive the same
coverage repeatedly and understate known debit risk.

The exact Decimal reproduction uses a25 USDT cap, a10 USDT order and5 USDT flat
allowance. Two future-dated half-fills each cost5 USDT plus5 USDT actual fee:
known future debit is20 USDT, while the projection reserves only15. Another
order costing5 USDT plus the same5 USDT flat allowance is accepted. Known debit
plus new reservation is30 USDT, above the25 USDT cap.

This requires a **nonzero flat allowance and multiple future-dated fills**; it
does not demonstrate failure of the ordinary zero-flat-fee setup. Actual OKX
occurrence is not claimed. Fixed fee overrides are supported by the existing fee
schema; future-dated observations already have an explicit projection path.

Fix direction: account for known future debit and remaining order coverage
together, allowing the per-order flat allowance only once. Red/Green cases must
cover duplicate/reordered fills, partial maturity, rebates, zero/positive flat
allowances, expiry and terminal confirmation. Do not discard actual fees or
advance the clock artificially to hide the excess risk.

### B3 — P2: a transient first YAML read can disable idle funding discovery

[`FundingObserver.collect`](../../hummingbot/cli/funding_observer.py#L51) assigns an
empty `template_pairs` set before awaiting catalogue load. If the first load
raises, later polls see a non-None empty cache and do not retry it. With no
historical pairs and no active connector, collection returns early indefinitely.

The actual method is exercised twice with a fake store/owner and a loader that
fails once, then could succeed. Catalogue load is attempted only once, no reader
is created, and the previous `OSError` persists. Trading is not stopped by this
optional observer failure, but configured funding observation does not self-heal
in this idle/no-history case.

Fix direction: publish discovered pairs only after successful loading. Verify
failure→success retry, legitimately absent/no-perpetual configuration, cancellation
and continued stopped-service observation; do not start a trading connector.

### B4 — P2: rejected-order identity history is unbounded in a continuous session

[`order_failed`](../../hummingbot/core/bot_ownership.py#L238) adds every positively
unsent rejection ID to `unsent_rejections`. Its only explicit clear is
[`prepare`](../../hummingbot/core/bot_ownership.py#L106); normal intent pruning
does not retire these IDs. A fixed Until-stopped session can run without another
preparation, so repeated harmless submission denials grow this set indefinitely.

After10,000 simulated local failures and an actual `_check_intents` call with an
empty mocked durable query, all10,000 IDs remain despite zero resident intents
and connectors. The native set/string size estimate is1,113,394 bytes; this is
not whole-process RSS or an observed OOM. Random/session changes that call
`prepare` can hide this growth in other workload profiles.

Fix direction: retain no-transport evidence for orders/tasks that can still be
visited by tracker, executor or shutdown processing, then retire it with that
lifecycle. Test sustained rejected-order churn and retained failed/cancelled order
objects. Arbitrary TTL eviction or a small recent-ID cache could reintroduce
false unknown-outcome HALT during shutdown.

### Remediation checklist

- [x] B1: Red/Green pre-intent reservation cleanup and real PostgreSQL owner tests.
  Initial Red5 failures/1 pass; Green69 selected PG and27 native cases. New local
  admission rolls back before persistence, while attempted intent writes remain
  tracked until a fenced durable rejection succeeds. Acknowledged/conflicting
  recorder rows cannot be rewritten; uncertain rejection still retains HALT/risk.
- [x] B2: Red/Green cumulative future fee coverage and exact Decimal differential replay.
  Red18 failures/6 passes; Green50 native and20 selected PostgreSQL cases.
  Fixed allowance covers the complete order once; known future debit survives
  release of its local reservation and expires through the existing window.
- [x] B3: Red/Green funding catalogue retry after transient failure/cancellation.
  Red2 failures/9 passes; Green24 native and11 selected PostgreSQL cases.
  Failed/cancelled reads leave the catalogue uncached for the next poll.
  Missing/perpetual-free YAML does not activate trading.
- [x] B4: Red/Green bounded rejection history while preserving shutdown evidence.
  Red2 native and2 PostgreSQL failures; Green6 native and48 selected PG cases.
  Weak references follow live order objects, including shutdown captures after
  tracker removal. Different objects with the same ID require readback; sent
  failures and uncertain outcomes retain the existing recovery behavior.

The original audit was read-only. B1–B4 implementation and combined native/PostgreSQL/targeted-image
verification are complete. [Audit evidence](life-mm-latent-bug-audit-20261101.json) preserves
the original reproductions/source hashes; [fix evidence](life-mm-latent-bug-fixes-20261101.json)
tracks new Red/Green results. Raw results are under ignored
`build/potential-bug-audit-20261101/` and `build/latent-bug-fixes-20261101/`.
No new full-release/image/load acceptance, deployment or historical H1/H2/H3
causal closure is claimed.


### Combined verification — 2026-11-01

- [x] Native declared regression target: **2,208 passed**, plus11 subtests.
- [x] Disposable PostgreSQL suite: **279 passed**. All8 strengthened shutdown
  identity cases were rerun after keeping the original rejected object alive.
- [x] Swagger drift check and commit hooks pass.
- [x] Image `3f5cbd0f` (application commit `3da3598bf`) packages all4 changed
  modules with matching SHA-256 hashes. This is a local derivative of `4971ea59`,
  with unchanged native dependencies, **not a clean full Dockerfile rebuild**.
- [x] Targeted image E2E groups: **spot-outflow, funding and life-mm** pass
  using the same immutable manifest. These cover outflow admission/fills/restart,
  exact funding bills/restart/closed-position catch-up, and the MM profile/mode
  matrix, Random expiry, fill/fee restart and database-outage HALT.
- [x] Read-only preservation check: Demo ledger fingerprints, control and its
  existing `e526118b` image remain unchanged.

The rejection-retention regression creates10,000 retired orders while keeping3
live order objects, then releases those3: only the3 live proofs remain, then0.
This verifies the specific retention fix; it is not a new whole-process memory
soak. No production GC policy, risk cap, ownership fence or timeout is relaxed.

[Portable fix evidence](life-mm-latent-bug-fixes-20261101.json) records each
phase commit, Red/Green results, source/image hashes, E2E metrics and raw-log
hashes. The original [audit JSON](life-mm-latent-bug-audit-20261101.json) remains
unchanged. This follow-up does **not** claim a new full E2E/upgrade matrix,
hour-load/soak acceptance, connected Demo trading or deployment; previous
release evidence retains its own image/scope and H1/H2/H3 attribution limits.
