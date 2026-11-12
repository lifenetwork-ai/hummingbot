# Availability follow-up — 2026-11-11

Later follow-up: revision110/owner483 is paused on an explicit50037 receipt,
with no HALT. Local fixes and their separate qualification are in the
[quote/recovery record](../performance/mm-quote-replacement-latency.md).

Compact evidence: [qualification matrix](mm-availability-closeout-evidence.json).

Status: r7 passes native3,363+16, all724 PostgreSQL tests and seven real OOM cases.
E2E coverage is 36 passing cases in the full run plus one corrected harness target
on the same image; the original full execution remains failed. All four healthy
load windows pass, including the 60-minute soak. The full load execution failed
at a host fault-fixture assumption; its corrected six-fault target passes
on the same image. Neither original failed execution is promoted to passing.
That offline work did not deploy an image. Subsequent connected activation on
2026-11-12 is tracked in the
[Demo close-first record](../demo/cloudrun-close-first-baseline.md): revision110,
Start84,83 verified LONG close fills and two exact cold owner handoffs. Its
regular Random quote window is complete and fails99% (conservative Spot95.32%,
perpetual92.65%). This does not invalidate the scoped CLOSE/cold-handoff evidence
or convert incomplete/failed predecessors to passing.
The earlier Cloud Run revision108 check was running at control82, with no recorded HALT in the
read-only check at 17:41:50 UTC (00:41 local on November 12). The process/owner identity fields
report the same revision; these fields alone do not establish unchanged physical
PIDs. The production two-sided quote objective and
connected close-first acceptance are not inferred from that health observation.

## Fixes and evidence

1. Initial account settings had two owners: the script scheduled background
   writes and LifeMM independently awaited the same settings. LifeMM now opts
   into controller-owned setup; generic controllers retain their native setup.
   Native Red observed two leverage writes. The corrected packaged startup case
   sees exactly one initial write and quotes only after account confirmation.
2. Physical engine OOM after fill SQL flush exposed
   `recovery_position_application_conflict` when drawdown was disabled. Recovery
   incorrectly required the optional drawdown scope. The corrected path also
   accepts an actual controller with the matching managed session and market;
   mismatched explicit risk scope, session, market or missing controller remain
   blocked. PostgreSQL Red/Green and physical OOM requalification are retained.
3. The wider PostgreSQL suite exposed an unnecessary reduction-checkpoint read
   during Spot-only/empty executor startup. The original two-query/zero-query
   budget is restored. Existing perpetual holds still restore their remaining
   costs even when the closing policy is disabled. The query-budget and cost
   checkpoint regressions pass together (14 tests).
4. The opt-in load probe exercised active CLOSE OrderExecutors, which the
   existing observational `expected_positions()` did not support. It returned
   unknown scope; the metrics wrapper flattened that to an empty map. The TDD
   fix projects actual executed CLOSE quantity against the opposite owned hedge
   bucket, preserves receipt/in-flight transfer semantics and rejects excess,
   nonfinite, unmanaged or foreign-controller reductions. It does not mutate
   accounting or trading authority. The wrapper now preserves an explicit
   supported-scope flag, so unknown scope cannot pass as a verified flat account.
5. A non-tick-aligned SELL model price could strand a remaining one/two-lot
   position. The OPEN builder rounded the price upward and then shrank the
   closing amount against `remaining_amount * raw_price`, even when the level's
   actual allocation covered the rounded quote. The combined defense probe
   subsequently exposed the same budget loss when a passive book bound was
   applied only at pre-identity refresh. CLOSE now incorporates the resident
   maker bound and price tick before allocating quantity. Native Red/Green
   covers one and two lots, both hedge directions, unchanged identity/quantity through
   refresh, and rejection when the actual budget cannot cover one lot. The
   fee-inclusive breakeven check, owned quantity and original queued notional
   bound remain enforced. All 119 reduction regression tests pass;
   packaged LONG/SHORT lifecycle and nonflat rollback pass; full load qualification
   remains in progress.

All unknown send outcomes still require positive venue evidence; this change
does not retry an order POST, erase a risk latch or guess missing financial data.

The close-first load producer targets both hedge directions and uses the
authenticated fixture fee rate. It must observe actual CLOSE fills on both legs.
It cannot pass merely because the policy flag is enabled. A canceled quote
selected by the fill driver is reselected only after the fixture's explicit
`order_not_fillable` rejection; canceled orders are never filled retroactively.

The read-only `scripts/mm_reduction_baseline.py` prepares a reviewable remaining
opening-cost proposal. It requires complete, independently matched fill/fee
history, native financial replay, verified flat anchors and a supported opening
tail. It never writes the DB or grants closing authority. The proposal prepared
from the old stopped snapshot is already stale for the running account; it must
not be applied without a fresh stopped snapshot and venue/hash revalidation.

## Current local artifact

Base: revision108 deployment digest
`sha256:8bc394b76e47aeb768a12df26fac11a427fb018e81099d710a412c36ef83354a`.
Current r7 candidate:
`sha256:42f9108d030e318c70b21169b6b133a9d65723e4e17bcf3b3250aa828a40f123`.
The 14 staged Python modules match the packaged source manifest
in `build/mm-availability-closeout-20261111/image-r7/` (seven runtime modules and
seven supporting tools). Runtime hashes are unchanged from r6; only load
comparison/evidence tooling advanced. The canonical native selection passes 3,363 tests plus
16 subtests. The seven reduction regression files (119 tests) are now explicitly included in the Makefile's
canonical CI target, closing the omission of reduction-specific math,
controller, cost, valuation and automatic-recovery tests from that target.
The fresh full PostgreSQL suite passes 724 tests in 21 minutes on r7
(`postgres-r7.log` in the current build evidence folder). The full E2E run passes
36/37 scenarios; its sole failed host assertion passes separately after the
bounded correction below, on the same immutable image. Both native LONG/SHORT
closing cases pass nonflat old → new → old upgrade/rollback, preserving costs,
fees and exactly-once application; full closure restores OPEN quotes. Generic
upgrade also passes. The four healthy load windows and corrected six-fault
target pass. Earlier results retain their original scopes.
The subsequent submission-uncertainty correction is host harness logic, outside
that frozen manifest; all seven runtime source hashes remain unchanged. The full
run records its original harness hashes, and the corrected target records its
updated harness hash separately.

Physical OOM qualification passes all seven cases on the r7 digest, under a
1 GiB hard application quota with no swap. Six managed MM cases restore eight
acknowledged working maker orders spanning BUY/SELL on both markets, under the
current physical owner. Native fill identities, exact position quantity, quote
volume and original fees reconcile; no financial journal fill remains unapplied.
Session/control are preserved and no manual reset is performed.

| Kernel fault boundary | Result |
| --- | --- |
| API memory exhaustion, also killing its sibling engine in this run | Verified automatic resume and working quotes |
| Engine after venue accepted the POST, before its ACK reached the connector | Verified resume; affected client ID physically POSTed once |
| Engine after fill SQL flush, before COMMIT | Venue fill backfilled and applied once |
| Engine after COMMIT, before local recorder acknowledgement | Durable fill retained and applied once |
| Engine during held-position/checkpoint transaction | Financial transfer restored and applied once |
| Whole MM container/PID1 during fill flush | Docker OOMKilled + exit137; cold verified resume and quotes |
| Whole legacy V1 container without a verified MM scope | HALT retained, no resubmission; the rolled-back venue fill remains explicitly unresolved for operator recovery |

Evidence: `build/mm-oom/20261111-214231-df3b40/result.json`. Independent observer
failures in earlier diagnostic attempts remain retained; the r7 external
observer has zero SIGKILL retries. This finite matrix is not a proof that every
OOM, venue outage or arbitrary legacy history can recover automatically.

## Healthy load and financial replay

Evidence: `build/mm-load/20261111-225005-a13889/`. The same candidate runs with
2 application CPUs, a 1 GiB hard quota and no swap, independent fixture/DB limits,
100,000-row history, post-only enforcement, close-first, volatility defense,
managed local credentials and Google-only application access. Original latency,
rate, accounting and memory budgets are unchanged for the healthy windows. The
host is ARM64 and the Linux AMD64 image is emulated; these local measurements
are not a Cloud Run latency SLA. Close-specific fault timing is scoped below.

| Window | Duration | Read p95 / p99 | Unexpected errors | Budget violations |
| --- | --- | --- | --- | --- |
| Warmup | 5 min | 52.15 / 427.74 ms | 0 | None |
| Mixed | 15 min | 107.71 / 785.61 ms | 0 | None |
| Burst | 90 s | 383.72 / 655.47 ms | 0 | None |
| Random soak | 60 min | 71.85 / 541.38 ms | 0 | None |

All scheduled 20 reads/s complete. Fixed windows meet the declared update/fill
rates; Random transition admission gaps retain their original separate rate
scope. All 4,884 simulated fill identities, quantities, original fees, account
positions and two identified funding bills reconcile. There are 96 actual
simulator CLOSE fills (50 LONG, 46 SHORT), reducing 0.096 LIFE base in total.
An independent exact-rational replay of all 193 perpetual fills confirms each
maker close covers its allocated original opening and closing fees. Remaining
quantity, native volume, original fees and realized gross PnL match both hedge
buckets exactly (`final-closed-economics.json` in the current build evidence
folder). These are fixture results, not real OKX Demo fills.

The soak has 58 continuous samples, unchanged supervisor/engine/API PIDs and
16.81 MiB retained growth against 64 MiB; the maximum cgroup peak is 739.77 MiB.
Healthy-window logs retain 41 new probe-timeout records: warmup2, mixed12,
burst1, soak26. The longest recovery observed in mixed is 5.111s; soak's longest
is 1.280s. One recovery crosses a window boundary. All four windows have zero
ERROR-or-higher or service stop/restart records. These are supervisor observation
intervals; without finer quote/tick timing they do not establish the exact
duration of a trading interruption, or establish that none occurred.

Retained failures are evidence, not passing releases:

- Fill-flush OOM Red: `build/mm-oom/20261111-184129-afe1bf/`.
- Repeated OOM pressure also killed/refused an in-cgroup test observer. Process
  observation now uses a separate 32 MiB / 0.1 CPU, network-isolated sidecar sharing
  only the fixture PID namespace. This does not change the application's hard
  quota or weaken kernel, owner, economic or working-quote assertions.
- The initial 60s reduction probe had no closing fill and correctly failed:
  `build/mm-load/20261111-181726-058048/`.
- The 180s all-feature r3 probe produced 180 exact fills but correctly failed its
  held/active position gate because the observer did not support CLOSE. Its
  driver also skipped legitimate closes smaller than the fixed 0.1-contract
  chunk after quantization/repricing. The opt-in producer now fills the smaller
  positive remaining closing quantity; ordinary opening/default chunks, rates,
  latency budgets and all economic comparisons remain unchanged. No closing
  coverage is claimed for that failed run:
  `build/mm-load/20261111-195003-069dff/`.
- The r4 180s probe reconciled 180 fills, original fees and native held/active
  positions but failed its both-leg closing gate. A LONG close left two amount
  lots; subsequent SELL quotes were rejected by size rounding, preventing the
  next SHORT opening. The accelerated-refresh diagnostic retained the explicit
  `order_size_rejected` / `reduction_price_not_profitable` reasons and reproduced
  the same failure. These are failed runs, not acceptance evidence:
  `build/mm-load/20261111-201910-a2d0d6/` and
  `build/mm-load/20261111-211447-0e4d4b/`. Final status/state snapshots are now
  captured before teardown on failures as well as successes.
- The r5 combined volatility-defense/reduction probe also correctly failed its
  both-leg closing gate: moving a tiny planned close from its narrow model
  spread to the passive book bound consumed its queued notional before venue
  submission. It retained a one-lot LONG and did not open SHORT. This is the
  second Red described above, not a passing load result:
  `build/mm-load/20261111-212203-8a712f/`.
- The r6 combined probe produced 180 exact fills, including a full LONG close,
  a full SHORT close and a new LONG opening. It still failed the test's account
  position comparison: the simulator retained a flat SHORT row with `pos=0`,
  while the native API/active projection omitted the flat bucket. The strict
  fixture normalizer now omits zero exposure and rejects duplicate, nonfinite,
  negative or unsupported hedge evidence. Its Red/Green and the producer/closing
  evidence regressions pass together (20 tests). This failed run is retained and
  is not retroactively promoted to passing load acceptance:
  `build/mm-load/20261111-213121-1a0eac/`. The full release's unchanged 5-minute
  warmup will qualify the corrected comparator before the longer windows.
  A separate exact-rational replay of that retained run confirms both closes
  cover allocated opening and closing fees; remaining quantities, native volume,
  original fees and realized gross PnL match both native hedge buckets exactly.
  It does not replace the failed gate (`r6-closed-economics.json` in the current
  build evidence folder).
- An injected startup setup longer than a 15s Random step correctly withheld
  expired authority; the latency fixture now declares a 30s step. The separate
  ordinary 15s continuous-rollover regression remains required.
- The first full E2E run on the final source stopped because its startup
  assertion treated the expected `halt_kind=shutdown_pending` rollover fence as
  a technical HALT. The captured session was preparing, the engine was running
  and no technical reason was recorded. The corrected fixture waits only for
  that specific transition, requires the fence to clear before accepting quotes
  and retains the same 60s deadline. Its targeted Green observes four fence
  samples, one account write per generation, restored quotes and a clean Stop:
  `build/e2e-offline/20261111-194600-37d3e1/`. Other HALTs still fail immediately;
  a stuck transition still fails its deadline. The failed full run remains
  `build/e2e-offline/20261111-192253-bf9f00/` and is not declared a passing release.

- The current full E2E run hit a harness race in `submission-uncertainty`:
  `reconciling` was published before the worker's first GET, but the assertion
  demanded both immediately. Its retained ledger contains two later GETs and
  zero accepted orders/fills. The corrected host harness waits for both within
  the original single 60s deadline and fences the owner from the first pause
  observation. Four focused tests pass, including missing GET and changing
  owner negatives. The same-image packaged target passes with four busy POSTs,
  no automatic resubmission, stable owner and acknowledged Stop:
  `build/e2e-offline/20261111-222928-cc4c39/result.json`. The original full run
  remains a failed execution; all its other 36 scenarios pass.
  This target must not be presented as a passing single `all` execution.
- The full opt-in load execution passed all four healthy windows and feed
  recovery, then failed before volatility input: the old fault fixture demanded
  12 quotes. Close-first correctly held 10: six Spot OPEN levels, three perpetual
  SELL OPEN levels, and one BUY CLOSE reserving the entire remaining SHORT.
  All financial comparisons still pass and the runtime is running. The host
  fixture now requires all opening levels and exactly the owned closing
  reservation, including partials; missing/duplicate/under/over-reserved quotes
  fail. TDD retained four Red failures and 16 Green tests. The same-image six-fault
  target uses a fresh measured five-minute fill baseline and passes;
  the original `all` result remains failed. No runtime module changed for this
  fixture correction.
- The first corrected fault target
  (`build/mm-load/20261112-001830-e56893/`) retains its failure: the uniform
  input-to-cancel upper bound reached 6.467s for a tiny CLOSE, versus 4.355s for
  Spot OPEN and 3.514s for perpetual OPEN. Independently logged first `reduced`
  decisions put that CLOSE's terminal proof at 3.104s. Sizing reduction does not
  necessarily invalidate a small CLOSE immediately; spread restriction does.
  The extended fixture keeps the original 5s input bound for every OPEN, requires
  the first observed restriction decision and a 5s terminal bound for CLOSE,
  and preserves the older all-order input bounds separately. Extreme pause still
  cancels all working quotes. This is a documented close-first timing scope,
  not a retroactive pass for the old uniform measurement. One timing Red and
  19 focused Green tests are retained; a missing decision or late OPEN cannot
  pass. The second same-image target passes under the documented close-first scope.

## Corrected fault target

`build/mm-load/20261112-003615-e37be4/result.json` passes all six injected faults:
feed loss, volatility defense, owner restart, queued expiry, DB lock delay and
PostgreSQL loss. It first produces 300 matched fills, including four real
simulator CLOSE fills on both legs, and retains a nonflat LONG during the faults.
Its final exact-rational replay preserves native amounts, volume, original fees
and realized PnL through the restarts. No manual HALT reset is used.

For volatility, OPEN input-to-terminal maxima are 4.229s Spot and 4.483s perpetual;
CLOSE first-restriction-decision-to-terminal is 3.061s. The all-order perpetual
input upper bound is **6.529s**, which still does not meet the older uniform 5s
input measurement. Joint pause cancels all six Spot and four perpetual quotes
within 0.029s / 1.428s. Position quantity and both durable pause latches are
preserved; stale candles allow no submissions, and fresh data restores quoting.

Acceptance here combines the original four healthy windows and the corrected
same-image fault target. It is not a single passing `all` execution. The 19 host
fault-observer tests and four submission-observer tests are additional focused
runs after the canonical 3,363-test run; 22 cases are newly added, not an observed
new canonical total. Both host corrections are outside the frozen 14-module
image manifest; all seven runtime hashes remain unchanged.

Eight obsolete bot images were removed after qualification: six intermediate
availability candidates, the startup-only candidate and revision106. The current
candidate and revision108 rollback/base remain cached. No volumes or unrelated
project images were removed (`image-cleanup.json` in the build evidence folder).

## Remaining acceptance

The read-only Cloud log review from 10:43:25 through 14:40:56 UTC contained 241
warnings and no ERROR-or-higher records, service stop or engine restart. A second
review from 14:40:56 through 16:43:55 UTC contained 144 warnings and no
ERROR-or-higher records, service stop or engine restart: 124 no-quote observations,
19 probe timeouts and one submission guard warning. The boundary records overlap;
the two counts must not be added without deduplication. Close-first remains off
on revision108. These observations are not continuous engine/quote timing proof
and do not close the regular-profile availability gate.
The final 16:43:55–17:41:57 UTC Cloud review contains 62 warnings (59 no-quote,
three failed probes), zero ERROR-or-higher records and no stop/restart records.

- Completed on revision110: reviewed Demo remaining-cost baseline under
  audit242, actual fee-covered native CLOSE orders on both hedge legs,83
  LONG close fills and cold owner handoffs with exact costs/positions. No natural
  SHORT close fill is claimed; details and original venue fee proof are linked
  in the connected record.
- Measure the regular 30-minute quote objective. Cap/inventory-blocked or unknown
  observations are not evidence of working two-sided quotes.
- Broader connected fault injection remains separate. Trading under unresolved
  effects is an explicitly deferred policy, not a missing implementation to
  bypass while closing checkboxes. Real GCP KMS remains outside this local scope.
