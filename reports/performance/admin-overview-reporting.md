# Admin Overview reporting qualification

Updated 2026-11-16. Contract, coverage, durable fees and the publication-read
optimization are implemented. The encoding-before-lock artifact passes all 989
PG cases, 4,033 native tests +16 subtests, scoped packaged lifecycle, the full
60-minute soak, six fault windows and real-SQL isolation. Resource gate C still
fails warmup read latency and one paired cancel-p99 comparison. All earlier
failures and interrupted runs remain retained. This report does not approve
deployment or close connected Demo acceptance.

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
