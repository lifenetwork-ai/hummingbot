# Owner publication timeout investigation

2026-11-04 (Asia/Saigon).

Investigate the pre-window engine exit on image `086454de`: PostgreSQL canceled the
owner-fenced `bot_state ... FOR UPDATE` publication at 06:48:59.915 UTC, then the
supervisor restarted the engine. Preserve the passing hour and this failed
preparation observation under their actual scopes.

- [x] Retain the exact statement, deadline, owner/control-version predicates,
  error/restart/bootstrap times and unchanged source/image identity.
- [x] Capture bounded pre-window PG activity/locks/blocking PIDs and transaction
  ages, API worker/SQL spans and engine/API stacks during the original preparation.
  Run only short disposable-network diagnostics; no new hour is needed to locate
  a pre-window failure.
- [x] Test the suspected wait path with controlled real-PG transactions, compare
  lock and non-lock controls, and separate reproduced mechanisms from historical
  attribution. Keep 2s statement/3s I/O deadlines and all ownership fences intact.
- [x] If a runtime defect is confirmed, demonstrate Red/Green and implement a
  narrow fix. Otherwise publish bounded findings and the remaining evidence gap.
- [x] Update the current acceptance register, preserve stopped Demo/23 fills,
  publish evidence and commit the completed investigation phase.

Diagnostics never reset recovery, mutate the actual Demo ledger, place exchange
orders or change production limits. All generated orders and credentials belong
to the existing offline simulator and disposable PostgreSQL.

Bounded investigation: three valid preparations, 1,836 PG samples, no reproduced
persistence error/restart; three new plus eleven related PG tests pass. A traced
1.182s publication COMMIT has eleven same-PID WAL I/O wait witnesses during
history insertion. No runtime fix is claimed.

- [ ] Attribute the original historical trigger using a contemporaneous
  wait/stack witness or equivalent host/storage evidence. The matching controlled
  reproduction below establishes a mechanism, not exclusive historical attribution.

[Report and evidence](../../reports/investigations/mm-owner-publication-timeout.md).

## Follow-up investigation

- [x] Fix the confirmed diagnostic defect that truncated engine/API trace files
  on supervised restart. Red: three OS-process restart cases lose the first
  record. Green: 58 related trace/analysis/timing/incident tests pass; opt-in and
  cumulative file-byte budgets remain unchanged. Only offline instrumentation
  changes; the trading-service image remains `086454de`.
- [x] Repeat the original preparation with server-side slow-statement/error and
  lock-wait logging, alternating traced and untraced runs. Distinguish the
  diagnostic collector's 200ms timeout from the runtime publisher's 2s timeout.
- [x] Correlate a failed publication with the exact backend, WAL activity,
  CPU/I/O pressure and runtime SQL span; retain blocker evidence where present. Use a separately
  labelled WAL-pressure control if the unchanged workload does not reproduce it.
- [x] Add Red/Green coverage and a narrow correction for any confirmed defect;
  retain failed artifacts and report unresolved historical attribution honestly.
- [x] Verify stopped Demo/ledger preservation, update evidence and commit.

Follow-up establishes the matching mechanism: with a per-container 8 MiB/s
PostgreSQL write limit and the original 100,000-row bulk seed, backend 322 waits
on WALWrite and the same publication SELECT/control version 2 fails with 57014.
The retained SQL span is 2,901.57ms. Other repetitions show lock timeouts behind
WAL-delayed COMMIT. The same limit without bulk history stays clean for 65s;
wal_init_zero=off does not remedy the fault. Seven unchanged-setting repeats
and the 500,000-row unthrottled control remain separate clean observations.

The original exclusive trigger is still unproven; the unchecked historical
attribution item above is deliberately retained. No shipping engine behavior,
durability or timeout setting changed. See the report's follow-up section.

## Offline history preparation

- [x] Use Red/Green to prevent fixture seeding while the service is running;
  cover failed Stop/release, remaining DB effects, failed seed/CHECKPOINT,
  changed intent, failed recovery and unexpected child restart without resets.
- [x] Preserve the verified outflow origin, stop the full service, seed and
  CHECKPOINT offline, then require normal startup with a new owner generation
  and fresh runtime-role metrics. Keep workload rows/rates/gates unchanged.
- [x] Repeat the 8 MiB/s PG control using the corrected preparation, then run
  the unchanged 300s load on the same shipping image. Retain any failed result.
- [x] Update evidence/instructions, verify stopped Demo and ledger preservation,
  and commit the completed phases.

Source validation: 13 initial Red cases, one separate Red for lost outflow
origin and six Red caller-wiring cases; **169 related native tests and seven
PostgreSQL compatibility tests pass**. All six maintained seeding entrypoints
use the shared offline preparation. The process experiment captures generation
after the planned restart; memory diagnostics require fresh role metrics.

Two corrected 8 MiB/s PG controls each seed 100,000 orders and 100,000 fills
with no application connection, owner lock or pending order. Normal restart
acquires generation 2; another 65s remains free of publication failure or
unexpected child restart. These are stopped-session diagnostics, not live-MM
acceptance under the imposed I/O limit.

The unchanged 300s workload passes on shipping image `086454de`: 6,600 measured
HTTP 200 responses, zero unexpected errors/stale, primary/auxiliary p99
113.94/36.87ms and 300 exact synthetic fills/fees/positions. Two isolated engine
probe timeouts recover without restart; no claim of zero slow probes is made.
A separate 120s slow-report test passes with one intentional SQL timeout,
successful fresh retry, zero stale replies, 120 exact synthetic fills and
unchanged engine PID/owner/session. Other converted long suites have common
lifecycle/wiring coverage, not new full-suite results.

Commits `7bdb5b6f6` and `2b4011556` change host-side test preparation only.
Runtime image, fences, durability and deadlines remain unchanged. Demo is
restored stopped with all 23 fills and four ledger hashes intact. That initial
short-verification phase did not include a new hour, actual Demo trade or deployment. Historical exclusive
attribution above remains open.

[Correction report](../../reports/investigations/mm-owner-publication-timeout.md#offline-history-preparation-correction),
[verification evidence](../../reports/investigations/mm-offline-history-preparation-evidence.json).

## Requested full-hour confirmation

- [x] Run the unchanged standalone 3,600s Random soak on `086454de` using the
  corrected offline preparation. Retain all preparation and measured-window
  failures; keep original rates, resource/deadline limits and acceptance budgets.
- [x] Verify continuous samples, HTTP freshness/errors, fill/fee/position ledger,
  memory retention and unexpected engine failures/restarts. Restore the stopped
  actual Demo with unchanged ledger hashes, publish evidence and commit results.

Completed: measured window 2026-11-04 10:47:14.268–11:47:14.268 UTC.
Runner artifacts: `build/mm-offline-history-soak-20261104T104559Z/`;
raw result: `build/mm-load/20261104-174603-b267d6/`.

**PASS:** 79,200 HTTP 200 replies, no unexpected errors/stale, primary/auxiliary
p99 260.39/77.27ms, 3,595 exact synthetic fills/fees/positions, all three Random
profiles and 58 continuous samples (maximum gap 65.50s). Retention growth is
28.15MiB against the unchanged 64MiB budget. No unexpected service failure,
publication failure or child restart appears in the retained logs. Planned
pre-window shutdown/restart and generation 1→2 are explicitly recorded.

Eight engine-probe timeout episodes recover; the longest observation interval
is 3.798s and maximum reported probe duration is 338.964ms. All eight derived
intervals have independent simulator fills plus local engine fill logs; seven
also have new simulator-accepted orders. This establishes some MM work during
those intervals, not every tick deadline or continuous two-sided quote coverage.
Per-tick/activity diagnostics were not enabled in this unchanged acceptance run.

Demo is restored stopped, owner available, no recovery block, all 23 fills and
four ledger hashes unchanged. Shipping image/settings are unchanged. This closes
the requested standalone-hour confirmation only; no new full R1/R2, actual Demo
trade, Cloud Run qualification or exclusive historical attribution is claimed.

[Hour report](../../reports/investigations/mm-owner-publication-timeout.md#full-hour-confirmation-after-offline-preparation),
[portable evidence](../../reports/performance/mm-offline-history-soak-evidence.json).


## Current-image MM/probe correlation — requested 2026-11-04

- [x] Reuse bounded activity/incident and independent venue sampling on unchanged
  image `086454de` with corrected offline preparation. Start with a 600s fixed
  follow_market/both diagnostic; retain original runtime/guard/load budgets and
  keep its opt-in instrumented scope distinct from the completed Random hour.
- [x] Correlate each captured probe failure with tick/controller start gaps and
  execution, submit/cancel outcomes/RTT and independently observed quote presence.
  Include losses, cadence, readiness/guard states and uninstrumented intervals.
- [x] Review measured jitter and runtime spans before selecting a fix. No
  avoidable runtime defect is established in this bounded run; publish the
  observer/controller overlap and recurring spot SELL invalidation as remaining
  leads. Do not invent a Red/Green runtime fix or change health/risk thresholds.
- [x] Restore stopped Demo and unchanged ledger hashes; publish evidence, update
  the checklist and commit. No actual Demo trading or deployment in this phase.


Result: **600s diagnostic PASS**, 13,200 HTTP 200/no errors or stale, p99
347.99/118.91ms and 600 exact synthetic fills/fees/positions. One recovered
probe is before measurement and one is inside. For the measured 318.874ms
probe, request-through-recovery tick/controller start gaps are at most
1,050.67/1,233.10ms; all five venue context samples show both markets two-sided
with 12 live orders. No unexpected engine restart/publication failure.

Whole-window jitter remains: maximum tick/controller start gap 1,237.52/1,570.78ms,
controller execution outlier 568.53ms overlaps a 992.49ms observer span without
exclusive attribution. Point-sampled two-sided coverage is spot494/597,
perpetual574/597, jointly490/597; 13 aggregate-empty samples form seven groups
with refresh or readiness context. Recurring spot SELL `spread_quote_invalid`
proposals are a separate optimization lead; component-level replay is needed
before changing sizing/spread policy. No zero-downtime/tick-deadline SLO is claimed.

Activity/venue files have zero drops; internal incident spans/context are bounded
and omitted-span counts remain explicit. The close summary from preparation is
not accepted as final measured-engine flush proof. Actual Demo is restored
stopped with all 23 fills and four hashes intact. Shipping code/image/settings
are unchanged; no new full release/hour/actual-trade acceptance is claimed.

[Diagnostic report](../../reports/investigations/mm-current-probe-correlation.md),
[evidence](../../reports/investigations/mm-current-probe-correlation-evidence.json).


## Spot quote size churn and controller outlier follow-up — 2026-11-04

- [x] Isolate spread, fee floor and size using a deterministic native cash/fill
  replay with stable reference/spreads/volatility. Tiny fill-driven inventory
  changes can invalidate original size despite a safe remaining resting amount.
- [x] Reproduce unnecessary BUY/SELL cancellation with Red tests before changing
  runtime. Reuse acknowledged in-flight entry data for quote-size validity only;
  ambiguous metadata retains full config size. No new cache, schema or setting.
- [x] Verify partial/late-fill quantities, missing/ambiguous entry metadata,
  genuine spread/fee/reduction breaches and unchanged full allocation/exposure
  reservations; run surrounding controller/executor lifecycle regressions.
- [x] Build a separately identified candidate and verify targeted lifecycle
  behavior plus a bounded matched diagnostic; retain failures and scope.
- [x] Review the retained controller568ms/observer992ms overlap with the new
  measurements. Attribute only paths actually observed; no speculative deadline,
  cache/pool or ownership-fence change.
- [x] Publish findings/remaining attribution limits, update checklist and commit.
  Actual Demo remains stopped; no maintenance reset or new trade is authorized.

Source phase: two original-code Red cases reproduce unnecessary BUY/SELL
cancellation after a small partial fill. The narrow fix reuses one acknowledged,
identity/side/pair/price-matched in-flight entry remainder for quote validity;
unknown/ambiguous/pending data retains the original size. Full risk and allocation
reservations are unchanged. Thirty-six new regression cases and the surrounding
controller/position-executor/orchestrator/strategy suite pass: **526 tests plus
two subtests**. Raw Red/Green/replay artifacts: `build/mm-sell-churn-20261104/`.
Candidate image and measured comparison remain pending; old-image gates do not
qualify this runtime change.

Verification completed: source `905bff7e3`, separately identified candidate
`354fec1e`, reviewed one-Python-file image derived from086454de. All parent layers
and1,315 other reviewed application files are unchanged. Final native regression
**528 plus two subtests**, including38 new cases; targeted LIFE lifecycle and
volatility-defense E2E pass. The candidate600s diagnostic passes13,200 HTTP200,
no unexpected errors/stale and600 exact synthetic fills/fees/positions. Fixture
and measuring-source hashes match the earlier baseline600s run.

Observed spot spread-invalid referenced executor IDs120→35; successful submit
and cancel returns each219→141; spot two-sided samples494/597→574/597 and jointly
two-sided490/597→566/597. These are proposal/call and point-sample counts, not a
universal latency/uptime guarantee. Guard trajectories and host scheduling differ.

Controller568.53ms outlier is not exclusively attributed; candidate maximum is
262.85ms in this sample. Tick maximum increases89.47→180.54ms even though p99
improves. Two candidate probes recover in replacement context, with real observed
short quote gaps. No claim that this patch fixes all timing outliers/probes.
Remaining35 spread-invalid references are not suppressed or all classified as bugs.

Actual Demo restored stopped on086454de,23 fills/four hashes unchanged; no active
Demo upgrade/trade, new candidate hour/full R1/R2 or production qualification.
[Fix/comparison report](../../reports/performance/mm-partial-quote-size.md),
[evidence](../../reports/performance/mm-partial-quote-size-evidence.json).

## Fine controller trace and candidate hour — requested 2026-11-04

- [x] Prepare a bounded180s diagnostic on candidate354fec1e using existing
  tracing primitives in a temporary test wrapper. Add reference/NATR, inventory,
  guard/checkpoint, action/refresh and snapshot/publication segments; preserve
  image, runtime behavior/deadlines and data/byte limits. No SQL parameters/locals.
- [x] Review retained component timing, thread CPU, nested spans and measurement
  loss; use Red/Green only for a confirmed additional avoidable runtime defect.
  Do not promote non-reproduction or temporal overlap into exclusive attribution.
- [x] If no further runtime correction is established, run the unchanged
  standalone3600s Random soak on candidate354fec1e with ordinary load fixtures
  and no extra trace instruments. Retain every failed attempt and original gates.
- [x] Restore stopped actual Demo and unchanged ledger hashes; publish evidence,
  update relevant checklists and commit. No new actual trade or deployment.

Fine180s diagnostic passes3,960 HTTP200 and180 exact synthetic fills. Measured
controller max51.67ms; cold NATR>400ms is pre-window. A seeded expiry pulse costs
290.23ms/283.17ms CPU; a separate publication223.97ms overlaps214.96ms GC on its
worker with small SQL spans. One402.89ms observer root remains incompletely
attributed. Session validation benchmark median1.04ms does not justify caching.
No extra runtime correction is claimed. The separate ordinary candidate354fec1e
hour completes on2026-11-05 (Asia/Saigon):79,200 HTTP200/no unexpected errors or
stale, primary/auxiliary p99 126.88/76.41ms,3,594 exact synthetic fills/fees/positions,
58 continuous samples/max gap65.50s and27.59MiB retained growth within64MiB.
Fixture/source hashes and original settings match the prior ordinary hour. No
unexpected service failure or child restart, including setup. Four measured
probe episodes recover; one additional probe recovers before measurement. All
four measured intervals have local and independent simulator-fill witnesses.
Two ERROR records for one locally spread-rejected SELL remain explicit; its ID
never reaches the simulator and subsequent orders/fills progress. The evidence
publisher initially rejects a copied pre-window-only ERROR assumption; its
reviewed correction changes no workload or original gate. Actual Demo restores
stopped on086454de,23 fills/four hashes unchanged. Historical exclusive cause,
continuous quote coverage, canonical rebuild and full R1/R2 remain outside this
completed bounded phase.
[Final report](../../reports/release/mm-partial-quote-final-soak.md),
[evidence](../../reports/release/mm-partial-quote-final-soak-evidence.json).
