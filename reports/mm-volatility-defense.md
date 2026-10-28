# MM volatility defense implementation

Implementation of the [ordered TDD plan](../docs/plans/MM_VOLATILITY_DEFENSE_TDD_PLAN.md).
Date: 2026-10-28 (Asia/Ho_Chi_Minh). V0–V4 runtime implementation is complete. The V5 load gate exposed reporting
resource contention; the bounded-worker/encoding/configuration follow-up now
passes final local/image/load gates and bounded OKX Demo compatibility. V5 is
complete on the exact artifact below; there has been no Cloud Run deployment or
live LIFE acceptance.
Earlier release evidence is not new acceptance. The sections below retain
chronological failed/pending attempts; the current table and final acceptance
section record the completed scope.
[Portable evidence](mm-volatility-defense-evidence.json) retains the current
status, immutable identities, focused TDD and original failed diagnostic scope.

Current candidate image:
`c924030a895a2cb37ec47ef56389462b940b306a89f80e6630dbad887ef68043`,
1,366,856,929 bytes, with all 26 selected runtime files matching source commit
`f52f6ee3f`. It adds the operator-requested `DB_POOL_SIZE` setting after the
bounded-worker/response-encoding changes. Earlier `795008ea…` and `c1263fe0…`
results retain their original scope, including their failed load windows.

| Current verification | Result |
|---|---|
| Native regression | 1,829 tests + 11 subtests pass |
| PostgreSQL | 243 tests pass |
| Handler-generated Swagger | Check passes |
| Pool configuration in Linux image | Default 5 and configured 10 verified; overflow remains zero |
| Declared synthetic comparison | 72 cases pass on unchanged controller source; scoped matrix, not a profit forecast |
| Complete image matrix | All 16 groups pass in one invocation, including upgrade/rollback; defense logs have no ERROR/CRITICAL or non-JSON lines |
| Enabled mixed/burst/hour/fault load | Full unchanged profile passes: warmup, mixed, burst, 3,600-second zero-error soak and all six post-hour faults; prior failures retained |
| Bounded OKX Demo compatibility | Pass: BTC spot/perpetual, twelve post-only quotes, weighted sizes, both real 1m candle feeds, Stop cancels quotes and retains positions |

## V0 — Baseline contracts

The existing MM base already produces three weighted levels on spot/perpetual:
at reference 0.1, the 0.4/0.9/1.8% levels produce the six proposed prices and
50/30/20 allocations within each side of a 200-quote total budget. The deployed
catalogue still selects spot/perpetual/both and two equally weighted levels.

Integration hooks: `LifeMMController.update_reference(sample_guard=True)` owns
policy sampling; ordinary readiness/send calls use cached observations.
`ServiceHost.mm_quote_valid` is called through `BotOwnership.mm_quote_check` at
intent and final transport. Existing controller `StopExecutorAction` preserves
positions. `BotOwnership._mutate_recovery` supplies fenced JSON checkpoints.
`SpreadPolicyStatus`, guard events and the service publication supply typed API
and history views without exchange calls from FE polling.

The new baseline tests are characterization, so they do not claim a Red/Green
runtime fix. Deterministic candle and clock cases follow in the owning phases.

Verification: five characterization cases pass. Candle fixtures reuse the
completed/forming/gapped feed helper in `test_life_mm_spread.py`; a separate wall
and monotonic clock fixture prevents offline time from earning recovery credit.

## V1 — Existing spread floors

Verified every level at zero/moderate/shock NATR and explicit static fallback
with `min_multiplier: 1`, ordered spreads and existing fee/rounding behavior.
Legacy low-multiplier/tighter-fallback settings remain readable. The MM guide
now explains three-level weights and the existing floor settings.

Verification: 31 focused spread/characterization tests pass. This phase needs
no new runtime flag; defense-specific validation is implemented with V2's schema.

## V2 — Reduced sizing and stale quote protection

Red: six new schema/raw-NATR/sizing/controller cases failed before implementation;
an additional task-context case failed because per-level authority did not exist.
Green: raw NATR is retained separately from the clamped spread multiplier, reduced
sizing composes with bootstrap/skew allocation, recovery needs distinct candles
and elapsed monotonic time, and final terms are checked against their own level.
Existing quote cancellation is coalesced, preserves positions and retains pending
reservations; no replacement occurs while an executor is shutting down.

The existing session context now carries optional level identity into async
connector tasks without changing the session authority tuple. Legacy policies
retain their existing send signature. Defense remains rejected at catalogue
admission until V3 installs durable pause/recovery; this commit cannot enable a
partial defense in the service.

Verification: 107 controller/spread/skew/session tests pass; a separate 134-test
selection including service and position executors passed before five additional
bootstrap/skew/per-level cases were added. The regression Make target includes
the new defense tests. Default deployed YAML and original file-mode policies
remain unchanged. An initially incomplete bootstrap test book was corrected to
supply real bid/ask entries; no production guard was weakened.

## V3 — Durable pause and recovery

Red reproduced an extreme threshold being treated only as reduction, backward
candle replay earning recovery, a missing durable checkpoint adapter and static
fallback continuing spread composition after a known extreme event. A retention
regression also demonstrated that a strong controller map kept a retired
controller alive. Green adds strict severity/recovery sampling, a bounded
owner-fenced JSON pause checkpoint and weak controller references.

Latches are account/environment and connector/pair scoped. Profile/session IDs,
Stop/Start/Switch and market selection do not clear them. Control CAS prevents
stale checkpoint clearing, transaction rollback leaves the latch in place,
corrupt/account-mismatched checkpoints require recovery, and YAML policy changes
are rejected while the corresponding pause is active. Recovery never credits
offline time. The existing account-identity helper is shared with spot outflow.

Controller cancellation requests precede the transition write; quantities and
filled positions retain the existing executor lifecycle. Static fallback cannot
clear a pause. Bootstrap admission cannot bypass defense readiness. Final REST
transport rejects an earlier narrower quote through the real owner and recorder.
Catalogues can now admit the complete feature; unsupported unmanaged mode is
rejected. No database table or migration was added.

Verification: 107 focused defense/spread/guard/bootstrap tests and 15 disposable
PostgreSQL defense cases pass. The native regression invocation also passed
1,732 tests plus 11 subtests before the final standalone paused-composition case
was added. Existing position-executor tests cover partial-fill preservation and
terminal cancellation proofs. Image/fault/load and Demo acceptance remain V5.

## V4 — Observable operation

Red reproduced missing typed raw-NATR/defense status, a generic operator label
for a volatility pause and stale snapshots still presenting a known pause/cancel
state. Green adds English labels/actions, Decimal/UTC status, current effective
allocation, recovery samples/time, pending cancellation and saved-latch status.
The global HALT/Stop/expiry contract stays authoritative; stale observations are
unknown rather than healthy. FE policy override remains rejected.

Only sampled state/reason changes log WARNING/INFO with bounded DEBUG detail;
checkpoint failures log ERROR. Repeated cached readiness/status evaluations do
not advance recovery or spam transition logs. Existing guard events record state
changes, and an attached checkpoint readiness check performs zero DB statements.
Swagger and the three canonical operating/integration guides were updated.

Verification: 63 native API/operations/defense/activity cases and 28 PostgreSQL
defense/history cases pass; Swagger generation/check and guide-link validation
pass. An earlier full PostgreSQL run (before the additional lifecycle/retention
cases) passed 226 tests. Final-source broad regression and image/load/Demo gates
remain V5.

## V5 — Verification history

The first dedicated image attempt failed in its observer: separate ledger reads
spanned quote replacement and produced an empty comparison group. The harness
now evaluates one ledger snapshot and retains first terminal-cancel time rather
than a later duplicate acknowledgment time. That retry passed weighted levels,
partial fills, delayed cancellation, durable pause/restart/new continuous-random
session, automatic recovery, all market modes and position-preserving Stop on
image `809f75ed0a05`. Its result remains scoped to that image.

A 72-case declared synthetic comparison passed independent accounting and replay
integrity. It also exposed an observability defect: intermediate spread state
was logged before later price validation failed, producing ten contradictory
WARNING transitions in five unchanged control samples. A Red test reproduced
this; Green publishes/logs only the final controller decision. This changes the
final runtime source, so image/load acceptance will use a rebuilt artifact.

A second compatibility Red test found that serializing disabled defense as a
new null field changed legacy policy payloads for readers that forbid unknown
fields. A standard Pydantic wrapper now omits only disabled defense; enabled
defense remains fully pinned. The 83-case controller/session/economic/fixture
selection and Swagger check pass after this change. Enabled-policy rollbacks
still require a runtime that understands the feature and its saved latches.

The test harness now includes the enabled three-level defense image scenario,
an explicit load flag/Make target without changed budgets, delayed simulator
cancellation with first-terminal proof, and an optional economic comparison
variant with per-market simulated state-time metrics. The original default
economic matrix and disabled load fixtures remain available. The 32-case
economic/exchange/diagnostic selection passes. Final-image acceptance is pending.

The old private Demo database port was unreachable and no retained Demo database
container/backup was found. The user requested recreating the container. A Demo
PostgreSQL container has been recreated with a dedicated named persistent volume;
the unavailable database's history/checkpoints have not been restored. Recreating
it made no exchange mutation. Exchange compatibility testing remains after the
local gates and current account/exposure review; no recorder fills are invented
to reconstruct the missing database.

Three further boundary Red tests reproduced: Stop racing the first pause write
discarded the breach, automatic random replacement could reset an unfinished
recovery window, and a cold profile could jump directly to normal sizing inside
the hysteresis band. Green allows owner/account-fenced recording across control
changes while retaining strict CAS for clearing, defers automatic replacement
without extending old step authority, and starts cold controllers conservatively
at reduced size until qualification. Account binding precedes defense sampling.
Recording risk during HALT does not clear it; automatic clearing remains blocked.
The 68-case focused selection and 32 PostgreSQL defense/session cases pass. The
plan/guides now state these contracts explicitly. Earlier image results remain
historical; final artifact/load acceptance uses this corrected runtime.

A hot-path Red test measured two spread compositions in one unchanged control
tick. Green refreshes the cached decision after a checkpoint only when its
persisted pause state actually changes. Stable ticks compose once, while the
existing intent/final-transport fee and policy checks remain active. The 67-case
defense/service/replay selection passes after the optimization. The dedicated
image fixture now also keeps a paused random controller across its original step
deadline, verifies that the deadline is unchanged and then exercises recovery.

The frozen runtime passes 1,746 native tests plus 11 subtests, 237 PostgreSQL
tests and Swagger checking. The latest 72-case declared economic comparison
also passes on this source. Image `42fb05a83b5c` matches all 17 changed runtime
source files and is in the complete offline image matrix, including a compatible
prior-artifact upgrade/rollback case. Healthy load/soak and Demo remain open.

The operating guides clarify the existing conservative startup collateral rule:
full declared selected budgets must be covered by available USDT. Two-sided or
reduced quote allocation does not lower that admission requirement.

The complete final-image matrix's first attempt stopped at the existing drawdown
observer. It compared full position dictionaries before/after deliberately changing
the mark price, so changed `upl`/`notionalUsd` incorrectly failed position
preservation even though contracts, side and entry terms were unchanged. A Red
test failed for the missing exposure comparator; Green compares instrument,
side, contracts, entry price and leverage, and still rejects a reduction/closure
or changed entry terms. All five observer diagnostics pass. No runtime change
or load-budget relaxation was needed; the full matrix is being repeated on the
same immutable runtime. The failed attempt remains at
`build/e2e-offline/20261028-072541-d0e24a/result.json`.

The enabled load harness adds a separate 90-second volatility fault window with
the existing concurrent read workload. It measures sampled-pause log timestamps
against independent first terminal-cancel acknowledgments, checks reduced
allocation and pause persistence, then a stale-candle outage with no submissions
and fresh-data recovery. This does not replace or dilute any healthy window.

The repeated 16-group image matrix passed, including the compatible upgrade and
rollback. Log review then identified an existing last-price fallback defect:
the OKX perpetual bulk ticker response can contain markets outside the tracker's
books, causing `KeyError` and a 30-second retry backoff. It also has the same race
if a book retires while REST is in flight. Two Red tests showed a valid tracked
book failing to receive its price; Green ignores only books no longer tracked
and continues the other updates. No request, execution or retry policy changed.
The 51 tracker/observer diagnostics pass and the tracker suite is now included
in the regression Make target. A rebuilt image and fresh final gates are required;
the passing `42fb05a83b5c` matrix remains prior-artifact evidence only.

The offline ticker fixture previously returned only BTC/100 even for other
requested instruments. Three Red cases captured the mismatch; Green returns the
configured instrument scope and current fixture prices for single/bulk queries.
All 16 fixture/observer diagnostics pass. This keeps fallback traffic realistic
after the tracker fix rather than repeatedly asking for an omitted tracked price.

Final image `3306c018f63b` passes the complete 16-group E2E matrix, including
legacy-disabled, all market/profile combinations and compatible upgrade/rollback.
The defense case observes about 200 quote units normally and 100 when reduced,
two exact partial fills, first terminal proof with two-second simulated cancel
delay, restart/Start pause retention, unchanged paused random deadline, all three
market modes and position-preserving Stop. Synthetic one-second candles recover
in 12.81 seconds; this is not a production one-minute-candle recovery ETA.
No `ERROR/CRITICAL` or last-price fallback warning appears in that defense case.
The full matrix has no last-price warning. Other recorded ERRORs are the explicit
schema/DB/controller/drawdown failures or final-send `SubmissionBlocked` events
during injected guard/outflow restrictions, not hidden healthy acceptance.
Raw final matrix: `build/e2e-offline/20261028-084219-d3e945/result.json`.

The enabled 60-second, 100k-order/100k-fill probe passes at 20 reads/s, p95
294.60 ms and p99 805.04 ms, peak RSS 497,311,744 bytes. All 60 unique fills,
fees, positions and two funding bills match independent truth; healthy runtime
ERROR/CRITICAL count is zero. Twelve earlier setup rejections were native
`SubmissionBlocked` events during deliberate Start/Switch/Stop supersession,
outside the measured interval, not unexplained network failures.

The first load-fault diagnostic stopped in its timing observer, before the full
gate. It required an ACK after each market's pause log, but rising dynamic spreads
had already canceled all working orders roughly two seconds before pause. The
observer now verifies known working orders' first terminal proof from controlled
input time (a conservative upper bound on decision-to-cancel), retains the same
five-second bound, and reports pause-only latency as not applicable when no
orders remain. Joint readiness uses the first market's pause decision. Red/Green
also checks duplicate terminal timestamps cannot inflate this measurement and
missing proof cannot become a fabricated zero. Ten observer/budget tests pass.
No runtime or image change was needed. Failed evidence remains at
`build/mm-load/20261028-091026-24540b/result.json`.

A separate diagnostic retry never reached the workload: Docker's short
`exec python /startup` probe exceeded ten seconds during cold instrumented
startup. The observer now treats only read-only `/startup`, `/ready`, `/live`
probe-process timeouts as unavailable, within the unchanged enclosing startup
deadline (200 seconds). POSTs and other data reads still propagate uncertain
timeouts; healthy aiohttp load timeouts and all latency/error budgets are
unchanged. Red reproduced the immediate abort, Green verifies no mutation/data
retry is introduced; 22 observer/fixture/budget tests pass. Raw preparation
failure: `build/mm-load/20261028-091854-fcd87f/result.json`.

The next standalone-fault attempt exposed a real availability defect before
fills were generated: fully acknowledged own-only levels could round downward
when OKX contract quantities passed through native float order-book rows.
Observed example: `5091.6496 * .01` became `50.916495999999995`, while the exact
own amount remained `50.916496`. The guard accepted positive rounding noise but
treated the negative equivalent as inconsistent depth, repeatedly canceling
otherwise qualified quotes. Both sides reproduce Red with native `OrderBook`.
Green applies the existing two-ULP noise bound symmetrically and discards the
entire noise residual; it never promotes it to outside liquidity. Own-only books
still wait, and a real deficit beyond the bound still fails qualification.
The 101-case guard/defense/bootstrap/observer selection passes. This requires a
new runtime artifact and fresh final gates. The standalone setup also now waits
for working quotes and committed recorder fills instead of assuming an atomic
instantaneous snapshot. Raw attempt: `build/mm-load/20261028-092152-4d5881/result.json`.

The guard-fix image passed 14 independent groups before the defense observer
assumed that `normal` sizing immediately enlarges every existing quote. The
original twelve cold quotes still totaled about 100, which was correct: smaller
safe quotes can remain until ordinary refresh. The fixture now explicitly waits
for about 200 before testing reduction, uses a declared 30-second functional-only
refresh and a 120-second paused-random step. Full load timers/budgets are unchanged.
The rerun passes about `199.9989913 -> 99.9997827`, preserves both partial fills,
checks delayed terminal proof, pause persistence, all modes and Stop. Synthetic
recovery is 8.49 seconds. Six native `Open order failed` logs correlate with
explicit `SubmissionBlocked` readiness fences during injected transitions; there
are no unexplained failures or last-price warnings in this case.

Compatible upgrade/rollback passes separately. Complete functional coverage is
assembled from those two passing reruns plus the 14 unchanged passes on the same
immutable image. The aggregation verifies identical base fixture hashes and that
the harness AST outside the corrected defense observer is unchanged from
`038c95656`; every runtime file still matches the image. It does not rewrite the
original failed run or claim a single successful full invocation. Component paths,
hashes and selected groups are recorded in
`build/volatility-defense/e2e-complete/result.json` (`single_invocation: false`).
Original observer failure: `build/e2e-offline/20261028-093825-b09348/result.json`.

The final-image probe passes 60 seconds at the unchanged offered workload:
p95 411.16 ms, p99 701.77 ms, 60/60 independent fills, matched positions/two
funding bills and peak RSS 497,135,616 bytes. Healthy runtime ERROR/CRITICAL count
is zero. Its order refresh counts differ in timing from the earlier probe, so
these two short observations are not claimed as a causal latency/churn benchmark.
The no-fill fault diagnostic remains necessary for the own-depth regression.

That diagnostic then hit an observer query which counted seeded historical fills
while waiting for exactly two new fills. The fixture already contains 100,000
historical rows, so the predicate could never succeed. It now uses the existing
`config_file_path <> 'load-history.yaml'` scope, without changing runtime,
workload or acceptance budgets. Eleven observer/budget checks pass. The failed
attempt is retained at `build/mm-load/20261028-101655-70517f/result.json`.

The following no-fill diagnostic sampled an empty working cohort during ordinary
refresh, so there were no baseline orders for its cancellation measurement. It
now waits (at most 30 seconds, with the same read load continuing) for twelve
working quotes and both normal policy states, then captures positions and orders
in one independent ledger snapshot before applying the stimulus. A persistent
quote outage still fails this precondition. No production timer, offered load,
latency threshold or runtime image changed. Raw observer failure:
`build/mm-load/20261028-102222-4fdb3f/result.json`.

The corrected no-fill diagnostic now passes its volatility window under the
unchanged read load: zero unexpected API errors, p95 304.85 ms and p99 637.74 ms.
Controlled input to first terminal proof is bounded by 2,372.81 ms spot and
3,377.81 ms perpetual, both below the unchanged five-second budget. No working
orders remain at the subsequent joint pause decision, so pause-only latency is
explicitly null. Both pause latches persist, stale candles produce no new
submissions, filled positions are retained and synthetic recovery takes 11.98 s.
Owner restart and queued-expiry fault windows also pass; final DB-fault and full
healthy acceptance are still pending at this point in the report.
Raw diagnostic: `build/mm-load/20261028-103000-f4a551/result.json`.

The first unchanged full gate stopped after warmup, before mixed/burst/soak:
6,000 scheduled reads and all 300 fills completed with zero unexpected HTTP
errors, but main/auxiliary p99 were 2,990.89/3,090.33 ms (p95 233.24/205.78 ms).
The minute-two cluster and event-loop max 3,888.50 ms failed the original
one-second p99 budget. The run remains failed at
`build/mm-load/20261028-103731-767771/result.json`; no threshold was relaxed.

A same-image, same-workload 300-second traced diagnostic then passes: p95
176.73 ms, p99 552.37 ms, all 300 fills and zero unexpected errors. Trace records
several full-generation GC pauses of hundreds of milliseconds but does not
reproduce or establish the cause of the earlier nearly-four-second stall.
Temporal overlap with large outflow expiry is not treated as causation. No new
runtime optimization or GC-policy change is claimed without a confirmed cause.
Trace/evidence: `build/mm-load/20261028-104629-c1dc2c/`.

The paired untraced 300-second diagnostic fails p99 at 1,351.40 ms with no
unexpected errors (`build/mm-load/20261028-105355-b8da44/`). Minimal bounded
GC-only timing reproduces a larger failure: p95 879.41 ms, p99 2,900.19 ms,
all 300 fills and zero HTTP errors (`build/mm-load/20261028-110324-aabc95/`).
A full collection lasts 2,125.57 ms wall / 1,031.79 ms thread CPU around elapsed
66.77 s, inside the slow second-minute cluster. These establish a real GC-related
stall; they do not establish that the new volatility feature caused it.
No global GC policy, thresholds or collection schedule have been changed.

Two initial immutable-tuple prototypes reduced object counts but barely changed
scan duration, so those implementations were rejected. An exact-string/JSON
prototype on the same 1-CPU amd64 image reduces retained tracked objects from
roughly 350,000 to 17 for 50,000 fills; median repeated full-collection time changes
from 130.98 to 74.48 ms. This isolated benchmark is not API-load acceptance.
Raw experiment: `build/volatility-defense/projection-gc-json-image.log`.

The candidate uses exact decimal text and an original-fee JSON snapshot for
rolling entries, decoding back to Decimal for accounting, expiry and duplicate
checks. Numeric quantum, JSON numeric equality, future reservations and half-open
windows are preserved. TDD also reproduces a latent aliasing defect: mutating
caller-owned fee metadata after insertion could silently alter the retained
identity. The snapshot fixes this. Two Red tests fail before the change; all 18
outflow/retention tests pass afterward. Commit `8512f3922` passes 1,805 native tests + 11 subtests, 237 PostgreSQL
tests, Swagger checks and 72 scoped synthetic replay cases. All 20 selected
runtime files match the rebuilt immutable image. A paired GC-timed diagnostic
precedes full candidate image/load acceptance.
No p99 resolution is claimed yet.


The candidate GC-instrumented 300-second diagnostic passes unchanged gates:
p95 168.86 ms, p99 784.74 ms; auxiliary p95/p99 145.30/829.78 ms; 6,000 reads,
300 independent/recorded fills, matching positions, two identified funding bills
and zero unexpected errors. Peak RSS is 446,574,592 bytes versus 500,023,296 in
the earlier GC-only run (about 51 MiB less). The maximum in-window full-collection
wall time is 298.33 ms versus 2,125.57 ms; 30 versus 27 collections are observed.
Neither GC policy nor load budgets changed. These paired observations support
the narrow optimization, but one run per image does not establish a general
latency improvement or replace the full release gate. Candidate image matrix,
healthy long load and final Demo remain open.
Raw candidate: `build/mm-load/20261028-113012-b12524/result.json`;
paired summary: `build/volatility-defense/retention-probe-comparison.json`.

The representation trades some rebuild/expiry work for a smaller retained GC
graph: observed maximum reconstruction changes from 2,990.30 to 3,606.13 ms,
and the same-time expiry maximum from 250.20 to 354.83 ms. These maxima are from
different runs and are not a precise isolated cost estimate. Ordinary outflow
advance p99 remains about 0.02 ms; existing history checks still report at most
three SQL statements for order pages and one for fill pages at all tested limits.
No per-quote database read or new cache/backend was introduced.


The candidate image now passes all 16 E2E groups in one invocation, including
all three scenarios × spot/perpetual/both, durable pause/restart and compatible
artifact upgrade/rollback. Raw report:
`build/e2e-offline/20261028-113625-5a5f11/result.json`.
Defense measures allocation 199.9989913 → 99.9996504, includes two partial fills
and a two-second delayed cancel ACK, preserves pause across restart/Start and the
original random deadline, and retains filled positions. Synthetic recovery takes
8.62 s on one-second fixture candles, not a real one-minute-candle recovery ETA.
The dedicated defense logs contain 401 INFO / 30 WARNING records and no
ERROR/CRITICAL or non-JSON lines. Twenty-six warnings describe guard transitions;
four upstream candle warnings occur during paused cold restart/Start with the
intentionally stale input. The latch remains closed and later recovery is tested.
No last-price fallback warning is present. Full healthy/fault load gates are now
running on this same frozen artifact; Demo still waits for local acceptance.


Candidate cold preflight (`build/mm-load/20261028-120342-d1654e/result.json`)
passes all six fault windows: feed, volatility defense, owner restart, queued
expiry, database delay and database failure. Its additional 90-second defense
window has zero unexpected API errors, p95 151.46 / p99 443.56 ms; input-to-first
terminal-cancel proof is bounded by 3,325.47 ms spot and 3,494.47 ms perpetual.
At joint pause no quotes remain, so pause-only latency is not applicable. Both
latches persist, stale paused candles admit no orders, positions remain held and
synthetic recovery takes 11.85 s. This cold diagnostic does not replace the same
faults following the full healthy release profile.

The final unchanged full profile is running at
`build/mm-load/20261028-121114-81c318/`: warmup 300 s, mixed 900 s, burst 90 s,
random soak 3,600 s, then fault windows. Its acceptance remains pending.


On the final full profile, warmup passes at p95 147.90 / p99 493.91 ms,
15-minute mixed passes at 178.42 / 517.77 ms and the 90-second burst passes at
180.50 / 480.64 ms. All retain the declared rates/zero-unexpected-error budgets.
The unchanged sixty-minute random soak is still running; these completed windows
do not close its acceptance item.


The full sixty-minute soak **fails** on the candidate (`20261028-121114-81c318`):
p95 259.42 / p99 1,058.81 ms; auxiliary p99 1,052.46 ms. All 72,000 main and
18,000 auxiliary requests finish. Main errors are one daily-statistics HTTP 503,
one daily-statistics timeout and the fill generator's `backlog_not_drained`.
Drain time is 10.13 s against 10 s. First/last five-sample median pool-checkout
p95 grows 13.36 → 31.40 ms, exceeding the unchanged 20 ms floor. RSS median grows
430.51 → 440.89 MiB (10.39 MiB, within 64 MiB); book backlog median remains 2.
All 4,888 cumulative exchange/recorder fills, fees, positions and two funding
bills reconcile. Soak generates 3,598 fills; all three profiles have observed
running intervals. No runtime HALT is observed across 58 minute samples.

Around minute 47, the scheduling-lag probe reaches 7,535.50 ms. The probe carries
missed scheduled beats forward, so this does **not** prove a single blocking call
or GC pause of that duration. No full tracer or GC-only callback was enabled in
this release run. A later bounded handler p99 is about 570 ms; client timing,
not that partial server metric, determines acceptance. The late read/API/pool
problem remains unresolved and must not be attributed to the already-improved
cold outflow cache without evidence. Post-hour fault windows were not run because
the runner correctly stopped on failure; the earlier same-image six-fault cold
preflight retains its diagnostic scope. Final OKX Demo remains unrun.

A narrow follow-up is under TDD/measurement: history pages currently allocate ORM
entities for scalar read-only responses and hold pooled connections while
formatting the page. Avoiding entity hydration and returning the connection
before formatting may reduce pool occupancy. It is not yet claimed to explain or
fix the late timeout cluster. No gate threshold or duration is changed.


PostgreSQL logs provide a concrete cause for the HTTP 503: at UTC
06:32:13.885 the daily original-currency fee aggregate hits the unchanged
statement timeout; its canceled parallel worker is logged immediately afterward.
This does not establish the cause of every late latency spike. Native controlled
history-page measurements on 100,000 orders/fills retain identical output and
3/1 queries: order CPU 13.65 → 11.42 ms and pool hold 32.12 → 27.13 ms; fill CPU
7.56 → 5.50 ms and pool hold 9.38 → 5.71 ms. Python peak allocation falls from
2,302,666 → 1,733,160 bytes for orders and 1,983,263 → 1,357,141 for fills.
Twenty counterbalanced ABBA measurements per implementation/method support
commit `a8da51633`; eight focused PostgreSQL tests pass after four Red failures.
These isolated improvements are not claimed to fix the full soak.

Commit `825bf5381` adds sanitized WARNING logs for unavailable admin requests,
including qualified exception type and the response's generated request ID,
without exception text or SQL parameters. Load error summaries now retain UTC
observation time and the server's error request ID. Four Red cases become Green;
34 focused API/cache/logging tests pass. A new immutable image/full verification
is required for these source changes. A separate controlled fee-query benchmark
is evaluating native typed JSON extraction and transaction-local worker limits.


The exact failing original-currency fee query was counterbalanced on PostgreSQL
14 limited to two CPUs/512 MiB, using 100,000 fills and identical results. Old
native-JSON extraction with parallel workers: median 252.47 / p95 424.01 ms.
Typed `json_to_recordset` fields with the existing worker policy: 160.60 / 184.74 ms.
Serial old/typed variants measured 514.31 / 344.86 ms medians and were not selected.
No parallel-worker setting, timeout, cache TTL or query budget changed.
Commit `53932a579` decodes each fee's token/NUMERIC amount once, rather than
repeating JSON extraction/casts across its aggregates. Nineteen daily/reporting
PostgreSQL tests pass, including DST/snapshot/multi-currency/rebate cases and
mixed JSON-number/string amounts beyond 28 decimal digits. The original failed
full load is the acceptance Red; isolated query improvements and existing parity
tests do not claim a Green hour. Full source/image verification is restarting for
this candidate. Raw benchmark: `build/volatility-defense/daily-fee-benchmark.json`.
The first isolated typed-SQL prototype had an alias error in its unchanged CTE;
it was corrected before any runtime query changed. Its failed benchmark log is
retained under `daily-fee-benchmark-first-prototype-failed.log`.


The reporting candidate passes all 16 E2E groups in one invocation at
`build/e2e-offline/20261028-141550-790d49/result.json`, including upgrade/rollback.
Its dedicated defense logs contain no ERROR/CRITICAL or non-JSON records; warnings
are expected guard transitions and four candle restarts under deliberately stale
cold input. All 23 runtime sources match the immutable image.

After E2E, all bot image references disappeared from the same Docker Desktop
daemon. The first load launch failed before setup at image inspection, not a load
SLO. The harness never removes images. The user confirmed other Docker cleanup/jobs
were stopped. BuildKit cache restored the exact image/config ID `795008ea…` and
registry manifest digest `4b7eed77…`; the registry address changed, not the artifact.
The completed E2E proof therefore remains valid. Original failed launch logs are
retained under `*-image-loss-failed.*` and `reporting-artifact-before-image-loss.json`.

The next 60-second diagnostic passes p95 90.17 / p99 404.55 ms, but cold fault
setup (`build/mm-load/20261028-191613-20c81c`) stops on one missing transport reply
while polling an accepted Start receipt. The runtime is running with no error or
incident and the last receipt is applying. No fault window had started.
Commit `22fc7e6c7` fixes the test-only observer: retry only a transport-missing
read of the same receipt within the original 60-second completion deadline.
It does not retry new mutations, accept definitive HTTP failures or alter the
separate ordinary read/command latency/error gates. Two Red cases and a preserved
HTTP-503 failure case become Green; all 20 focused harness tests pass.
AST comparison verifies every harness method outside `mm_command` is unchanged
from the passing image matrix, and all 23 runtime files remain identical.
The original E2E has no such missing receipt (it would have failed the old helper).
Final load evidence now records receipt transport retry counts, preserving these
observations separately from normal load-read errors. The cold preflight is being
repeated before the unchanged full load; no runtime image rebuild was needed.

### Follow-up: bound Admin API database concurrency

The exact `795008ea…` artifact completed cold probe
`build/mm-load/20261028-191347-1ad6e0` and all six cold faults in
`build/mm-load/20261028-192614-12f141`. Its full load attempt
`build/mm-load/20261028-193345-cd8d56` passed warmup (p99 421.45 ms) and mixed
(p99 561.65 ms), then failed burst p95 575.35 ms / p99 1,747.86 ms. All HTTP
requests returned 200 and Start/Switch/Stop met their budgets. The pipeline
stopped before the sixty-minute window. This is a latency failure, not a pass.

A cold traced burst `build/mm-load/20261028-200032-88bbe5` also failed latency.
It showed main-loop pool waits and JSON encoding, with concurrent outflow
rebuild. That cold run retained 50,000 active historical outflow rows, whereas
the warmed full burst had 645 outflow entries, so it does not prove a single
exclusive cause of the warmed failure. Tracing was lossy and intrusive; its
measurements are diagnostic, not acceptance. Full GC was approximately
140–236 ms in this run, insufficient to explain the entire multi-second tail.

Red: one real-PostgreSQL HTTP test reproduced five API reads competing for the
shared five-slot pool while two connections were held by background work. The
owner read timed out. Two focused native tests also failed before the dedicated
executor existed. Raw logs: `admin-capacity-red.log` / `admin-executor-red.log`
under `build/volatility-defense/`.

Green: database calls from `AdminAPI` now use a dedicated two-worker executor,
with the same engine/pool and preserved request context. Canceling an awaiter
cannot increase the number of running DB workers. Application cleanup joins
them off the main loop. Filesystem and connector work continue independently.
No SQL pool size, query timeout, load threshold, cache policy or `.env` changes
were made. This prevents the API alone from exhausting the shared pool; it is
not an absolute reservation against every combination of background work.

Focused verification: 18 native API/cache/logging/executor tests and nine real
PostgreSQL capacity/history tests pass. Full regression, new image/E2E, short
load and unchanged final load gates must be repeated before acceptance.

### Follow-up: avoid a second JSON traversal for history pages

Orders/fills now send the existing validated Pydantic model's JSON directly,
instead of building a JSON-ready dictionary and then invoking Python's JSON
encoder. This changes neither query shape nor decoded response values. Two
HTTP characterization cases pass before and after, covering 500-row pages,
exact Decimal strings (including scientific notation), UTC normalization,
Unicode/escaping, nulls and opaque cursors. An initial fixture incorrectly
expected fixed-point text for a Decimal that already serializes in scientific
notation; the fixture was corrected without changing the existing contract.
This is performance characterization, not a claimed Red/Green logic fix.

An isolated ABBA benchmark on the unchanged Linux deployment image, limited to
one CPU, measured final encoding at 500 rows: orders 4.780 → 3.097 ms CPU and
fills 5.976 → 3.557 ms CPU per page, with decoded JSON equality. Native results
were 3.319 → 2.098 and 3.966 → 2.339 ms respectively. These exclude database,
network, validation and concurrent scheduling; they do not close the burst/hour
gates. Raw results: `response-json-image.json` / `response-json-native.json`
under `build/volatility-defense/`. No new serializer dependency was added.

### Operator-requested environment pool ceiling

After the bounded-worker fix, the user requested an environment setting for
pool capacity. `DB_POOL_SIZE` now defaults to 5 and accepts integers 5–50 through
the existing dotenv/process loader; changes apply on restart. The minimum keeps
the existing capacity baseline, and the upper bound prevents an unbounded pool
configuration. API workers remain fixed at two and overflow remains zero. This
is an operational setting, not a YAML/API trading-policy override.

TDD: 13 new configuration/engine-wiring/dotenv-precedence cases failed before
implementation. All 50 runtime-configuration/executor tests pass afterward.
Logs are `pool-env-red.log` / `pool-env-green.log`. The errors do not echo input.
The environment example and Cloud Run template explicitly retain five; final
load gates will not silently increase capacity. Full regression/build started
before this request is interim evidence and must be repeated on the new source.

### Current-artifact short burst

Image `c924030a…` passes a cold, untraced 90-second burst with all enhancements,
volatility defense and operations reads enabled, keeping pool size five. Result:
`build/mm-load/20261028-211740-ea8413/result.json`; main p95 169.01 ms /
p99 434.25 ms, 20 completed main reads/s, 1 fill/s, zero unexpected errors and
no budget violations. Start/Switch/Stop application times were 5.629 / 7.574 /
0.660 seconds, and independent fills/positions reconcile. This cold run does not
replace the unchanged warmed mixed/burst/hour gate or prove exclusive causation
against a differently warmed/traced earlier run. Full sequential gates are next.

The same current artifact also passes the 60-second probe
`build/mm-load/20261028-212129-86d995/result.json` (p95 73.64 ms / p99
416.49 ms, zero unexpected errors) and all six cold preflight faults
`build/mm-load/20261028-212340-3b48f4/result.json`. The latter is an explicitly
diagnostic fault profile, not the healthy release gate. Full sequential
acceptance is running at `build/mm-load/20261028-213108-851dbd`; no partial
window is promoted to the hour or post-hour fault acceptance.

The full current-image sequence has now passed warmup (p99 422.39 ms),
mixed (p99 426.17 ms) and the warmed burst (p99 443.35 ms, p95 145.87 ms).
Each window has zero unexpected errors and no budget violations. The old
`795008ea…` warmed burst failed p99 at 1,747.86 ms; this new passing window
uses the same declared workload/budgets and five-slot pool. It does not by itself
prove an exclusive causal attribution or certify the uncompleted hour.
The unchanged sixty-minute Random soak is next, followed by aged fault windows.

The unchanged 3,600-second healthy window now passes on `c924030a…`: 72,000
main plus 18,000 auxiliary reads completed, zero unexpected errors, main
p95 142.69 ms / p99 459.19 ms, auxiliary p99 395.41 ms, and queue drain
0.180 seconds with no queued reads at generation end. All declared violations
are empty. This is the completed hour window, not yet the post-hour fault result.

Across 58 minute samples, first/last-five median RSS is 422.33 → 434.06 MiB
(+11.73 MiB against the 64 MiB budget), median pool-checkout p95 is
4.65 → 5.84 ms (below the 20 ms floor), and book backlog remains two.
At most 20 executor states and ten held executors appear in the snapshots.
This is bounded one-hour retention evidence, not a multi-day leak guarantee.

The full non-diagnostic `--profile all --enhancements --volatility-defense
--operations` run exits zero with `passed=true`, all four healthy windows and all
six post-hour faults passing, no command-receipt transport retries, and the same
`c924030a…` image. At the hour boundary, 4,884 exchange fills match 4,884 recorder
fills, account positions reconcile and two identified funding bills remain
deduplicated. All three profiles have verified running coverage in the Random
soak. Warmed burst Switch/Stop/Start take 3.593 / 0.568 / 3.190 seconds.
Final account compatibility on OKX Demo remains a separate uncompleted check.

### Final bounded OKX Demo compatibility

After all local gates passed, read-only account review confirmed Futures/Hedge,
the same account for both Demo keys, supported BTC spot/swap instruments, funded
minimum sizes and no pending orders. The persistent Demo DB had no bot tables
before the first launch; original private configuration files were not edited.
A separate Demo-only catalogue uses spot 200 / perpetual 400 quote allocation,
three weighted levels, strict guards and 1m/14 completed candles. Production
budgets and policy remain unchanged. Existing BTC long/short exposure is retained
using the existing initial-position adapter, without removing a drawdown policy
or reconstructing historic PnL. Pool size stays five.

The first attempt failed before any Start: the local GET startup observer did not
catch `aiohttp.ServerDisconnectedError` during cold boot. The service completed
bootstrap and exited cleanly when the observer stopped its container. That failed
result/log/source is retained under
`build/volatility-defense/demo/attempt-1-startup-disconnect/`. A focused Red case
reproduced it (one failure, two existing passes); all three cases pass after
handling connection failures only in the bounded GET startup probe. The same
120-second deadline, image, runtime and trading POST behavior are preserved.
No runtime fix or fresh load-gate claim is inferred from this observer change.

The successful retry records `passed=true` in
`build/volatility-defense/demo/acceptance.json`: twelve independently confirmed
recorder-owned post-only orders, three levels per side per market, tick/lot/minimum
and normal/reduced weighted-budget checks, fresh raw NATR and enabled fee floors.
Both real completed 1m candle identities advanced during observation. Stop
removed all pending quotes and did not reduce held positions; measured Stop,
including independent reads, was 1.241 seconds. No fills or volatility were
forced and no risk reset was used. The app container is stopped with exit zero;
PostgreSQL and its persistent volume are retained.

Demo service logs contain 89 INFO / eight WARNING records, no ERROR/CRITICAL and
no malformed JSON lines. Four warnings describe expected guard qualification/
reduced sizing; four describe upstream server-time refresh/network failures.
Subsequent private reads and all twelve order acknowledgments succeeded; the
result does not claim uninterrupted network/time synchronization.

All V5 checklist items are now verified on source `f52f6ee3f` / image
`c924030a…`: 1,829 native tests + 11 subtests, 243 PostgreSQL tests, Swagger,
16 image groups, scoped 72-case synthetic comparison on unchanged controller
source, cold diagnostics, the unchanged full non-diagnostic load profile and
bounded real Demo compatibility. This does not certify live LIFE liquidity,
Cloud Run/Cloud SQL latency, multi-day uptime or profitable execution.
