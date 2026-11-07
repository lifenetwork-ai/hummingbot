# Runtime follow-up remediation

Date: 2026-11-07 (Asia/Saigon). Source phases follow
[this plan](../../docs/plans/MM_RUNTIME_FOLLOWUP_REMEDIATION_TDD_PLAN.md) and the
[read-only review](../README.md#historical-records) (Git: `reports/investigations/mm-runtime-followup-code-review.md`).
Source/native/PostgreSQL phases and subsequent image/load gates retain separate
scopes below. The [current combined image gate](../release/mm-runtime-followup-load.md)
includes both latest optimizations. No existing Demo, real exchange, Google/GCP
or production change is performed.

## R1 — Availability boundaries

Risk checkpoint/status collection and typed rendering now have a narrow protected
boundary. Unexpected view failures produce null observations with scoped incidents;
healthy observations clear them. Ownership/SQL failures and cancellation propagate.
Actual risk checkpoint writes and cleanup remain outside the optional boundary.

Stored outflow projection requires a valid policy, account/environment/pair binding
and integer coverage timestamp. Malformed stopped checkpoints remain retained and
the owner/control service stays available. A running intent with invalid stored
risk data uses the existing durable HALT before admission; raw checkpoints and
unrelated recovery fields remain intact. Required HALT-write failure loses
authority and propagates. Cold-HALT reconciliation is not bypassed.

The Stop path also uses protected drawdown observation capture before real cleanup;
a rendering error cannot prevent cleanup, while a real cleanup failure still
propagates. Semantic invalid-drawdown projections keep one continuing incident
instead of logging false recovery on every poll.

Fresh checkpoint incidents produce the English operator state **Risk data needs
review**, disable Start/Switch and retain Stop. Stale/render-only incidents do not
become cached risk authority. A null risk observation means unavailable, not disabled.

Initial native Red:12 failed/3 passed; extra checkpoint/Stop Red:7 failed;
operator view Red:2 failed/2 passed after correcting a test-only `actions` field
name. Thirty native + five real-PG cases are added. Related native87 and PG10
pass; focused suites overlap final broad results. Logs:
`/tmp/hbot-runtime-r1-red.log`, `/tmp/hbot-runtime-r1-extra-red.log`,
`/tmp/hbot-runtime-r1-operator-red-corrected.log`, `/tmp/hbot-runtime-r1-ui-green.log`,
`/tmp/hbot-runtime-r1-postgres.log`. Formatting/lint checks pass.

## R2 — One strict steady readiness traversal

A running session without listing bootstrap now performs only the final strict
readiness check. Preparing/bootstrap sessions keep qualification, the initial
bootstrap-permitted check, anchor/checkpoint writes and the final strict check.
Per-order and after-throttle checks remain fresh; no readiness cache is introduced.

Nine native cases: two Red failures/seven passes before the change; all pass after.
A real LIFE-controller fixture measures reference/exposure checks from two to one
per controller, with no steady session write. It verifies strict rejection, data
changes between calls, recovery, preparation changes during commit, bootstrap and
inactive sessions. Related native46 and PostgreSQL joint-send readiness11 pass.
This halves those traversals, not total engine CPU. Logs:
`/tmp/hbot-runtime-r2-red.log`, `/tmp/hbot-runtime-r2-green.log`,
`/tmp/hbot-runtime-r2-postgres.log`.

## R3 — Narrow fresh valuation and per-report hold reuse

Valuation readiness uses the existing executor's current filled-quote property
instead of constructing the full PnL/custom DTO. PositionExecutor's open-base
fallback still catches exposure awaiting transfer to PositionHold. DTO-only legacy
adapters retain their interface. Non-finite current/open/retained quantities now
return not-ready rather than being normalized away or accepted with a valid mark.
Fresh mark validation, new fills, held exposure, scope checks and critical SQL
failure propagation remain. No report from a prior tick becomes risk authority.

Unified reports pass the already-computed position summaries to performance
aggregation, following existing per-report executor DTO reuse. Standalone performance
calls still compute fresh values. Scope filtering, held fees, PnL and volume remain;
the next call observes changed prices and rejects stale marks. No resident cache,
SQL change, connector rewrite, framework, worker or Redis is added.

Eighteen new native cases: initial nine Red failures/four passes, then four held
non-finite Red failures/one pass. Related native56 pass. The main Make regression
target includes the new report tests. Correctness checks cover current/held exposure,
zero exposure, finite values, stale/recovered marks, fee/PnL/volume, scope filters
and no cross-call reuse. Logs: `/tmp/hbot-runtime-r3-red.log`,
`/tmp/hbot-runtime-r3-held-red.log`, `/tmp/hbot-runtime-r3-final-green.log`.

### Measurements and deliberately deferred work

[Raw evidence](mm-runtime-followup-evidence.json) retains the before/after sample
arrays and limitations. Seven alternating warmed pairs, 500 calls per sample,
compare original methods from `45707b06e` to current methods with separate equivalent
fixtures in one native process. Actual PositionExecutor/PositionHold methods use
synthetic connector/order inputs. These are component CPU timings, not engine p99,
SQL/REST savings, exchange performance or a new load acceptance.


- Valuation readiness, 1 resident(s): median CPU 24.93 → 4.10 µs per call.
- Valuation readiness, 12 resident(s): median CPU 256.87 → 26.98 µs per call.
- Unified held report, 1 resident(s): median CPU 27.10 → 20.13 µs per call.
- Unified held report, 12 resident(s): median CPU 195.30 → 113.71 µs per call.

The steady-admission preliminary seven alternating pairs used real LIFE controller
methods with a fixture provider (500 calls per sample); raw results are retained.
The robust claim is two reference/exposure traversals become one per controller,
not that total engine CPU halves.

JSON normalization is unchanged. Preliminary native medians for inner capture /
outer detach are about 0.010 / 0.005 ms with an empty account, 0.523 / 0.188 ms with
100 synthetic balances (~14 KB), and 5.065 / 1.829 ms with 1,000 balances (~139 KB).
Those are per-component samples, not production payload measurements. The copies
serve NaN rejection and mutation isolation; combining them is deferred without
representative live payload evidence. Existing detached-publication tests remain.

The first measurement fixture lacked two order metadata attributes; it was fixed
before measurement. This was a fixture error, not another runtime defect. The
valuation path with drawdown enabled does not explain the earlier six-probe load
where drawdown was disabled. No single old probe root cause is claimed.

## Frozen-source verification

Source is frozen at `504343b9b` after availability `45707b06e` and steady
readiness `1f4f19de6`. All recorded runtime
file SHA-256 values match the measured/current files.

- Full `make test-cloudrun-regression`: **2,780 passed + 11 subtests** (202 config,
  11 client performance, 1,814 runtime/model/connector/executor and 753 remaining
  strategy/core/guard cases), exit 0.
- `make swagger-check`: pass, exit 0.
- Full disposable PostgreSQL suite: **523 passed**, exit 0, 785.07 s.
  Log: `/tmp/hbot-runtime-followup-final-postgres.log`.
- Formatting/lint/pre-commit and changed-document relative links pass.

Logs retain Pydantic/AsyncMock and SQLAlchemy deprecation warnings; no test failure
occurred.
The five new PostgreSQL cases are included in the full suite, not added to its
count again. No Demo data change, new migration, API configuration override,
new dependency or process architecture is introduced.

A changed runtime image still needs its separately identified image/E2E/load
qualification before deployment. This source result does not inherit the earlier
accepted image's hour gate or establish that every historical probe cause is fixed.

## Subsequent image verification — 2026-11-07

New image `bf4e1f77…` separately passes the unchanged full load/hour/five faults,
four targeted E2E groups and enabled-enhancement90. Soak p99 306.91 ms, zero
unexpected healthy errors, 21.89 MiB retained growth and 4,885 exact simulated
fills/fees/positions. Three healthy probes recover without child replacement;
measurement and attribution limits remain. One unused image removed; all container
references/volumes retained. [Image report](../release/mm-runtime-followup-load.md),
[evidence](../release/mm-runtime-followup-load-evidence.json).


## Probe follow-up: fill autoflush — 2026-11-07

Review all three accepted-image episodes without broadening one sample into an
exclusive root cause. Warmup captures fill/checkpoint and an owner libpq wait.
Mixed overlaps a 1,215 ms asynchronous funding batch; its thread CPU includes
interleaved callbacks, and one sample is ordinary wallet parsing. Soak's selected
completed spans mostly precede failure, but raw loop samples at -208/+77/+342 ms
show bounded libpq waits around it. That is additional sampled context, not proof
of a continuous 1.05 s wait. The captured 13 ms GC finishes before failure.

A real PostgreSQL Red exposes three recorder flushes per fresh fill: Order alone
when checking existing status, Order/status/fill, then MarketState. The status
lookup only depends on the stable order identity/timestamp/status, not the pending
Order update. Suppress autoflush only around that lookup in `_store_fill`; the
next existing query flushes Order/status/fill with the original fresh owner fence.
Cleanup's existing validation query can flush the same batch before checkpoint;
its dependent sums still see the pending fill. Checkpoint and commit retain their
fences. Nothing is cached across operations.
Other history helper call sites retain their original behavior.

Two Red cases fail/5 safety cases pass before the change. Eight new PG cases
(including paired measurement) pass; related recorder/checkpoint/cleanup PG61 pass.
They cover known/unknown orders, identical-timestamp fills, duplicates/late create,
physical owner-session loss between flushes, final-commit rollback, and visibility
before ack. Native file-backend behavior remains in the final broad regression.

Seven alternating warmed real-PG pairs replay the original flush schedule as the
control: **3→2 flushes/owner checks**, **13→12 SQL statements**, p50 **17.23→15.12 ms**.
One after sample is slower (24.77 vs20.47 ms); all samples are retained. This is a
component measurement with synthetic connector tracking, not an MM/probe p99 claim.

Funding reader construction is ~0.27 ms in a warmed native dummy-config benchmark,
with no networking/task startup. Keep its lifecycle unchanged; adding a reader or
balance cache is not justified by this measurement. Dedicated owner checks and
synchronous durable recorder writes are not moved to a general background thread.

[Scoped evidence](../performance/mm-fill-flush-evidence.json). Runtime `e030cfcdc`
passes full native **2,780 + 11 subtests**. Full disposable PG: **530 pass / 1 fail**,
811.37 s. The sole failure is the older steady-fill statement-count assertion:
11 expected, 10 observed. Steady fills already have an OrderFilled status; this
change avoids the premature owner-check roundtrip there too. Correct that exact
expectation to 10, retaining its fill count/sum/persistence assertions. Rerun the
corrected benchmark and all eight new cases: **9 pass / 522 deselected**, 20.20 s.
This covers all 531 distinct PG cases across the full run and focused correction;
it is not a claim of a subsequent single all-green full-suite run. No runtime
change follows the broad regression. Native warnings include deprecations and
unawaited AsyncMock warnings; the logs retain them.

Logs: `/tmp/hbot-fill-flush-final-native.log`,
`/tmp/hbot-fill-flush-final-postgres.log`,
`/tmp/hbot-fill-flush-final-correction.log`. Image `bf4e1f77…` and its existing gate
retain their original source identity; no new image/hour or real-Demo acceptance
is claimed. Archive deletion is separate (`b78f0d25c`); historical evidence remains
available through the [Git lookup](../README.md#historical-records).

## Fill-flush image gate — 2026-11-07

Image `837e7997…`, build `9ca14a148`, separately passes the unchanged full local
load/soak3600/five faults, four targeted E2E groups after one restart retry, and
enhancement90. Soak p99 **360.68 ms**, zero unexpected healthy errors, **24.05 MiB**
retained growth, **4,884 exact simulated fills**. Eight probes recover (mixed1,
soak7); no healthy child replacement. The failed first 9.5s shutdown poll remains
unexplained and retained. Warmup/mixed improve versus `bf4e1f77…`, burst/soak and
probe count regress within unchanged budgets; no uniform system speedup or unique
probe cause is established. Image `f74d2a2a…` removed; candidate, rollback
`bf4e1f77…` and all ten container references/fourteen volumes retained.
[Image report](../release/mm-runtime-followup-load.md),
[evidence](../release/mm-runtime-followup-load-evidence.json).

## Residual probes: ownership reads — 2026-11-07

The increase from one to seven soak probes on `837e7997…` is not uniquely
attributed. Both runs create exactly 32/112/16/472 healthy orders in the four
windows. All seven soak failures are socket timeouts; six have a sampled engine
stack in libpq I/O within/adjacent to the request, one in executor PnL work.
Five sampled contexts have no additional cgroup throttling, and none has a
selected GC interval. This bounds those explanations without ruling out host
scheduling, unobserved work or Random trajectory/alignment differences. Selected
spans beginning after the timeout are not labelled its cause. Request-time CPU
samples, offsets and source hashes are retained in [evidence](../performance/mm-owner-read-evidence.json).

One confirmed mechanism is the fresh ownership read's separate transaction and
read COMMIT, performed synchronously on the engine loop. Scope Psycopg's
autocommit flag around that single SELECT, retain SQLAlchemy's logical commit,
then restore transaction mode. Keep the physical backend/advisory lock, query
contents and fresh control/version/risk checks; no cached authority or new
thread/process. Already-open SQLAlchemy/DBAPI read transactions retain their original behavior;
only idle reads change mode. Original write isolation stays unchanged. Read failure still
closes submissions. Invalidated sessions close permanently; release of a dead
session closes without reconnecting or attempting a new-session write.
The driver behavior is documented by [Psycopg](https://www.psycopg.org/docs/connection.html#connection.autocommit)
and the logical/physical distinction by [SQLAlchemy](https://docs.sqlalchemy.org/en/20/core/connections.html#understanding-the-dbapi-level-autocommit-isolation-level).

Corrected Red: 4 fail/2 pass on the original path; the first draft's nonexistent
store.load call was corrected before the valid Red and is not runtime evidence.
Real PG checks cover both isolation levels, atomic recovery-write rollback,
fresh Stop/version changes, lock preservation, query/physical loss, no reconnect,
dead-session release and bounded pool bookkeeping. Targeted final source:
**80 pass**, including **16 new cases** and all five loss-of-response modes. A controlled 450ms delay in the separate
read COMMIT makes the actual 300ms production probe time out at 302.3ms; the same
production health handler/probe returns 200 at 2.2ms with scoped read mode. The
test uses real PG and a local health app; it is not a full MM engine or proof of
the unique historical cause.

Seven alternating real-PG pairs in the frozen full regression preserve all 25
fresh SELECTs per round and reduce separate read COMMITs 25→0 for idle scopes.
The median of seven batch-mean read durations is **2.209→1.210ms** (~45% lower).
The preceding targeted measurement is 2.262→1.219ms. All samples are retained;
there is no timing pass threshold or whole-MM/p99 claim.

Reject the first SQLAlchemy-isolation-execution-options prototype (`2409579d5`):
200 reads accumulate 400 pool-reset callbacks on the process-lifetime connection.
Its timings and passing early tests do not qualify it. The corrected source
`d9488143d` changes only the documented driver flag, preserves isolation metadata
and passes the unchanged callback-count assertion. The first prototype's native
result and interrupted PG run (185 pass; intentional SIGINT) remain separate.
An intermediate dead-connection release failure is retained and resolved.
If restoring mode fails, discard the physical connection before closing: plain
SQLAlchemy close could return an uncertain mode to the pool. Another interrupted
full run retains one old permanent-closure assertion failure/418 passes. Its
three-second deadline was not loosened; the owner assertion now requires permanent
logical closure as well as a closed physical session. Other I/O modes still
require invalidation.

Frozen source `d9488143d`: native **2,780 + 11 subtests**, full disposable PG
**547 pass** (772.09s), formatting/lint pass. Native logs retain existing
deprecation/AsyncMock warnings. Log paths/hashes and source hashes are in evidence. No new image/hour gate or
real-Demo qualification is inherited; accepted image `837e7997…` is unchanged.


### Subsequent ownership-read image gate — 2026-11-07

Image `e00cef38…`, built from `ff6b85ded` with runtime `d9488143d`, separately passes
four targeted E2E groups on first attempts, enhancement90 and the unchanged full
baseline/Random soak3600/five faults. All 1,224 packaged hashes stay unchanged.
Soak p99 410.20ms, zero unexpected healthy errors, retention 28.59/64MiB, exact
4,884 simulated fills/fees/positions. Five engine probes in soak (previous seven),
one API probe in mixed and one engine probe between windows recover. Mixed/burst
improve; warmup/soak latency and retention regress within budgets. This does not
uniquely attribute the historical count increase or prove continuous quoting.
Bounded engine capture reaches 32 and suppresses four further context captures;
markers/sink limits remain disclosed. The full [image report](../release/mm-runtime-followup-load.md)
and [evidence](../release/mm-runtime-followup-load-evidence.json) distinguish this
from the component experiment and retain the prior image at Git `ff6b85ded`.
Remove unused `bf4e1f77…`; retain accepted rollback `837e7997…`, all ten containers
and fourteen volumes, with the existing actual Demo unchanged.

### Fresh write-fence identity projection — 2026-11-07

The write fence fetched all fourteen owner-state columns, including recovery and
session JSON, then ignored the payload. It now requests only the physical backend
PID while preserving fresh owner UUID/generation/advisory-lock predicates on every
call. It still checks before reusing a transaction's shared row fence. Observer,
admission and final-send checks retain the full fresh row. Same connection,
transaction restoration, rollback, loss deadlines and submission closure; no
owner cache, threshold/pool changes, migration or exchange mutation.

Nine new real-PG cases cover result projection, full-state compatibility, repeated
fresh checks inside a transaction, physical lock/generation loss, closed/invalidated
sessions, actual recorder flush rejection, bounded bookkeeping and paired cost.
Red: one payload-shape failure, four existing-safety passes. The first Green draft
has two failures: an old spy counted the public method rather than the common
reader; leading projection whitespace prevented the existing latency injector's
SQL-prefix match. Correct the spy and preserve the original full-query prefix;
do not loosen its deadline. Retain both failed logs in the
[portable evidence](../performance/mm-owner-read-evidence.json) under
`identity_projection_followup`.

Frozen source: targeted **87 pass**, native **2,780 + 11 subtests**, full PG
**556 pass** (836.05s). Changed Python hashes remain fixed through verification.
The load fixture times the common reader so full/projected ownership reads remain
represented; metric time excludes public guard-only rejections that execute no read.

| Synthetic payload per JSON field | Median of seven full-read batch means | Projected | Serialized result bytes, full→projected |
| --- | ---: | ---: | ---: |
| 0 bytes | 1.187 ms/read | 0.969 ms/read | 471→20 |
| 65,536 bytes | 2.871 ms/read | 0.967 ms/read | 131,543→20 |

Each pair alternates order and performs 25 fresh reads per batch. Both cases keep
every SELECT, same backend and zero pool-reset callback growth. This is a bounded
component test; the JSON sizes are explicit fixtures, serialized results are not
network byte counts, and batch means are not request p50/p99. Earlier targeted
measurements are also retained; no hard timing assertion chooses a winning run.

The latest engine traces also show observer/recorder work, driver waits and one
sample inside the test trace recorder's `_finish`; the API episode overlaps GC
and request work. Those samples are temporal context, not one exclusive cause.
No speculative thread move, trace-lock rewrite or GC tuning is applied. At that source-only phase, the new
source had **no new image E2E/hour load/Demo/GCP qualification**; accepted image
`e00cef38…` still contains `d9488143d`. Existing Demo and image inventory remain
unchanged; the disposable test database is removed.

### Reporting snapshot copy cost — 2026-11-07

`capture_reporting` recursively copied every previous reporting observation, then
used its existing JSON round trip to normalize/detach everything again. It now
copies the account/balance containers it edits and the positions/performance maps
it replaces entries in. Retained history is read-only until the same final strict
JSON boundary. No cache, connector threading, serialization-policy, API/schema,
trading/risk or publication-authority changes.

Eight new cases cover one traversal of retained history instead of two, balance
replacement/missing assets, detached live and prior positions/performance,
verified-account replacement, malformed observations and nonfinite JSON samples.
The initial draft includes one mistaken missing-timestamp fixture; its new-currency
short circuit did not exercise that comparison. Correct the fixture, retain its
failure, and verify the final test set on the original function: **Red1/7 pass**.
Source checks: early targeted46 (seven new cases at that point), frozen native
**2,788 + 11 subtests** and reporting/publication/API PG **36 pass / 520 deselected**.
The earlier full PG 556 remains attributed to `5c421b121`, not this new source.
[Portable evidence](mm-runtime-followup-evidence.json), key
`reporting_snapshot_copy_followup`, preserves failed drafts, hashes and all pairs.

| Synthetic shared-wallet fixture, two active controllers | Median batch mean wall ms/capture, before→after | Thread CPU ms/capture, before→after | Tracemalloc peak bytes, before→after |
| --- | ---: | ---: | ---: |
| 2 balances / 2 retained positions and performance rows | 0.08774→0.05168 | 0.08775→0.05169 | 24,898→31,882 |
| 100 balances / 200 retained positions and performance rows | 2.96243→1.26469 | 2.96054→1.26448 | 473,748→378,444 |

Seven warmed alternating pairs of 100 captures show about **41% / 57% component
CPU savings**. Both outputs match the original function and leave prior data
unchanged. Allocation medians are seven separate alternating pairs of 25-call
tracemalloc experiments; collection occurs before each allocation experiment.
The small fixture's peak increases **6,984 bytes**; the larger peak decreases
**95,304 bytes**. This does not establish uniform memory savings, process RSS,
retained growth or a leak fix. Batch means are not request p50/p99, and the fixture
sizes are not an observed production distribution.

The smaller case saves about **0.036 ms per capture**; this is not an explanation
for the previous second-scale probe observations. Other observer/recorder I/O and
trace/GC context remain unproven attribution. At that source-only phase, no new image/E2E/hour load/Demo/GCP
run was claimed; accepted image `e00cef38…` predated both changes. The subsequent
combined gate below qualifies a new image separately.


### Combined image qualification — 2026-11-07

Image `a6fe88c0…` (build `745a86e3c`) now packages both the write-fence projection
and reporting-copy changes. All 1,224 packaged hashes match through measurement.
Four targeted E2E groups, enhancement90, activity300 and the unchanged full
baseline/Random soak3600/five faults pass on first attempts. Soak p99 406.14 ms,
zero unexpected healthy errors, 24.26 MiB retained growth and 4,885 exact simulated
fills/fees/positions. Eleven soak engine probes and three mixed engine probes
recover without healthy child replacement. Burst p99 and measured probe count
regress versus accepted rollback `e00cef38…`; component savings do not establish
a uniform whole-system speedup or one exclusive probe cause.

The separate 300-second diagnostic records completed tick/controller cycles and
two-sided simulator order samples during both recovered probes. Five other
market point observations are non-two-sided near refresh, and one controller
gap reaches 1.583 s. These observations do not prove continuous quotes, remote
OKX timings or baseline-soak activity. Numeric quote-coverage policy stays open.
[Current image report](../release/mm-runtime-followup-load.md),
[portable evidence](../release/mm-runtime-followup-load-evidence.json),
[operator guidance](../../docs/fe-integration.md#reviewing-quote-availability).

After success remove only unused `837e7997…`; retain current candidate, accepted
rollback `e00cef38…`, all 10 containers and 14 volumes, including unchanged Demo.
Prior accepted evidence remains in Git `745a86e3c`, earlier failures in
`ff6b85ded`. No existing-Demo/Google/GCP/deployment operation is performed.

### Combined-image regression audit — 2026-11-07

The preceding soak's engine probes **5→11** and burst p99 **267.47→503.57 ms**
remain regressions in those measurements. Component benchmarks do not establish
a whole-system improvement. Review of 1,224 packaged source/config hashes finds
exactly two changed files: `bot_ownership.py` and `admin_observations.py`.
Fresh ownership checks, generation/session/advisory-lock predicates and final
JSON detachment remain intact; the changes do not introduce another DB read.
This source comparison does not assert that every binary/image layer is identical.

Run immutable images A=`e00cef38…`, B=`a6fe88c0…` in **ABBA** order, each with a
fresh disposable DB and a 120-second measurement. Same 100k histories, 20 main
and 5 auxiliary reads/s, 100 book updates/s, one synthetic fill/s, CPU/memory,
cache and acceptance limits. The common fixture adds five bounded stage timings;
its ownership hook selects the available reader on each version. That hook's
boundary differs slightly (old public guard versus new internal reader), so it
is not an isolated ownership-method benchmark. Offline certificate fixtures are
generated per run. Host scheduling and short, sparse incidents remain limitations.

| Order / image | API p95 / p99 (ms) | Engine probe episodes | Exact simulated fills |
| --- | ---: | ---: | ---: |
| 1 / A | 58.39 / 449.07 | 0 | 120 |
| 2 / B | 61.09 / 427.69 | 1, recovered in 1.187 s | 120 |
| 3 / B | 59.52 / 495.84 | 1, recovered in 1.056 s | 120 |
| 4 / A | 71.72 / 755.18 | 0 | 120 |

All four pass, with 2,400 main/600 auxiliary reads each, zero unexpected HTTP
errors and exact fill/fee/position reconciliation. Both B runs have a probe
episode; neither A does. Retain that adverse signal. It is insufficient to prove
or exclude a causal regression with two short samples per version. The old image
also has the worst API p99; blanket claims that either version is uniformly
faster are unsupported. Reporting capture medians are about 0.425 ms on A and
0.294–0.299 ms on B; publication-preparation p99 is 3.18–6.22 ms on A and
4.21–4.25 ms on B. These end-of-run stage distributions are rolling/cumulative,
not exclusively the HTTP window or proof of second-scale probe causation.

Code review still finds synchronous owner/recorder PostgreSQL waits on the
engine loop. Prior bounded stacks often sample driver waits; one retained SQL
span is 372.7 ms wall versus 1.6 ms thread CPU. This establishes a blocking path,
not a unique database/host explanation for all probes. Async `observe_once`
duration/CPU includes other work while it yields. Do not add nested spans or
attribute all of that inclusive time to status collection.

The reproducible defect is in **test instrumentation**: `IncidentBuffer` held a
shared producer lock across JSON decoding and sink callbacks. A delayed watchdog
flush could therefore block engine-side trace emission or even summary reads.
Six barrier-based regressions fail on the original code (**6 fail / 22 pass**).
Move decoding/output outside the lock after capturing bounded immutable history
and updating incident state. Preserve ring/byte/age/count limits, detached values,
tail capture and concurrent eviction/recovery without duplicated history.
Add reversed-publication-order analysis coverage and keep the GC callback test
inside an actual locked critical section. **82 targeted tests pass**; style passes.
The barriers prove the lock dependency, not its duration on a historical run.

The first post-fix 120-second diagnostic on the same B image and timing fixture
passes: API p95/p99 **54.46/258.09 ms**, 2,400 main/600 auxiliary HTTP200,
zero unexpected errors, zero retained probe transitions in the measured window
and **120 exact simulated fills/fees/positions**. The changed mounted fixture is
`load_incidents.py`; fresh offline certificate/identity fixtures also differ.
The 1,224 packaged runtime/config hashes are unchanged. This single short run
verifies fixture integration, not a causal latency gain or a replacement hour
gate. No new image is built and no image or existing volume is removed.

Decision: retain the two production changes and their safety checks; no new
production optimization, rollback, threshold change or architectural rewrite is
justified by this audit. Fix the demonstrated diagnostic lock dependency. Keep
both images and the long-run probe attribution item open. The diagnostic change
does not eliminate GIL/sink overhead or establish that previous stalls were all
test artifacts. Raw artifacts: `build/runtime-regression-audit-20261107/`;
portable evidence is under `combined_image_regression_audit` in the
[existing evidence file](mm-runtime-followup-evidence.json).

### Full load after incident-lock fix — 2026-11-07

The first unchanged full repeat on the same `a6fe88c0…` image **fails** in the
mixed window: main/auxiliary p99 **1,315.91 / 1,169.10 ms** exceed the original
1,000 ms limits. Warmup p99 394.79 ms passes. The runner exits 1 before burst,
Random soak and all five fault windows; no new hour/retention result exists.
There is no automatic retry, threshold relaxation or fresh production change.

All 24,000 main/6,000 auxiliary requests return HTTP200, and 1,200 exact simulated
fills/fees/positions reconcile. Warmup one/mixed six engine probes recover;
healthy logs have zero ERROR/CRITICAL and zero child lifecycle events. Four and
fourteen minute samples retain PIDs 1/38/39. Eight premeasurement ERROR events
are connector/executor `SubmissionBlocked` reports for four IDs absent from the
simulator ledger; their generic safety reason does not prove the precise earlier
control transition. All existing containers, volumes and image IDs are retained.

The slow minute ending at 480 seconds has p95 2,596.61 ms across many routes.
Retained API request spans reach 1,462 ms; engine ownership/SQL waits and event-loop
work overlap. Low thread CPU relative to wall time points to waits/descheduling
as part of the observed work, without uniquely establishing DB, host or fixture
causation. The short 258 ms diagnostic did not predict this cluster. Pause further
optimization until a controlled before/after witness is established; do not call
this run a whole-system improvement or claim the producer-lock fix caused all
of the latency increase. [Failed attempt](../release/mm-runtime-followup-load.md),
[portable evidence](../release/mm-runtime-followup-load-evidence.json), raw
`build/incident-lock-load-20261107/`. Earlier full-pass evidence remains in Git
`dfad4a4d1` at those release paths; its source/fixture scope remains intact.

### Quantile export work — 2026-11-07

Further investigation finds quota-throttling counter increases during parts of
the failed interval, while both roles perform work. This supports CPU contention
as retained context, without assigning aggregate throttled time to one role or
proving one cause. The existing metrics exporter runs in a thread in each measured
process. Its complete sorts share the GIL; moving work to that thread does not
isolate interpreter CPU contention.

The failed snapshot contains 54 per-role metric rings with 380,006 retained values.
The exporter fully sorts every ring to retrieve only p50/p95/p99/max every five
seconds. A deterministic synthetic operation-count test fails on the old function:
147,115 scalar comparisons for 12,000 values. Replace full ordering of long numeric
rings with NumPy partition at the same exact nearest-rank indices. Keep sorting
for rings up to 256 values, bounded immutable snapshots, the single worker and
in-flight job, five-second cadence, all sample counts and quantile definitions.
No production code, image, request/risk/authority logic, CPU/pool or test budget changes.

Eleven new cases cover nearest-rank boundary/parity, unchanged input, large-integer
precision and comparison-work bounds. Initial Red: **1 fail / 12 pass**; final
targeted instrumentation suite **80 pass**, style passes. The two extra boundary
cases at 256/257 are added with the measured short-ring branch. All numeric inputs
in the benchmark match the old function's output exactly.

Seven alternating pairs of three complete quantile exports use synthetic sample
values with the observed ring lengths. Final medians, before→after:

| Environment / fixture | CPU ms per export | Wall ms per export |
| --- | ---: | ---: |
| Native arm64 / 54 small rings, 1,080 values | 0.0772→0.1140 | 0.0771→0.1140 |
| Native arm64 / observed 380,006-value shape | 52.49→20.66 | 52.51→20.68 |
| Same amd64 image / 54 small rings | 0.1064→0.1609 | 0.1063→0.1607 |
| Same amd64 image / observed shape | 77.10→39.88 | 77.11→39.90 |

The small case increases about 0.037/0.054 ms per export; no uniform size-independent
gain is claimed. The initial all-array draft is retained separately; its small-ring
overhead motivated the bounded short-ring branch. A separate three-pair synthetic
worker/heartbeat experiment on the image records before p95 12.85–17.65 ms versus
after 3.66–4.15 ms. The native experiment has a third-pair outlier: after max 132.88 ms
versus before 91.66 ms. Keep it; timing is not a deterministic test assertion.

These quantify component CPU/GIL exposure, not a complete export/HTTP/DB benchmark,
memory-leak result or proof of the 2.6-second minute's unique cause. The direct
component savings cannot be equated to that whole request cluster. The unchanged
full workload must be checked separately. [Portable evidence](mm-runtime-followup-evidence.json),
key `quantile_export_followup`; raw `build/latency-cluster-fix-20261107/`.
