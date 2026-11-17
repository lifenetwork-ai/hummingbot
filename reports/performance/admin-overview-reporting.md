# Admin Overview reporting qualification

Updated 2026-11-17. Contract, coverage, durable fees and the publication-read
optimization are implemented. The encoding-before-lock artifact passes all 989
PG cases, 4,033 native tests +16 subtests, scoped packaged lifecycle, the full
60-minute soak, six fault windows and real-SQL isolation. Resource gate C still
retains warmup/cancel failures, plus a new local paired submit-p99 failure that
stops requalification before the remaining pairs and soak. All earlier failures
and interrupted runs remain retained. This report does not approve
deployment or close connected Demo acceptance.

Current source adds bounded final-send priority and cooperative outflow clock
catch-up. The new 1,134-file artifact passes 4,074 native tests +16 subtests and
95 focused safety checks, all 250 affected PostgreSQL cases and the three
scoped packaged lifecycle checks. Its first resource pair fails only cancel p99; remaining pairs and canonical
soak are not started. New-image real-SQL reporting isolation also passes. The preceding priority artifact retains a failed tick-gap/cancel
pair. Older artifact passes do not qualify this latest combined source.

Scope: the four follow-ups in [the approved plan](../../docs/plans/ADMIN_OVERVIEW_REPORTING_TDD_PLAN.md),
based on PR #2 `c17c938670dd0ea1543467e6b38db20d5331a970` and dev `0f99cd28e`.
The merge and implementation were measured before commit on `codex/overview-reporting-tdd`;
the source fingerprint and image digests below identify the measured artifacts.
No external service, credentials, risk limit, HALT flag or trading authority was changed.

## Behavior

- Overview units follow the configured base asset. Contradictory asset/pair values
  become unavailable. Financial wire values remain exact plain decimal strings,
  including tiny fees/rebates, with no float conversion.
- Coverage separates eligible, restricted and unknown intervals over 1,800 seconds,
  with conditional and conservative fractions. Session/generation admission proof
  and funded-side evidence are required. Normal ACK/replacement waits remain
  eligible; missing evidence remains unknown. Bounded buckets represent a sampled
  interval lower bound, not proof of continuous quote availability. Existing
  lifetime counters remain separate and private histories stay out of HTTP payloads.
- Fees use reporting-only contribution identities, atomic aggregate/progress and
  repeat keyset sweeps in batches of 500. Late fills/attribution, crash boundaries
  and unknown COMMIT outcomes are replayable. An older usable publication survives
  a version rebuild and an old worker cannot downgrade it. Catch-up readiness does
  not claim complete exchange history; missing conversion/attribution stays explicit.
- Managed attribution uses captured `site:UID` bindings. Connector UID stays raw;
  missing/inconsistent site is not guessed. A bounded watcher discovers the current
  scope independently of UI polling. Reporting reuses the existing heavy DB lane,
  single flight, bounded caches and queue/SQL/lock deadlines. Catch-up yields at
  least 50ms; errors back off three seconds even during partial catch-up.
- Status waits at most 200ms for cold fees. Each read rechecks freshness; pending
  or failed refresh retains a last-good total labelled stale. A one-second forward
  tolerance covers query timestamps following the row clock; larger clock errors
  remain stale. Migration 022 adds optional reporting tables and indexes separately
  from financial metadata, without startup backfill or a new trading prerequisite.

## Functional evidence

Raw logs, hashes and all failed attempts are retained under
`build/overview-reporting-tdd-20261116/` (local generated artifacts).

| Check | Result | Scope |
| --- | --- | --- |
| Contract counterexamples | 10 RED /3 passing, then GREEN | Assets, mismatched pairs and exact HTTP decimal serialization |
| Final native regression | 4,033 passed +16 subtests | `native-encoding-final.txt`, including both Stop fixes, both bounded reads and encoding before the authority lock |
| Frontend | 120 files /610 tests, lint/typecheck/coverage/build passed | Generated contract and dashboard; no new browser E2E |
| Final contract drift | Backend Swagger and generated TypeScript checks passed | `swagger-final-encoding.txt` / `swagger-types-final-encoding.txt` |
| Final source and hooks | All 1,133 runtime file hashes match the measured image; hooks pass on all 63 changed files | `final-source-verification.json` / `hooks-final-encoding-all.txt`; no source drift after measurements |
| PostgreSQL suite | All 989 cases across 115 modules pass on the encoding source | `encoding-postgres-batches/`: seven fresh-process groups, no failures; earlier writer-source matcher failures/follow-ups stay retained |
| Full restored histories with blocked SQL | Two cases passed | 1,800 coverage buckets and 2,000 recovery observations per market, payload >500KB, unchanged 500ms status/control bounds, callbacks and owner-fence progress |
| Million unrelated rows + partial fills | RED 3,000 source visits for 501 results; GREEN 501 visits | Direct indexed seek, approximately 0.95ms source SQL; physical query work, not a trading latency claim |
| Projection durability | Passed | 100,000 exact fees; late keys/attribution; concurrent workers; crash boundaries; actual COMMIT request/reply loss; exact large+tiny SQL SUM; rebuild/downgrade controls |
| Reporting row-lock deadline | Passed | Real 55P03 at 250ms, then exact replay after release, unchanged source fills |
| Populated legacy migration | Passed | 100,000 existing rows unchanged; idempotent bootstrap; required indexes present; no startup backfill |
| Concurrent Stop readback race | 1 RED /4 negative controls passing, then 7 GREEN | Unsent exact-object proof, sent/acknowledged/replaced/nonterminal/unproven/error controls |
| Late terminal cancel ACK | Three terminal-state REDs and a metadata RED, then GREEN; all 41 tracker cases and 84 related PG shutdown/control cases pass | Newly received exchange ID preserved; late fills and duplicate handling remain active |
| Final packaged terminal proof | Passed | `packaged-terminal-ack-proof.txt`: delayed readback, concurrent terminal fills, random rollover, overlapping owner handoff and clean Stop |
| Final managed startup smoke | Passed; 30 seconds only | `20261116-150250-c58112`: strict setup checks, actual fee contributions and independent ledger; not a full resource gate |

The grouped PG run covers 964 original cases, plus two new projection deadline/
populated migration cases, seven Stop-race cases and one coupled real-tracker
shutdown case. The initial monolithic run
was interrupted at 7 failures /723 passes. One pending-intent benchmark lost its
idle-in-transaction connection during explicit GC and passes alone. Two endpoint
query-count fixtures included periodic reporting discovery; the fixture isolates
that producer while preserving the one-query assertion. Four pre-existing cold
recovery fixtures lacked required opposite-leg absence evidence; explicit snapshot
proof fixes those fixtures without changing trading semantics. Group 4 also retains
two full-history fixture JSON encoding failures and their successful targeted rerun.
No test deadline, financial invariant or query-count acceptance was relaxed.

The latest writer-source run covers all 987 cases in seven fresh-process groups.
Its two failures were instrumentation coupled to the previous SELECT column
prefix: one could not identify the blocked publication query, and one did not
inject the intended SQL sleep. Match the locked `bot_state` SELECT independently
of its projected columns; all three contention and all ten availability cases
then pass, preserving physical lock/timeout, rollback, health and ACK assertions.
`writer-postgres-batches/` retains both original failures and follow-up receipts;
this is not described as a clean first attempt.

The initial full packaged lifecycle run retains four positive-fixture setup
failures: rolling-refresh, feed-recovery, maker-entry and pending-terminal-ws.
Their Live ENV harness lacked the required Perpetual bundle. Validation correctly
refused Start. Four RED /six passing native reproductions became ten passing after
the host harness supplied dummy Live credentials; missing-key and Demo/no-Live-
fallback negative controls still reject. All four packaged reruns, managed-
environment and upgrade/rollback then passed. The host-driver-only change preserved
byte-identical service sources; the later shared runtime fixes were separately
qualified by all native tests, 84 related PG cases and final packaged shutdown proof.
The original full run is not presented as a clean full-suite run on the final image.

## Baseline preparation races

The original baseline (`build/mm-load/20261116-130203-c01f74`) failed before any
measurement. Immediate Start → Stop selected a pending native order for GET; its
create coroutine rejected it at the closed final send gate while GET awaited a
response. The simulator ledger contains zero accepted orders/fills/mutations,
but stale selection led to 51603 and incomplete cleanup. Strict preparation refused
history insertion, correctly preserving the unresolved runtime marker.

The fix rechecks existing terminal, exact-object, no-exchange-ID and no-transport
proof before each queued read and after an in-flight not-found response. Not-found
alone never proves cancellation. Sent/uncertain orders and unrelated errors remain
fenced.

A second failure on the healthy-depth fixture exposed a tracker ordering bug:
FAILED was followed by an already queued PENDING_CANCEL ACK on the same order
object. That resurrected a terminal order and invalidated the proven-unsent
predicate. Diagnostic `pending-cancel-native-diagnostic.json` records the exact
object, state and no-send proof. Three native terminal-state counterexamples fail
before the fix. The tracker now ignores only PENDING_CANCEL state after a terminal
state, while preserving a newly received exchange ID. Late trade updates still
apply and deduplicate normally; other order-state updates retain their behavior.
The new PG case couples the real tracker with owned shutdown evidence.

Load preparation now rejects unexpected service failures/restarts after its
initial Start/Switch/Stop sequence, before measuring. A recovered setup failure
cannot qualify as a clean baseline. Both final comparison artifacts carry the
same two runtime fixes; baseline has no Overview feature code. Original failed
attempts and diagnostic artifacts remain retained.

## First full-run immutable artifacts

Both are local Linux amd64 images derived from the verified native runtime.
All 132 native source hashes match its ABI. The frozen image matches 1,133
packaged Python/SQL/OpenAPI hashes. All 1,133 packaged files matched the workspace
at the first full-run freeze, including the corrected host drivers. A subsequent
publication-read fix below requires a new candidate. Local registry manifest pins
are cached before their temporary loopback registries close.

Candidate fingerprint: `41786408618338a8525130b6743ca493aef65ead862ec456565e8668fc73ebc6`.
Config ID: `sha256:1046a1e3bb9b70a2a1e642250dee9061d8dc75adeb61f66af56548df7253690e`.

- Baseline: `127.0.0.1:55667/hummingbot@sha256:102dbda394a3c2b030cfe238afe49ce796b040690357fb7a216efd1b03b601a2`.
- Candidate: `127.0.0.1:58941/hummingbot@sha256:14dfa321c6b8d009ef685ccd07686c9fc1cd4608f8bbccf2173653ee0888341f`.

Original resource failures below used candidate config `f6e915a0…`, manifest
`81f65d7a…`, fingerprint `5b37b6a5…` and baseline manifest `d6e0e781…`.
Their pins and exact host sources are archived separately. They are not results
from the final two-fix candidate above.


## Resource qualification (open)

Predeclared comparison: three sequential baseline/candidate pairs, identical
120-second workloads, 100,000 historical rows, managed credentials, 2 CPU /1,024MiB.
Native tick duration/start gap and submit/cancel call-return p99 may not regress
beyond `max(10% of baseline, 10ms)`. Missing samples remain unqualified. Preserve
counts and distributions; call-return timing is not continuous quote or terminal
cancel proof. Paired windows do not overlap functional regression work.

Next gates are packaged Overview reporting isolation with two real SQL timeouts
and an independent durable fill witness; canonical warmup 300s, mixed 900s, burst
90s, Random soak 3,600s and existing fault gates on 2 CPU /2,048MiB. Existing
workload/rate/latency/ledger/ownership limits stay unchanged. Diagnostics cannot
substitute for release acceptance. All DBs are disposable and all exchange traffic
uses the isolated simulator; no real Google/OKX secret or local `.env` is loaded.


### Retained paired failures

All six 120-second windows completed on 2 CPU /1,024MiB with 100,000 historical
rows, 20 reads/s, approximately 100 market updates/s and one fill/s. Candidate
contributions were nonzero. Five load runs passed all declared budgets; candidate
3 failed command acceptance p95 (799.66ms, limit 500ms). This remains a failed run.
The strict paired calculation also fails submit p99 in pairs 2 and 3; it is not
closed by passing average throughput or other percentiles. No margin was changed.

For pair 2 the slowest candidate submit spans 570.03ms versus baseline 377.07ms.
Approximate wall/monotonic alignment places about 537ms before simulator creation
and 33ms afterwards. Creation is not socket receive: this first segment includes
client guards/scheduling/network and simulator scheduling. It does not prove
reporting caused the delay, or isolate pure network RTT. Original per-order events,
SQL/queue/CPU/event-loop distributions and cgroup throttling counters are retained
in the reports and `paired-submission-segments.json`. No unique cause is established.
Independent SQL fault and canonical soak checks continue without changing this
failed paired/command gate or treating them as replacements.


### Earlier packaged Overview SQL isolation

The final isolated run (`build/mm-reporting-isolation/20261116-135438-e8a1f3`)
passes on the earlier immutable application image (`81f65d7a…`), 2 CPU /1,024MiB, managed local KMS
and 100,000 historical fills. It records two actual PostgreSQL 57014 timeouts
(about 1.50s each), an independently observed committed fill at first-fault +1.13s
before that query finished, zero unexpected HTTP errors, approximately 100 market
updates/s and one fill/s. Engine PID, owner generation and session stay unchanged;
no HALT occurs, fees recover fresh, the independent ledger matches and Stop is clean.
This is bounded fault evidence, not universal CPU/DB isolation or a p99 guarantee.

Two original failed attempts remain retained. The first used the original baseline's default one-level book; the conservative
self-quote filter moved markets to `market_liquidity_unqualified` before the SQL
fault. A host helper was added to restore an explicit L2 baseline while preserving
legacy thin-book baselines (two interface tests RED, then GREEN). Subsequent old-
baseline runs still had one level: their running phase is not evidence that an
L2 control fixed the initial guard condition. The second collected its
DB witness 364ms after the fault finished, which correctly failed the strict
observation interval despite an exchange fill inside it. A single prewarmed
independent psql backend and a conservative log-time cutoff remove Docker/backend
startup from the fault window. Two native process-lifecycle controls and the full
packaged rerun pass; no assertion or SQL deadline changed. These host-fixture
tests and the exact 900/900 bucket oracle are included in the final 4,033-case
native regression. Isolation is being repeated on the final candidate.

Canonical warmup has passed (300s, read p95 132.62ms /p99 335.78ms, command accept
25.41ms, approximately 20 reads/s, 100 market updates/s and one fill/s). Its mixed
window failed as detailed below, so burst, full Random soak and later faults did
not run in that attempt. The failed paired submit/command gates remain recorded.


### Healthy-depth qualification (new fixture scope; earlier results retained)

Original load commands used `--external-book-levels=1` by default. The initial
canonical run failed mixed fill rate: 878/900 fills (0.97556/s, allowance one missed
fill). Read/command/market-rate budgets passed; Perpetual guard evidence shows
`empty_or_filtered_depth`, while Spot retains independent depth. This is not a
native parser or guard failure. The approved healthy reporting comparison needs
market data that remains independent of our known/cached quote uncertainty.

The new explicit two-level case declares 100,000 base tokens per external level.
Fixture `ctVal=0.01` requires 10,000,000 SWAP contracts; Spot wire quantity stays
100,000. Native parser + Cython order book + unchanged quote guard reproduce the
legacy 100,000-contract (1,000-base) thin case and the declared-base case. The
former remains unavailable when recent own quantities exhaust it; the latter
retains known external depth. No production guard, quote allocation, risk limit or
acceptance threshold changes. These results cannot replace the original thin-book
failures. Exact old host sources and hashes are archived for replay.

The first new runner omitted the explicit two-level flag; metadata assertion
caught levels=1/base_quantity=null after baseline and stopped before candidate.
That failed orchestration receipt is retained separately. The corrected runner
requires explicit levels=2/base_quantity=100000 on all six windows and records its
own results. Canonical uses existing `--keep-going` to collect every unchanged
gate; a violation still makes the final result fail.

### Final-image paired results: not qualified

All six explicit healthy-depth windows completed. Every candidate passes the
individual load budgets. Baseline 2 fails read and auxiliary latency budgets;
the unchanged relative p99 gate also fails four comparisons. Passing candidate
throughput does not close either failed gate. Full per-pair distributions, counts,
resource/fixture hashes and absolute/delta values are in
`normalized-paired-comparison.json`.

| Pair | Baseline read p95 /p99 ms | Candidate read p95 /p99 ms | Individual budgets |
| --- | --- | --- | --- |
| 1 | 51.11 /203.21 | 135.11 /554.35 | Both pass |
| 2 | 857.83 /1,625.68 | 133.47 /307.53 | Baseline fails |
| 3 | 53.76 /538.64 | 137.21 /748.43 | Both pass |

| Failed relative comparison | Baseline p99 ms | Candidate p99 ms | Delta ms | Allowed increase ms |
| --- | --- | --- | --- | --- |
| Pair 1 tick start gap | 1,027.74 | 1,234.38 | 206.64 | 102.77 |
| Pair 3 tick duration | 5.80 | 40.91 | 35.11 | 10.00 |
| Pair 3 tick start gap | 1,021.81 | 1,239.73 | 217.93 | 102.18 |
| Pair 3 cancel call return | 46.87 | 65.40 | 18.53 | 10.00 |

All six deliver 2,400 successful reads, 120 fills and approximately 100 independent
market updates/s per window. Candidate cgroup peak is at most 780,828,672 bytes
under the unchanged 1GiB limit. Snapshot/checkpoint sizes rise from roughly
19KB/5KB to 83KB/59KB; full-history construction has separate >500KB tests. These
are local amd64-on-ARM simulator measurements, not native Cloud Run qualification.
Submit/cancel p99 has only 22–26 observations per component window and represents
call return, not venue execution or continuous quote availability.

Baseline 2 (`20261116-151439-f3f306`) has worker queue p99 319.68ms and event-loop
lag p99 1,069.08ms. Self-recovered probes, simultaneous engine/API stalls, sampled
stacks and cgroup counters are retained, with a compact extract in
`normalized-baseline-2-stalls.json`. Those baseline observations cannot establish
Overview causation; a sampled stack alone does not isolate the delay. No unique
cause is established. No threshold was changed and no failed window was discarded.
Independent SQL isolation and canonical soak use the same final image; their
outcome cannot erase these failures.

### Final-image SQL isolation: passed

`build/mm-reporting-isolation/20261116-153027-7ac943` uses the final candidate,
2 CPU /1GiB, 100,000 history rows, explicit two-level base-normalized depth and
managed local KMS/offline credentials. Two real 57014 timeouts take 1.505s and
1.509s. An independent committed-fill witness arrives 0.778s into the first query,
before its timeout. All 120 fills match the independent ledger, market progress is
100.007 updates/s, unexpected HTTP errors are zero, PID/owner/generation/session
are unchanged, no HALT occurs and final Stop is clean.

There are 17 stale Overview fee observations, followed by a usable fresh value;
unprovable financial history remains labelled incomplete. The harness asserts
both degradation during the fault and recovery afterwards. Its legacy
`stale_read_count` and `transient_timeout_recovered_fresh` fields describe the daily
error/retry mode, not Overview freshness; the retained
`overview_fee_observations` and explicit Overview recovery assertion are the
relevant evidence here. This wait fault holds one API DB connection; it does not
model arbitrary CPU-heavy queries or qualify all Cloud Run workloads.

### First full-image canonical load: failed

`build/mm-load/20261116-153335-333726` uses the same candidate, the canonical
2 CPU /2GiB limits and the explicit healthy-depth fixture. Overview contribution
counts are asserted after each window, with independent ledger checks. Completed
windows have no budget violations:

| Window | Duration s | Read p95 /p99 ms | Reads/s | Market updates/s | Fills/s |
| --- | --- | --- | --- | --- | --- |
| Warmup | 300 | 134.10 /449.94 | 20.00 | 100.002 | 1.000 |
| Mixed | 900 | 149.74 /596.75 | 20.00 | 100.003 | 1.000 |
| Bursts | 90 | 145.93 /378.87 | 20.00 | 144.395 | 1.000 |

The unchanged 3,600-second Random soak completed and failed `read_p99`,
`auxiliary_read_p99` and `pool_wait_growth`: read p95/p99 373.06/3,690.54ms,
zero unexpected HTTP errors, approximately 19.99 reads/s. Its 58 minute samples
are continuous (largest gap 65.75s), retain PID 1/31/32 and pass memory/thread/FD
growth gates. Retained cgroup growth is 27,226,112 bytes against the unchanged
64MiB budget. The first/last-five pool-checkout p95 medians rise from 2.40 to
20.83ms, exceeding the 20ms gate. All 4,885 exchange/recorder fills match, with
matching positions and two identified funding bills. Overview contributions are
4,884, with backfill/partial history labelled by their existing contract.

Timestamped `probe-intervals.json` retains every observed failure/recovery pair:
warmup 1, mixed 5, bursts 0 and soak 45. The healthy windows have no structured
ERROR/CRITICAL or child-restart event. This is probe/process evidence, not proof
of uninterrupted two-sided quotes; the latency failures remain failures.

Snapshot/checkpoint sizes reach 919,269/872,196 bytes. The feed fault passes; the
volatility-defense fault then fails its clean-read assertion with two HTTP 503s
(`/orders?limit=500` and `/statistics/daily`). Later faults do not run in this
attempt. All logs remain retained. Random step admission gaps use the existing
observed-rate scope; fixed mixed traffic enforces the one-fill/s gate. These
canonical results are separate from the hard 1GiB paired windows and cannot close
the failed relative p99 comparisons.

### Follow-up: private checkpoint transfer on reporting reads

The publication query transfers and decodes the full private history before
Python/HTTP filtering. An actual DBAPI JSON-decoder counterexample fails with
728,488 bytes crossing that boundary, while seven missing/null/scalar/legacy
controls pass. The new query removes only `_coverage_history`, `_coverage_buckets`
and `_overview_recovery` from the liquidity object in PostgreSQL. It preserves the
full durable checkpoint and the default owner/restart read, the public Overview,
unrelated fields and one-query semantics. No retention or recovery evidence is
deleted. This proves avoidable read work, not unique causation of every p99 spike.

The first implementation's JSONB operator-precedence error is retained as a failed
attempt; explicit parentheses correct it. All eight boundary cases and four
real-SQL HTTP/quote cases pass: decoded payload is below 64KB, exact private state
remains readable, and held-query/timeout Stop/owner progress is unchanged. The
full 4,033-case native regression passes again. All 115 publication-consumer cases
across 27 PG modules pass in three fresh-process groups (47/34/34), including
control, credentials, history plans and state read projections. The eight added
cases increase cumulative coverage from 974/114 modules to 982/115 modules; this
is not a new monolithic run of every PG case on the latest source.

The separate publication candidate differs from the first full-run candidate in
only `hummingbot/model/bot_state_store.py`; all 1,133 packaged files match its
frozen workspace, with no native ABI change. Its fingerprint is
`1f9b7e652630e90c4528a131f9181a69c56eba5f31a17de730485d24c0cbe0a0`, config ID
`sha256:62c0a7fcca9f4d6ccff3478a8df48ecb2ef107e7e0730935950fe401bee1ad54`, manifest
`127.0.0.1:56873/hummingbot@sha256:ac5608108388c9fb1e1c4c7b2e78815858691e4e9e79da0a94af03c2d8b04eb7`.
Baseline remains the same `102dbda3…` artifact with the shared Stop fixes. Packaged
shutdown proof, managed lifecycle and upgrade/rollback all pass on the new image
(`publication-packaged.json`). The schema check and all hooks on 60 explicit
files pass; generated FE contract sources are unchanged. Earlier failed
measurements remain failed and cannot qualify the new source.

### Publication candidate resource results: not qualified

Six more unchanged windows completed (`publication-paired-comparison.json`).
Baseline 2/3 and candidate 2 fail absolute read/auxiliary latency budgets; baseline
2 also fails generator backlog. Tick duration, tick start gap and cancel call
return meet the declared relative margins in these pairs, but submit p99 fails
in pairs 1/2. No margin, rate or assertion was relaxed.

| Pair | Baseline read p95 /p99 ms | Candidate read p95 /p99 ms | Absolute result |
| --- | --- | --- | --- |
| 1 | 52.01 /240.24 | 136.46 /339.70 | Both pass |
| 2 | 1,301.07 /2,085.86 | 271.57 /1,135.97 | Both fail |
| 3 | 706.36 /2,328.31 | 144.55 /570.93 | Baseline fails |

| Failed submit comparison | Baseline p99 ms | Candidate p99 ms | Delta ms | Allowed increase ms |
| --- | --- | --- | --- | --- |
| Pair 1 | 225.34 | 355.72 | 130.38 | 22.53 |
| Pair 2 | 595.52 | 1,614.43 | 1,018.90 | 59.55 |

The new SQL isolation run `20261116-174900-d95640` passes: two real 57014 timeouts
at 1.506/1.511s, independent durable fill inside the first timeout, all 120 fills
matching the ledger, stable PID/owner/session, no HALT and clean Stop. There are
17 stale Overview observations followed by fresh recovery; the incomplete
financial-history label remains explicit.

During the paired follow-up, host inspection reports Battery Power at 100%;
before the new canonical window it reports 99% discharging and hardware
`ExternalConnected=No`, including after the operator reports connecting power.
The exact AC/battery transition time for preceding windows is not established.
This is a measurement confound, not proof that battery mode caused the failures.
Only the four current harness containers are running; no orphan test workload
was found. Power observations are retained in `host-power-observations.jsonl`.

The unfinished new canonical run `20261116-175209-9b4da8` is interrupted for this
hardware precondition and cleaned up, with no completed measurement window.
`publication-power-interruption.json` distinguishes that intervention from an
application failure. A complete new 3,600-second soak has **not** passed. Resource
acceptance remains open; every completed failed window above stays failed.

AC is subsequently verified by both `pmset` (`AC Power`) and hardware
`ExternalConnected=Yes`. A separate `ac-publication-qualification.json` scope
repeats the same artifact, fixtures, workloads and margins. The driver records
power before/during/after each run with nominal ten-second polling and interrupts
the test if it observes external power loss. Results and source timestamps are
retained separately in `host-power-ac-observations.jsonl`; the prior transition
time and failure causes are not inferred from the new power state. This scope
is complete but does not qualify resource performance, as detailed below.

### Monitored AC reader-candidate results: failed

All six hard-1GiB windows pass absolute budgets, with no observed power loss.
The same relative margins still fail in three comparisons:

| Pair | Baseline read p95 /p99 ms | Candidate read p95 /p99 ms |
| --- | --- | --- |
| 1 | 50.31 /241.44 | 135.37 /371.82 |
| 2 | 51.49 /207.72 | 133.68 /348.47 |
| 3 | 52.99 /542.97 | 134.37 /331.08 |

| Failed comparison | Baseline p99 ms | Candidate p99 ms | Delta ms | Allowed increase ms |
| --- | --- | --- | --- | --- |
| Pair 1 submit | 225.81 | 254.97 | 29.16 | 22.58 |
| Pair 2 submit | 262.92 | 330.05 | 67.13 | 26.29 |
| Pair 3 cancel | 30.75 | 73.36 | 42.61 | 10.00 |

`ac-publication-paired-comparison.json` retains counts/distributions and matched
fixture/resource hashes. Tick duration/start-gap comparisons pass; failed submit/
cancel comparisons stay failed. The monitored SQL isolation
`20261116-182453-054335` passes independently.

The monitored canonical run `20261116-182800-63bcf8` passes warmup
(133.43/412.92ms read p95/p99) and mixed (149.85/678.84ms), then fails bursts
(581.54/1,417.51ms and auxiliary p99), despite 20 reads/s, 144.24 market updates/s
and one fill/s. The remaining partial soak is interrupted after identifying
avoidable writer read work. `ac-publication-writer-followup-interruption.json`
records this intervention; a partial soak is not a passed hour or an application
crash. A complete final-source hour is still required.

A predeclared diagnostic control runs two pairs of the **identical baseline
artifact** on AC (`ac-baseline-control-qualification.json`). All four absolute
windows and both relative comparisons pass. This does not waive the failed A/B
gates or prove one cause, but provides no basis to dismiss them all as measurement
noise. The control is not a replacement release qualification.

### Follow-up: publisher reads the entire old checkpoint unnecessarily

`publish()` previously reads the full old status under its row lock, although
guard history uses only `mm_guard_observation`. Two actual DBAPI-decoder tests
(one/two publishers) fail at 729,035 bytes while their guard-transition,
concurrent/repeated-publication and authority assertions already pass. Eleven
other boundary/fence controls pass in that RED run.

The new SQL projection returns just that guard observation for object statuses,
preserving other legacy status kinds, the full session, `FOR UPDATE` and every
owner/generation/control-version/acknowledgement predicate. The new complete
snapshot, heartbeat and command outcome are still committed as before. Full
owner/restart reads retain all private histories. Owner polling already uses its
existing thin authority query; cold history restoration remains unchanged.

All 21 targeted PG cases pass (13 boundary/writer/fence cases, six state-read
projections and two real-SQL HTTP/Stop isolation cases), followed by the full
4,033 native tests +16 subtests and passing latest-source evidence for all 987 PG
cases across 115 modules. A new frozen artifact is replayed below in packaged/
resource qualification. Known byte-work reduction is not a claim that every latency
regression is resolved.

### Writer artifact before the encoding-lock follow-up

The writer candidate has source fingerprint
`7269f0aa05d1bbce550b3f8d58a1be0cc0ab304638fb14f7234130897f7ab298`,
image configuration ID
`137db04695ed518877b2a4c8fa9485ec97e471007880ecddf48c2a4827e255d0`
and local immutable manifest
`127.0.0.1:58817/hummingbot@sha256:ab6caa3af4cadd11c21b0010d16fe9a73b672bf2b2d44558f3ee1f56460c1713`.
All 1,133 packaged file hashes match the frozen workspace. Relative to the prior
publication-filtered image, only `hummingbot/model/bot_state_store.py` changes.
The baseline manifest remains `102dbda394a3…`; all earlier artifacts/results remain
separate. All three scoped packaged runs pass: shutdown proof, managed lifecycle
and upgrade/rollback. This does not claim a new clean run of every E2E scenario.

The new monitored AC component measurements retain five absolute passes and one
auxiliary-read p99 failure. All three submit-p99 comparisons fail; pairs two and
three also fail cancel-p99 comparisons. Tick and tick-start-gap comparisons pass.

| Pair | Baseline read p95/p99 (ms) | Candidate read p95/p99 (ms) | Relative failures |
| --- | --- | --- | --- |
| 1 | 52.55 /294.63 | 138.42 /703.16 | Submit 182.42 →248.57ms; allowed increase 18.24ms |
| 2 | 52.21 /196.82 | 133.84 /306.32 | Submit 214.53 →301.53ms; cancel 43.18 →75.82ms |
| 3 | 56.97 /739.35 | 137.23 /978.13 | Submit 197.51 →407.48ms; cancel 43.01 →119.40ms; candidate auxiliary-read p99 also fails |

`ac-writer-paired-comparison.json` preserves sample counts, exact margins and raw
paths. The fixture requires one fill per second and approximately 100 market
updates /20 HTTP reads per second.
Native submit/cancel observations measure call returns, not terminal exchange
effects or continuous quote availability. No budget has been increased.

The final-image Overview isolation (`20261116-203107-75e076`) passes with two real
57014 timeouts, an independent committed-fill witness during the first wait,
120 exact ledger fills, unchanged PID/owner/session, no HALT and clean Stop.
Seventeen Overview samples become stale then recover fresh; the generic daily
stale fields in this report are not Overview counters.

Canonical `20261116-203414-2224d7` passes the 300-second warmup (read p95/p99
133.66/446.09ms). During mixed load, a recorder fill callback receives 55P03 while
taking its financial `FOR SHARE` fence on `bot_state`. Persistence is poisoned,
the engine restarts and the independent fill comparison fails. The later
`CredentialCryptoError` follows the test's deliberately unavailable local key
provider; it is not evidence of an invalid key. Mixed runtime samples retain
the phase/PID change. Burst and the 3,600-second soak are not reached. The live
application/PostgreSQL logs and a disposable-fixture DB dump are retained under
`writer-canonical-*incident*`. This does not identify the historical blocking
transaction or attribute every earlier tail-latency spike.

### Encoding before the authority lock

Two controlled PostgreSQL RED cases hold encoding of a >500KB status payload.
The old publisher has already locked `bot_state`, so the actual financial fence
and a concurrent Stop each receive a lock timeout; 13 existing boundary tests
pass. Prepare the large JSON before opening the owner-fenced transaction, then
merge only the small guard/error metadata under its unchanged row lock. Owner,
generation and control-version predicates still run after encoding, so a Stop
that wins during preparation rejects the stale publication without writing its
payload or ACK. Guard events, full checkpoint, heartbeat and command outcome
remain atomic; no lock mode, timeout or persistence-failure policy changes.

All 30 focused PG cases pass, including both new reproductions, full checkpoint
and concurrent guard accounting, authority rejection, contention/health and real
SQL isolation. Retain an intermediate new-test failure on a legacy null status,
corrected to assert exact preservation of the preceding status. Full native
regression passes again (4,033 +16 subtests, `native-encoding-final.txt`), followed
by all 989 PostgreSQL cases across 115 modules in seven fresh-process groups,
without failures. The new artifact completes packaged and resource checks with
the retained failures below. C remains open.

The encoding candidate has source fingerprint
`4f8758158030deaf79640861d91bfb25b945b9637724bbc13055e85adb6c7fcd`,
image configuration ID
`686855541fdd65637a427bcbf9066c3c437ab0fa8fbd346a549a113d3655284e`
and local manifest
`127.0.0.1:58675/hummingbot@sha256:59ca59fe3b11d0f10dcb06a7b21df8de9a7f5dde61ef588f67464a813cdbfe16`.
All 1,133 packaged hashes match. Its shutdown-proof, managed-lifecycle and
upgrade/rollback scopes pass. The same complete canonical load runs first on
this artifact to revisit the financial-fence incident, followed by the unchanged
component comparisons and SQL isolation; the reordered execution changes no
resources, windows, fixture or acceptance margin. Results are retained in
`ac-encoding-*` and canonical `20261116-214515-137c33`.

The complete encoding-source canonical run is retained with these results:

| Window | Duration | Read p95 /p99 (ms) | Result |
| --- | --- | --- | --- |
| Warmup | 300s | 2,983.73 /5,127.91 | FAIL: main and auxiliary read latency; no HTTP errors |
| Mixed | 900s | 259.71 /650.28 | PASS |
| Burst | 90s | 168.85 /452.70 | PASS |
| Random soak | 3,600s | 151.24 /387.10 | PASS, including unchanged resource/retention gates |

The soak completes 19.999 reads/s, 99.944 market updates/s and 0.99833 fills/s,
within the declared thresholds. Its cumulative independent ledger has 4,884
exchange/recorder fills with exact fee/unit/position comparison, two identified
funding bills and three daily snapshots. Reporting has 4,883 contributions at the
post-window observation; that asynchronous as-of count is not claimed to be the
complete exchange history. Final status/liquidity checkpoints are 905,532/864,892
bytes. Retained cgroup memory grows 26,173,440 bytes (24.96MiB), below 64MiB.
All 58 minute samples preserve supervisor/engine/API PIDs 1/32/33; maximum sample
gap is 65.94 seconds. These samples and the ledger do not prove continuous quoting.

All six injected-fault windows pass: feed, volatility defense, owner restart,
queued expiry, database delay and database failure. Those deliberate disruptions
remain separate from healthy latency. Healthy windows have no structured
ERROR/CRITICAL or scheduled child restart. Timestamped `probe-intervals.json`
retains 5/9/1/19 recovered probe intervals in warmup/mixed/burst/soak, maximum
2.521/2.520/1.159/1.199 seconds respectively, with no unresolved healthy starts.
All 19 soak intervals concern the engine. They do not identify a unique stall
cause. Warmup's broad route tails coincide with measured API-loop lag p99
2,757.90ms/max 3,468.91ms; the known encoding-lock mechanism is not presented as
the unique explanation for those tails. The canonical run correctly exits failed
because warmup violates unchanged budgets.

All six 120-second component windows pass individual load budgets on 2 CPU /
1,024MiB. Canonical long windows above use 2 CPU /2,048MiB; they do not constitute
a one-hour qualification under 1GiB. The baseline is unchanged and both artifacts
include the same two Stop fixes.

| Pair | Baseline read p95/p99 (ms) | Encoding candidate read p95/p99 (ms) | Native p99 comparisons |
| --- | --- | --- | --- |
| 1 | 51.54 /176.80 | 134.48 /283.55 | Tick/gap/submit/cancel pass |
| 2 | 52.25 /175.71 | 134.39 /320.39 | Tick/gap/submit pass; cancel 41.69 →125.46ms fails, allowed increase 10ms |
| 3 | 51.91 /176.29 | 135.29 /337.34 | Tick/gap/submit/cancel pass |

Every tick, tick-start-gap and submit comparison passes. Retain the pair-two
cancel increase of 83.77ms and its sample counts in
`ac-encoding-paired-comparison.json`; no margin is changed after measurement.
Submit/cancel observations measure call returns, not terminal effects or a
continuous quote SLA.

Final SQL isolation `20261116-233857-678f4d` passes: two real 57014 timeouts,
independent committed-fill witness during the first wait, 120 exact ledger fills,
unchanged engine PID/owner/session, no HALT and clean Stop. Seventeen Overview
samples become stale then recover fresh. Its generic daily stale counters are
not Overview counters.

The final resource scope retains 711 external-power observations, all reporting
AC, from 14:45:15Z through 16:42:06Z. Polling is nominally every ten seconds;
maximum observed within-phase gap is 10.210s. This is sampled source evidence,
not proof against transients between polls. All task containers are cleaned up.

C remains open for API warmup tail-latency attribution and the isolated paired
cancel-p99 failure. Functional correctness and a successful long soak do not
waive either failed gate. A new connected 30-minute Demo acceptance window remains
separately open after any approved deployment.

### Bounded tail investigation — 2026-11-17

The original warmup failure (read p99 5,127.91ms) and pair-two cancel regression
(41.69 to 125.46ms) remain failed. No production code is changed in this follow-up.
The changes add opt-in test diagnostics and six regression cases for the capture
mechanism; the final expanded diagnostic regression passes 81 tests. Changed-file
hooks pass. The 1,133 packaged production file hashes remain unchanged.

The retained worst cancel reaches the simulator HTTP handler at 15.550ms and
its terminal effect at 15.602ms, then returns from the native coroutine at
125.459ms. The remaining 109.856ms does not establish a unique cause: completion
at the simulator and receipt/processing of its response are different events.
The original incident capture did not retain causal stacks for that interval.
The test harness now queues a bounded scalar marker for completed cancels
>=100ms and drains it from the watchdog, outside producer locks. Full tracing
also records async cancel spans without arguments or exception messages.

Three bounded diagnostic windows retain the same immutable production artifacts,
100,000-row history and normalized healthy book depth, at 2 CPU /2GiB. AC source
was observed throughout; these runs do not replace the original acceptance runs.

| Diagnostic | Evidence directory under `build/mm-load` | Read p95/p99 (ms) | Native cancel samples /maximum |
| --- | --- | --- | --- |
| Candidate, 360 seconds | `20261117-001704-e8b46b` | 135.13 /420.56 | 67 /47.75ms |
| Baseline, 360 seconds | `20261117-002502-ed7cfb` | 51.49 /186.30 | 67 /60.74ms |
| Exact canonical warmup, 300 seconds, full trace | `20261117-003452-8138e6` | 133.65 /324.35 | Full trace retains slow spans, not all-call cancel counts |

Neither original tail reproduces. In the full-trace warmup the largest retained
API request takes 673.06ms, with two sequential SQL spans of 378.87/286.96ms and
0.061ms worker queue wait. It is a daily-statistics request, without the original
multi-second broad route stall. API full GC observations are <=16.03ms in this
run and <=18.20ms in the candidate 360-second diagnostic. This does not establish
the GC duration or cause of the original stall.

The original warmup's final minute adds 47 cgroup CPU throttle periods and
5.313 seconds of cumulative throttled time, after no increase over the preceding
three minute samples. The new traced measurement adds one period /19.746ms
between its first and last in-window CPU samples. This is correlation with
resource contention, not attribution of individual requests: the old run lacks
fine-grained causal capture at the boundary. The retained macOS power log shows
no Sleep/Wake transition in the failed warmup interval; AC samples alone would
not prove that.

Raw diagnostic results, power observations and retained cancel decomposition are
under `build/overview-tail-investigation-20261117`. The 300-second run deliberately
stops before mixed, so its overall incomplete-release result must not be reported
as a full qualification pass. C remains open; retain the existing margins and
capture a recurrence before proposing a production fix for these tails.

### Controlled CPU contention — 2026-11-17

Two predeclared local diagnostic probes run baseline then candidate, once each,
for 360 seconds with the same frozen images, workload, 100,000-row history and
2 CPU /2GiB application quota. CPU-only sibling processes share that cgroup:
three workers for 20 seconds near 180s, twelve for 10 seconds near 260s. Every
worker has an absolute deadline and exits normally. Neither quota nor production
configuration is changed. Actual pulse starts are 179.70/259.61s for baseline and
179.85/259.67s for candidate, relative to their measurement windows. AC is sampled
before/during/after both runs; Docker VM resources remain 8 CPU /8,218,251,264B.

| Diagnostic | Evidence under `build/mm-load` | Read p95/p99 (ms) | Exact ledger |
| --- | --- | --- | --- |
| Baseline with CPU pulses | `20261117-005346-0ff3d1` | 519.34 /5,024.63 | 360 venue /360 recorder fills; positions match |
| Candidate with CPU pulses | `20261117-010136-69bc4f` | 3,567.82 /6,252.99 | 360 venue /360 recorder fills; positions match |

Both runs fail existing latency budgets; both retain zero unexpected HTTP errors.
Candidate is more sensitive to this injected contention in these windows. These
are fault diagnostics, not qualification passes, and do not supersede the failed
healthy warmup or paired cancel gate.

The baseline's first pulse captures a 162.81ms native cancel: simulator handler
entry at 3.028ms, terminal cancellation at 3.037ms, then another 159.770ms before
the coroutine returns. This recreates the post-terminal delay shape seen in the
old 125.46ms outlier. The candidate's largest cancel is 181.93ms at 178.949s,
returning before the first worker pulse begins at 179.85s. It must not be
attributed to those running workers. Injector startup is not separately timed on
the shared Linux clock, so its contribution cannot be excluded using a host-file
timestamp. This earlier event also spends most time after terminal effect:
173.259ms.

The candidate pre-pulse trace records a generation-zero GC pause of 726.82ms,
an API loop stall of 836.30ms and a separate engine stall of 932.23ms. The API
stack includes timestamp serialization in an orders response; engine samples
include qualification and DB waits. These point samples are not evidence that
the named frames themselves consume the entire pause. GC elapsed time also
does not establish collector CPU use. Existing full-GC CPU measurements cover
generation two only, leaving this generation-zero event without that distinction.
Three RED/GREEN cases now require wall time and collector-thread CPU for deferred
GC records across generations 0/1/2; 84 diagnostic tests pass. This diagnostic-only
change occurs after both measurements; the original CPU use remains unknown.

Final snapshots show both runs still running, with three working orders per
side on both markets, no error/restart/ownership-loss logs, matching two funding
bills and three daily snapshots. All minute samples retain the same role PIDs.
During the final approximately 90 seconds, baseline/candidate record 25/13
native cancel returns (maximum 94.27/17.81ms), 25/13 submit returns and 90 strategy
ticks each. These observations support recovery; they do not prove continuous
quoting or a separately measured post-pulse HTTP p99.

Raw pulse timestamps/cgroup counters, power observations, analysis and regression
logs remain in `build/overview-cpu-contention-20261117`. No production fault has
been isolated sufficiently to justify a production patch. Keep C open, retain
every failed gate and distinguish resource contention from demonstrated code
faults before choosing the next fix.

### No-injection GC attribution follow-up — 2026-11-17

One predeclared 300-second canonical warmup runs with full tracing and the new
wall/thread CPU GC measurements, without injected CPU workers. Frozen candidate,
2 CPU /2GiB, 100,000-row history, book/security/defense workload and production
GC policy are unchanged. Evidence: `build/mm-load/20261117-013436-448f3f`;
driver/host observations/analysis: `build/overview-gc-attribution-20261117`.

- Read p95/p99: 136.76/381.47ms; zero warmup violations and unexpected HTTP errors.
- Independent ledger: 300 venue /300 recorder fills, matching positions, two
  funding bills and three daily snapshots.
- All minute samples retain supervisor/engine/API PIDs 1/32/33; final state is
  running with working BUY/SELL orders on both markets, with no error, ownership
  loss or restart lines in retained bot logs.
- API GC captures >=10ms: 14 generation-two events; largest is 13.742ms wall
  /13.289ms collector-thread CPU. No generation-zero/one event reaches the
  capture threshold in the measurement window. The old 726.82ms API pause is
  not reproduced, and its CPU use cannot be reconstructed from these events.
- Engine full GC at 182.357s takes 233.175ms wall /232.592ms thread CPU. This
  collection uses CPU for nearly all of its elapsed time. Another young-GC
  event takes 54.032ms wall /34.367ms CPU. These are different events in a
  different process from the historical API pause.

Maximum retained heartbeat lag is 258.57ms for API and 399.27ms for engine.
The largest retained API request is an orders page taking 852.83ms; its thread
CPU span is inclusive of interleaved loop work, not exclusive request CPU.
There are no retained native cancel spans above the 100ms trace threshold.
In-window role metrics show zero dropped GC events and zero dropped trace
records, but the engine's bounded child-span selection evicts 150 children.
This evidence is not an all-call cancel percentile or continuous-quoting proof.

Use the same-PID in-window metrics for capture-loss accounting. The standalone
shutdown summary files belong to an earlier fixture startup stopped before
history preparation; final harness cleanup forcibly removes the test containers.
Those files do not establish final capture counters or graceful Stop for this
deliberately partial scope.

All 41 sampled power observations report AC; Docker VM resources remain 8 CPU
/8,218,251,264B. Host process sampling includes busy VS Code/Chrome processes,
so this is not an exclusive performance worker. Between the first/last API CPU
samples in-window, cgroup counters add 10 throttle periods /168.654ms cumulative
throttled time; that does not attribute individual GC/request pauses.

The bounded experiment ends without a new production patch or GC tuning.
The three previously added GC timing regression cases and the 84-case diagnostic
regression remain the validation of the test-only timing change. C remains open:
this successful partial warmup does not replace the failed healthy warmup,
paired cancel gate, or the separate connected Demo acceptance.

### Local requalification with operator-confirmed idle host — 2026-11-17

After the operator confirms heavy tasks are stopped, a fixed schedule attempts
up to three sequential 120-second baseline/candidate pairs at 2 CPU /1GiB,
followed by the canonical load/soak only if all pairs pass. Fixtures, normalized
book, 100,000 history rows, security/defense, rates and all margins remain fixed.
Bounded incident/MM/GC capture is identical on both sides. Image digests and
1,133 candidate production file hashes match the frozen artifacts. New evidence
is under `build/overview-local-requalification-20261117`; no old result is replaced.

Pair one completes and fails the submit comparison, triggering the predeclared
stop. Pairs two/three and canonical load/soak are **not run**.

| Metric | Baseline | Candidate | Result |
| --- | --- | --- | --- |
| Read p95/p99 | 49.77 /102.15ms | 132.59 /214.39ms | Absolute budgets pass |
| Tick p99, 120 samples each | 4.922ms | 5.468ms | Pass |
| Tick-start gap p99, 119 samples each | 1,028.751ms | 1,019.392ms | Pass |
| Native submit p99, 16/26 samples | 292.489ms | 331.789ms | **Fail**: +39.299ms; allowed +29.249ms |
| Native cancel p99, 24/26 samples | 45.756ms | 41.298ms | Pass |

Baseline: `build/mm-load/20261117-102533-c94415`; candidate:
`build/mm-load/20261117-102935-b045a0`. Both retain 120 venue /120 recorder fills,
matching positions, two funding bills and three daily snapshots, with no error,
restart or ownership-loss lines. Peak cgroup memory is 780,230,656/780,308,480B,
below the unchanged 1GiB limit. Shared-host process/power observations remain
retained; all 52 power observations report AC. Operator idle confirmation is not
a claim of exclusive CPU allocation.

The largest baseline submit is Spot: 230.539ms before simulator acceptance and
61.950ms after it. The largest candidate submit is Perpetual: 305.200ms before
acceptance and 26.589ms after. Submit cohorts differ: baseline has nine Spot /
seven Perpetual observations, candidate fourteen /twelve. With these counts,
the declared empirical p99 is the maximum on each side. This limits causal and
statistical interpretation; it does not waive the failed gate or change its
quantile, duration or margin.

Neither role's bounded incident file retains causal context within two seconds
of the worst submit intervals. Slow-cancel capture added earlier does not trigger
on a slow submit. A new RED test demonstrates this diagnostic gap. The wrapper
now uses one bounded deferred submit/cancel queue, drains capture outside producer
locks and wraps async `authorize_rest` so future retained traces can include its
nested SQL. The regression verifies that a slow submit preserves preceding
authorization/SQL context, return value, parent linkage and private-argument
exclusion; all 85 nearby diagnostic tests pass. The capture change is made
**after** the measured pair and does not establish its cause or fix its latency.

No production patch is justified from the retained submit decomposition alone.
C remains open, and this stopped scope cannot qualify the release or the
separate connected Demo gate.

### Submit-path attribution — 2026-11-17

One candidate-only 120-second diagnostic reuses the failed pair's frozen image,
2 CPU /1GiB, 100,000-row history, normalized healthy book and security/defense
workload. It adds test-only native-submit/throttle-acquire spans to the previously
added authorization/SQL capture. A real-throttler lock-barrier RED/GREEN test
verifies waiting, one original rate-limit entry after release, result identity,
span parentage and private-argument exclusion; 86 diagnostic tests pass before
measurement. No production source, rate limit, admission policy or guard changes.

Evidence: `build/mm-load/20261117-111638-75a09d`; driver/stage decomposition:
`build/overview-submit-attribution-20261117`. Read p95/p99 is 132.87/237.87ms,
with no absolute violations or unexpected HTTP errors. There are 120 venue /
120 recorder fills, matching positions, two funding bills and three snapshots.
Measured runtime remains running; retained bot logs have no error, ownership loss
or restart lines. Peak cgroup memory is 781,213,696B, below 1GiB.

Of 22 native submit samples, 17 are >=100ms; all 17 have matched retained submit
spans and corresponding deferred capture markers. The largest call is Perpetual:

| Boundary or interval | Time |
| --- | --- |
| Native call duration | 306.714ms |
| Native trace start to authorization start, including throttle | 1.064ms |
| Authorization duration, including queue/loop scheduling | 261.679ms |
| Authorization start to first traced owner check | 252.682ms |
| First traced owner check to authorization return | 8.997ms |
| Native call start to simulator acceptance | 263.552ms |
| Simulator acceptance to native return | 43.163ms |

The throttle span is too short to survive bounded longest-child selection for
this root, but the enclosing pre-authorization segment bounds all throttle time
to approximately 1.064ms. It cannot explain this call's hundreds of milliseconds.
The root evicts nine short children; the retained SQL sum of 2.789ms includes
post-response receipt work and is only a lower bound, not total DB time.

The traced first owner read occurs near authorization's end. Source inspection
shows `admit_order` and final `authorize_rest` share `submission_admission`, with
an explicit loop-turn yield before their synchronous DB gates. CPU samples show
other submit/authorization operations outstanding while the loop runs DB work;
there is no captured GC or full-GC event overlapping this call. Taken together,
this supports admission queue/turn scheduling as the dominant pre-send delay in
this new event. The trace does not independently split lock-acquire waiting from
ready-task scheduling, and does not reconstruct the old 331.789ms event.

The authorization span's 199.326ms thread CPU is inclusive of other work on the
same event-loop thread during awaits, not exclusive CPU for this order. Do not
attribute that entire CPU interval to its owner check or SQL queries.

The bounded diagnosis ends without a production patch: a shared queue is not by
itself evidence of a logic fault. Future scheduling optimization would also need
admission-to-venue latency, queue fairness and fresh Stop/version/ownership-fence
validation. The failed paired submit gate remains failed; this single candidate
probe is not a replacement comparison or release qualification. C stays open.

### Bounded final-send scheduling experiment — 2026-11-17

An operator-authorized implementation experiment gives ready final-send checks bounded
preference within the existing single admission slot: at most two sends before
a waiting new admission, FIFO within each class. Fresh Stop/version/ownership
checks and the existing loop turn remain; the slot is released before network
I/O. It does not bypass a guard or alter API/risk configuration.

The twelve-order logical burst is RED under the old FIFO and GREEN with bounded
priority. Three fresh real-PG native bursts per side retain 396 SQL statements
and twelve send/ACK records per burst with an identical 2ms injected SQL delay.
First enqueue-to-venue acceptance improves from 1,191–1,230ms to 185–212ms;
mean improves 26–33%, and last acceptance is not later in any observed pair.
This measures the whole path rather than moving a wait outside the native timer.
It is controlled component evidence, not exchange/Cloud Run qualification.

Validation: 4,051 native tests +16 subtests, 35 final focused checks (including
two test-only races added after the full native run), and all 997 PostgreSQL
tests across 116 modules. One new version-race fixture initially failed because
legacy Start correctly rejected armed spot_outflow; it now simulates an
independent control-version writer, and Stop/version fencing after the first
send both pass. Original failure logs are retained.

Candidate manifest `2850a961a049f02fc5d08c74a547379dbd405ad426c06620b46029d731182acd`
matches all 1,134 frozen runtime files, source fingerprint
`74a057e83a39edde9b191b449423f04de54257089ed6e2546746c5b523e2df7e`.
Packaged shutdown proof, managed lifecycle and upgrade/rollback pass. These
three scopes do not represent the entire packaged release suite. Evidence:
`build/overview-admission-scheduling-20261117`.

### Priority artifact: resource gate remains failed

The fixed scope stops after its first pair, as predeclared. Baseline
`20261117-123136-0c8d02` and candidate `20261117-123534-68d4b5` each run
120 seconds at 2 CPU /1GiB, 100,000 history rows and identical normalized
healthy-book/security/defense fixtures. Both pass absolute budgets, retain
120/120 independent fills and matching positions, and peak below 1GiB. Read
p95/p99 is 52.95/237.91ms versus 136.17/214.86ms.

| Engine observation, empirical p99 | Baseline | Candidate | Gate |
| --- | --- | --- | --- |
| Tick duration | 5.011ms | 5.022ms | Pass |
| Tick start gap | 1,013.906ms | 1,129.261ms | **Fail**, +115.356ms exceeds 101.391ms |
| Native submit | 272.785ms | 204.643ms | Pass, approximately 25% lower |
| Native cancel | 40.802ms | 111.973ms | **Fail**, +71.171ms exceeds 10ms |

Tick durations have 120 samples and gaps 119 per side; native submit/cancel
have 21/25 samples. Submit/cancel p99 values are empirical maxima with different
cohorts, not a universal speedup or an identical order-cohort comparison. AC
remains connected; host CPU observations are retained. A shared laptop is not
an exclusive worker. No replacement pair, remaining two pairs or canonical
load/soak is run after this failure. The old failures remain unchanged.

The largest candidate tick gap, 1,278.911ms, overlaps a bulk outflow expiry in
`SpotOutflowProjection.advance`: 271.689ms wall /269.928ms thread CPU within
`service.observe_once`. It is distinct from the second-largest gap used as p99.
This establishes CPU-heavy expiry in this observed interval, not a unique cause
of earlier delays or proof that the new scheduling change causes the gap.

The two native cancels above 100ms decompose to 76.914/69.846ms before the
request reaches the simulator, 0.014625/0.008334ms simulator request-to-terminal,
and 35.045/33.345ms from terminal to native return. No captured >=10ms GC
interval overlaps them. Incident markers and sampled owner/SQL context survive,
but native cancel spans are only enabled in full-trace mode; these records
cannot identify the exact wait or reconstruct unobserved SQL time. No speculative
production patch or weaker guard is applied. Raw decomposition:
`qualification/failure-decomposition.json`.

New-image real-SQL Overview isolation `20261117-124133-5824aa` passes at 1GiB:
two server waits end in SQLSTATE 57014, one durable fill is witnessed during
the wait, final venue/recorder counts are 120/120, positions match, engine
PID/owner/session remain unchanged, no HALT occurs, and Stop is clean. Fee
observations preserve a last usable value explicitly marked stale and recover
afterward. This verifies server-wait isolation; it does not model CPU-heavy SQL
or qualify Cloud Run.

The scheduling experiment improves controlled enqueue-to-venue acceptance and
this paired submit metric, with functional safety regression GREEN. **C remains
open** for tick-gap/cancel latency. The next distinct work is bulk-expiry
bounding with unchanged risk proof and exact cancel-path tracing, followed by
new artifact qualification. No commit, push, deployment or connected Demo change
is performed in this scope.

### Cooperative outflow clock catch-up — implementation evidence

The service observer RED case schedules a callback during expiry and finds that
it runs only after all 4,096/100,000 fills finish. The new path processes at most
512 heap/replayed-fill work items per synchronous advance, yields on the owner
loop, and rechecks owner/control version. No mutable risk state moves to a
worker thread. Final-send stays synchronous and never grants partial permission.

Incomplete catch-up is a BUY-only wait: `ready=false`, `stale=true`, decimal
recorded/reserved/remaining values are null, and the reason is
`spot_outflow_projection_catching_up`. `SpotOutflowPending` retires an unsent
managed maker BUY without consuming venue retries; fresh controller admission
is required after catch-up. SELL/cancel remain under their existing gates.
Committed arrivals are detached and bounded, then replayed in FIFO order after
the current clock's due work. Invalid deferred data preserves a BUY failure
instead of escaping as a service exception. Capacity or binding/clock failures
are not relabelled as a normal catch-up wait.

Two intermediate-design counterexamples are reproduced and fixed before this
artifact: a malformed deferred timestamp escaping replay, and skipping a prior
clock when resuming at a later target. The latter changes a deliberate large/
tiny Decimal reference by 2.34; finishing the prior clock and queued arrivals
before moving forward preserves the original operation order and reference
value. These are local TDD findings in the new cooperative design, not claims
that the earlier atomic expiry had those bugs.

The final focused 95 tests cover exact totals/fees/reservations, future and
half-open boundaries, detached duplicate arrivals, bounded overflow, callback
progress, cancellation, Stop/control/ownership interruption, stale clocks,
executor scope, prior fills and genuine failure classification. Full native
regression is 4,074 passed +16 subtests. Earlier staged suite failures and new-PG
fixture mistakes remain in raw logs; they are not silently replaced or counted
as final GREEN. All changed-file hooks pass before freezing.

Candidate source fingerprint:
`24645f512022fccf084ad53af45c6cf1e913648630d4fad7a19ac3b28e7dac19`;
manifest `39a4a0b1b28e9b3b415be370a4b5e52cad3fdb3cd2a4cb54b68de905eba69f99`.
All 1,134 runtime hashes match workspace and image. Raw evidence:
`build/overview-outflow-expiry-20261117`. All 250 cases across the 20 affected PostgreSQL modules pass on the frozen
source, including the corrected new fixtures. Packaged shutdown proof, managed
lifecycle and upgrade/rollback also pass. Full-trace cancel diagnosis and the fixed resource scope are recorded below; C
remains open and no deployment is approved by this implementation evidence.

### Cooperative artifact: tick-gap gate passes, cancel gate stays open

The fixed scope stops after pair one: original baseline
`20261117-140521-319b96` and candidate `20261117-140917-d95fb2`. Each window
runs 120 seconds at 2 CPU /1GiB with the declared 100,000-row/security/healthy
book workload. Both pass absolute budgets, retain 120/120 independent fills,
matching positions/funding and peak cgroup memory below 1GiB (779,096,064B /
777,576,448B). Read p95/p99 is 51.87/211.26ms versus 134.11/251.43ms.

| Empirical engine p99 | Baseline | Candidate | Gate |
| --- | --- | --- | --- |
| Tick duration | 9.914ms | 4.396ms | Pass |
| Tick start gap | 1,016.266ms | 1,021.213ms | Pass |
| Native submit | 318.922ms | 123.704ms | Pass |
| Native cancel | 38.846ms | 66.429ms | **Fail**, +27.583ms exceeds 10ms |

Each side retains 120 ticks, 119 gaps and 24 native cancels; native submits are
18/21. These are same-host empirical cohorts, not identical physical orders or
proof of a causal percentage speedup from expiry alone. The earlier warmup,
submit, tick-gap and cancel failures remain retained. No replacement pair,
remaining two pairs or canonical load/soak is run after this failed comparison.
AC and host observations remain in the scope directory.

The first full-trace diagnostic `20261117-135942-249d2d` passes absolute budgets
with read p95/p99 134.49/364.85ms and 120 exact fills. It retains no native cancel
root/child at the ordinary 100ms threshold. Its bounded per-role metric samples
report advance max 14.559ms and CPU max 14.565ms; these metrics cover retained
lifetime/rebuild samples, not an exact-window owner-loop batch distribution or
total catch-up CPU. The deterministic service tests establish the 512-item
progress boundary; resource tick-gap observations remain the relevant gate.

Because 66ms falls below that trace threshold, one separately predeclared
candidate-only diagnostic lowers capture to 25ms in a detached test asset:
`20261117-141509-6b3d2a`. The business runtime/image is unchanged. Both original
and injected asset hashes, actual threshold and `acceptance_qualified=false`
are retained in `diagnostic-capture-override.json` and the result. The window
passes absolute budgets, read p95/p99 133.79/271.02ms, and exact 120-fill ledger.
It is not a replacement comparison or a new qualification pass.

Eight native cancel roots survive capture; the largest retained root is
42.766ms with zero dropped children. Throttle is 0.071ms, authorization 1.307ms
(including 0.602ms SQL), and authorization-end to native-return is 40.965ms.
Most of this observed call therefore follows the authority check. The capture
does not split transport time from event-loop scheduling or reconstruct the
failed paired 66.429ms event. Async-span thread CPU includes other work on the
same loop during awaits. Do not treat it as exclusive cancel CPU or remove a
fresh owner check based on this observation. No additional production cancel
patch is justified from these two diagnostic windows.

The scoped outflow fix, exact accounting and authority regression are GREEN.
**Resource gate C remains open for cancel p99**, and current-artifact full
soak/connected Demo acceptance remains unqualified. Detailed raw summaries are
`cancel-diagnostic/summary.json`, `cancel-diagnostic-low/summary.json` and
`qualification/paired-comparison.json` under the cooperative evidence directory.


Final cooperative-image Overview SQL isolation `20261117-142129-f55b59` passes
at 1GiB: two real SQLSTATE 57014 server waits, one durable fill witnessed during
the fault, final exact 120/120 venue/recorder fills, matching positions, unchanged
engine PID/owner/session, no HALT, no unexpected error logs and clean Stop. Fees
retain a usable value marked stale and later recover. This is server-wait
isolation, not CPU-heavy SQL or Cloud Run qualification. No additional native
regression is needed after documentation-only changes; final frozen runtime
hashes are verified again before ending the scope. No commit/push/deploy occurs.

### Same-pair cancel attribution: delay localized, qualification still fails

One predeclared diagnostic pair uses the same new bounded offline timeline
assets on both original baseline and frozen cooperative candidate. Business
runtime remains fingerprint `24645f512022fccf084ad53af45c6cf1e913648630d4fad7a19ac3b28e7dac19`.
There is no production patch, image rebuild, guard relaxation or Cloud change.
Actual aiohttp pipeline/terminal-callback tests first reproduce missing markers;
the final 192 nearby diagnostic and web-assistant tests pass, as do file hooks.

| Diagnostic pair | Baseline `20261117-151138-68179e` | Candidate `20261117-151535-02881d` |
| --- | --- | --- |
| Read p95 /p99 | 50.223 /193.761ms | **10,668.465 /12,299.482ms** |
| Native cancel p99 | 31.310ms | **339.461ms** |
| Native calls /unique venue matches | 27 /23 | 26 /26 |
| Exact venue /recorder fills | 120 /120 | 120 /120 |

Both retain matching positions, two identified funding bills and three daily
snapshots. The candidate fails absolute read budgets and all four relative
comparisons. This pair is diagnostic only (`acceptance_qualified=false`), and
does not replace or erase the earlier failed cancel comparison. No remaining
qualification pairs or canonical soak are started. Four baseline calls lack a
venue join and remain partial; they are not assigned invented venue timings.
Timeline streams report no sink/metadata errors, drops or malformed records.
Maximum outer marker difference from the existing native timer is 0.105ms /
0.267ms; this is timer-envelope difference, not a bound on all observer overhead.

The largest candidate cancel is call `31:37:445624351618`, a single Spot request.
Its native timer is 339.461ms; the outer timeline is 339.728ms. Matched stages:

| Stage | Observed duration |
| --- | --- |
| Pre-transport | 10.660ms (including 8.048ms authorization) |
| Transport enqueue ->venue received | bounds [1.111, 1.207]ms |
| Venue received ->terminal observed | 0.063ms |
| Terminal observed ->response ready | 0.590ms |
| Response ready ->client headers marker | **326.738ms** |
| Connection returned ->caller resumed | 0.038ms |
| JSON read/decode | 0.082ms |
| Terminal observed ->tracker callback completed | 556.052ms |

Native REST return and tracker callback completion are separate observations.
The headers marker runs after the request coroutine resumes; it is not kernel
packet-arrival time. The 326.738ms therefore includes transport and client
I/O dispatch/task scheduling, and cannot be labelled pure network delay. The
venue response EOF ->headers interval is 326.527ms. Callback latency also does
not establish when the durable ledger committed.

Two engine samples bracket this call. The later sample observes the synchronous
fill recorder in `save_market_states`, reading three tracked IDs plus unresolved
identities. Its completed span is not retained in the existing 100ms-threshold capture,
so a stack alone does not prove its duration or a defective query. The overlapping
437.424ms async `service.observe_once` root has 11 dropped children; its 330.776ms
thread CPU includes unrelated main-loop work during awaits. Do not assign that
CPU to status publication alone. The bracketing cgroup throttled counters do not
increase, and no retained GC pause overlaps this exact cancel.

Host samples during the candidate show Go up to 169.9%, VS Code renderers up to
151% and clang at 85% CPU, with AC connected. These jobs were not launched by
the measurement driver. This is evidence of concurrent host activity, not proof
that it uniquely caused the regression; baseline/candidate also run different
business code. The current evidence localizes the wait but does not justify a
specific production fix. Keep C open and use a quiet host or independent worker
for the next controlled resource qualification, preserving these failures.

Raw pair, asset hashes, host samples, matched-call summaries and final validation
are in `build/overview-cancel-timeline-20261117`. Test containers and the scoped
sleep assertion are gone. Frozen runtime hashes remain unchanged; no commit,
push or deployment occurs in this scope.

### Cloud Run post-deploy observation, 2026-11-17

At 09:01:44 UTC (16:01 ICT), `hummingbot-demo-00159-ptg` is Ready and serves
100% traffic at 2 CPU /1GiB. Registry config confirms commit
`dc8f0aa692ef0eac0ebf4446181cf48ca5a887af`, which includes `5e3c4180e`, in image
`sha256:17c43394c7cea3f98e75cfd9a6076b6fc5a64a056730c3a19577a01763ca7e84`.
This confirms deployment, not MM acceptance.

The API/owner are fresh on revision159, control92 is acknowledged, `/startup`
and `/live` return200, but `/ready` returns503 `halted`. Status reports
`desired_state=running`, `running=false`, `error=RecoveryRequired`. There are no
running market controllers to validate native submit/cancel latency. Drawdown
and outflow observations correctly remain stale/not-running rather than proof
of available risk headroom.

The HALT predates this rollout: revision158 logs `ownership_lost` and incomplete
cleanup at 2026-11-16 11:06:53 UTC, followed by durable-HALT bootstrap refusal at
11:07:23 UTC. Later owner losses/restarts also retain that refusal. Revision159
logs the same refusal at 2026-11-17 08:58:28 UTC. This establishes an inherited
stop, not the root cause of the original ownership loss. A startup slow-stage
warning records `service.observe` at3,930.706ms with heartbeat gap26.913ms; wall
duration alone does not prove continuous main-loop blocking.

The refreshed revision159 warning query also retains one engine heartbeat stall
of300.114ms at08:59:42.862 UTC and recovery at08:59:42.910 UTC. No child-restart
event is present in that query. Its ERROR entry is the observer's `/ready`503
request, not an additional application exception. These short observations
are not proof of sustained availability or successful MM execution.

Read-only Recovery status currently blocks with `recovery_reason_unsupported`
and `recovery_stop_required`. Its displayed old operation is a failed scope-limit
check from November12, not a new check performed in this review. No Stop, Check,
Recover, Start, database maintenance, key, risk or deployment change is made.
Resolve and verify the durable recovery state before measuring MM on this image.
Raw API, registry and timestamped log evidence is retained in
`build/cloudrun-review-20261117-r159`.

### HALT origin correction and reconnect TDD, 2026-11-17

A subsequent SELECT-only read through the existing Cloud SQL proxy establishes
the actual persisted manual reason: `A required runtime task failed; inspect
service availability status.` `runtime_active=false`, with no pending mutations
or current incomplete-shutdown flag. Thus the earlier ownership-only explanation
was incomplete: ownership losses happened after the original HALT.

The originating revision158 log contains the complete failure chain on November16:
08:16:43 UTC WebSocket1006 and repeated private-stream handshake502; at08:16:53.995,
`ValueError: list.remove(x): x not in list` escapes the perpetual stream's finally
block; at08:16:54.157, the required-task guard reports `connector_task_stopped`.
The stored generic task-failure string was absent from `TECHNICAL_HALTS`, explaining
the later `recovery_reason_unsupported`. Neither the log nor the old DB row records
the original HALT time in structured metadata; no timestamp is fabricated for it.

RED reproduces a successfully established stream failing, followed by failed
reconnect attempts: the loop retained the previous assistant, then removed that
already-removed assistant again. GREEN resets the assistant for each attempt;
repeated handshake failures keep retrying at the original cadence, a later stream
delivers messages, and cancellation cleans up without the double removal. A
genuinely stopped required task still closes admission and requires recovery.

New stops retain a bounded original `halt_diagnostic` with allowlisted reason,
runtime task identity/state, event time and revision. Later reasons continue to
control recovery/risk eligibility; they do not erase the original diagnostic.
Status and Recovery expose the same safe `cause`, separating original and current
reason, cleanup state and unavailable legacy evidence. Publication transfers only
known reason strings and bounded selected metadata, not arbitrary errors or large
checkpoint JSON. Ownership failures now distinguish closed/invalidated/missing
sessions, lock/generation mismatch and database errors; fixed codes and SQLSTATE
reach structured logs without raw SQL/DSNs/exception text. No lost owner obtains
a new write path or reconnect permission.

The task-failure/trading-health technical reasons can enter a stopped recovery
check; preview, exchange/ledger proof, confirmation, risk latches and separate
Start remain required. This is not an automatic reset of the current Demo.
The banner consumes the new safe cause and known time/revision, retains unknown
legacy time and uses operator progress for temporary shutdown fences. OpenAPI
and generated dashboard types are updated together.

The reviewed reset atomically removes active cause metadata while retaining the
normalized origin in the completed operation/audit. Verified clean shutdown and
handover also remove current metadata, so a later shutdown cannot reuse an old
HALT explanation. A private/unrecognized reason with a shutdown-kind flag stays
unknown and cannot claim automatic recovery.

Validation: native Cloud Run regression4,101 passed +16 subtests; the final
recovery/ownership/publication PostgreSQL subset88 passed, following an earlier
five-module93-case run and targeted3-case SQL fault checks (overlapping counts).
Frontend632 pass with coverage; lint, typecheck, production build, backend schema
and generated-type checks pass. Changed-file hooks and `git diff --check` pass.
The initial RED failures and formatting/type-fixture corrections remain in raw
logs; none are presented as passing runs. No new browser E2E is added under the
dashboard's temporary E2E policy. The build retains the existing large-chunk
warning; it is not a failed build.

Evidence and RED/GREEN logs: `build/halt-diagnostics-20261117`. The proxy was closed
after SELECT-only inspection. Current Demo HALT, credentials, risk limits and
trading controls are unchanged. Deploy the repaired image before attempting the
reviewed live recovery flow; current-image MM latency/soak remains unqualified.

### Revision160: deployed fix verified, resolved rejection blocks recovery

Registry config confirms exact `e926ee46c` on `hummingbot-demo-00160-zh5`, image
`sha256:24b37fb9589a68edc915e88baceabe20d0a1bb14211cf29567d731e6dd83f51f`,
Ready with100% traffic. Both cause APIs now explain the old runtime-task HALT;
legacy event time/revision remain null, and `recovery_reason_unsupported` is gone.
Startup/live succeed while ready remains503. A heartbeat stall313ms self-recovers
in the short pre-check observation; this is not a completed MM qualification.

The authorized recovery workflow issues one audited Stop with the configured
Basic admin actor. Control93 is acknowledged and the bot is stopped; a fresh
Check `9f03df17-bb6a-4b06-af80-2187a89723c8` fails `recovery_scope_limit`. The
initial helper conservatively exits on a stale transition observation after
Stop; the continuation verifies fresh owner/acknowledgment before Check and does
not issue another Stop. No Reset/Start or manual database mutation is performed.

SELECT-only inspection finds55,876 orders/15,169 fills, matching checkpoint
financial/binding/environment hashes, a reviewed anchor, zero pending intents,
zero unapplied journals and zero nonterminal trackers. The apparent unresolved
work is one November13 submission attempt in `rejected`, paired with
`OrderFailure`, `creation_pending=false` and no exchange ID. The recorder already
treats this as a durably retired create, but checkpoint recovery accepted only
the literal attempt state `terminal`. That mismatch disables the checkpoint path
and falls back to the capped lifetime replay.

The PostgreSQL RED case reproduces this exact classification error; ten negative
controls already pass. The follow-up source accepts a rejected attempt only with
the matching nonpending failure row, no venue ID and no fills. Prepared, possibly
sent, uncertain, acknowledged, unknown, missing-order and contradictory rejection
cases still disable the checkpoint path. Existing anchor/account/hash/journal/
tracker/exchange proof gates and history limits remain unchanged. A SELECT-only
candidate snapshot on the unmodified Demo DB selects `verified_checkpoint` and
14,519 journaled suffix fills; it does not verify their venue proof or clear HALT.

Affected PostgreSQL59 and native93 pass. This narrow follow-up is not deployed
or committed yet. The previous broad local checks retain their own source scope;
no new load/soak or live MM pass is claimed. The current Demo remains stopped,
HALTed atcontrol93. Raw image/API/SQL/TDD evidence is retained in
`build/halt-recovery-postdeploy-20261117`.

### Revision161: checkpoint path restored, native-history proof still blocked

The registry config confirms exact `aae12bda3` on
`hummingbot-demo-00161-svn`, image
`sha256:27f1e6a2e1a93a44c367866fba2cf433d87d9a8cb9f8788b0ddd71595d277a23`.
Cloud Run is Ready with100% traffic; API and owner agree on Demo/revision161.
Control93 is still acknowledged stopped, with the inherited runtime-task HALT.
Both cause APIs keep the recognized legacy explanation and unknown original time.

One new Check `e18bffeb-584c-4924-8842-1ac49a90ef34` progresses to
`checking_exchange`, confirming the local rejection-classification fix removed
the old `recovery_scope_limit`. It then fails
`recovery_exchange_scope_incomplete`. The configured Basic admin actor is used;
no duplicate Stop, Reset, Start, credential or risk change is issued.

A SELECT-only count over the reviewed anchor's journal suffix finds12,674 Spot
and1,845 perpetual fills. At100 fills/page, Spot needs at least127 pages, exceeding
the current100-page per-connector verifier bound. This is a sufficient capacity
blocker to complete proof even with perfect pagination. The current API does not
report the exact failed page/cursor, so an earlier pagination defect cannot be
excluded from the observed failure code alone. Do not label venue history missing
or corrupt, or replace full proof with an empty open-order list.

The correct follow-up is bounded resumable proof with frozen ledger/venue scope,
complete identity/economic/fee coverage and two agreeing scans plus exposure
checks. Retain the per-job page/time budgets; partial progress cannot authorize
Reset. No code/limit workaround is applied in this review. The local source tests
and old release/resource failures retain their own scopes.

The short check-window logs include one293.815ms heartbeat stall/recovery and the
expected blocked-recovery warning; no child-restart or new service-failed event
is observed in that captured query. Startup/live200 and ready503 are consistent
with the inherited HALT, not MM readiness. Evidence:
`build/halt-recovery-r161-20261117`. Connected recovery and trading acceptance
remain open; the temporary read-only SQL proxy is closed after inspection.

### Segmented recovery source qualification (local only)

The approved follow-up now uses bounded resumable checkpoint proof rather than
increasing a single job's limits. Migration023 stores per-operation/per-pass native
fill identities; each page atomically advances its cursor and digest with those
identities. The saved fingerprint binds ledger/account/owner/control/risk scope.
Two complete matching scans and fresh account/open-order/exposure/head checks are
required. Each job keeps100 history pages/120s, yielding at90s; the complete proof
is capped at250,000 suffix fills per scan and30 minutes. Partial receipts expose
safe progress only, without Reset permission or confirmation. Bounded read retry
does not retry uncertain financial finalization.

Native157 tests pass. PostgreSQL regression68 pass; a separate physical-owner
rollback and supervised API/restart set3 pass; final authority/expiry fences7 pass
(three overlap the regression set). The10,001-fill PG scenario spans at least
three jobs for Check and another three for fresh Reset, preserving ledger hashes,
positions and cost basis. Worker interruption RED exposed a last-page/next-pass
gap in the new implementation; the saved boundary now resumes correctly. Duplicate
IDs/bills, bad economics/fees, late venue changes, timeout/commit acknowledgement
loss, stale scope, revoked actor and expired proof/confirmation remain blocked.
Scratch identities are removed atomically on terminal success or failure.

The existing five-minute preview expiry remains enforced at final Reset. Slow
proof can still expire that review and require a new Check; the larger proof
ceilings do not promise completion within every review window. API/operations
docs describe pending stages, retry progress, migration023 and this limitation.
Evidence: `build/recovery-segmented-20261117`. These source edits are not deployed;
no Cloud/financial mutation, connected recovery pass, MM pass or new soak result
is claimed. Last connected observation remains revision161, stopped/HALTcontrol93.
