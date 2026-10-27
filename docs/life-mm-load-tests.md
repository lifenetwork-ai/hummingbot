# LIFE MM local load testing

Workload, commands and acceptance budgets for offline verification. Historical
results and measured fixes are in [the load report](../reports/life-mm-load-tests.md).

Run with the compiled environment and Docker available:

```sh
make build-cloudrun
make test-mm-load
# Short diagnostics only:
make test-mm-load MM_LOAD_PROFILE=probe
# Add operator-history/configuration polling to the same diagnostic workload:
python scripts/test_mm_load.py --profile probe --probe-seconds 60 --history-rows 100000 --operations
```

`CLOUDRUN_IMAGE` selects the deployment artifact. Full profiles pin its registry
manifest through a disposable loopback-only registry, then remove the registry.
Probe uses its immutable image ID and cannot count as final acceptance. Reports
are sanitized JSON under `build/mm-load/<run>/`; failures retain logs and exit
nonzero. Every test container/network is cleaned in `finally`. No local `.env`
or real exchange credentials are used. Application, PostgreSQL, exchange and
client run on an internal Docker network with dummy Demo credentials.

`--operations` preserves the original eleven-route read population and all its
budgets. In addition to the existing event/liquidity reads, it adds one command
list, session list and pinned-configuration read per second. The additional reads
share the auxiliary latency/error gates and have per-route measurements. This
bounded multi-widget workload is not a replacement for the unchanged hour soak
or an actual FE browser test.

The gate reuses the deployment-image E2E harness, real OKX adapters, real recorder,
session/ownership guard, V2 executors and PostgreSQL schema runner. Its simulator
has no database access and records its own order/fill/fee/position truth. It is
scripted test infrastructure, not a matching engine. Runtime instrumentation is
an explicitly mounted test wrapper around the normal service entry point. It
measures SQL/pool/recorder/handler timing and book queues without replacing
trading decisions or adding a production metrics endpoint.

## Workload and resources

For the focused E0 availability matrix, run
`python scripts/test_e2e_offline.py --image <image> --scenario availability --trading-env demo`.
It uses the same disposable PostgreSQL/internal exchange network and real service,
with an explicitly mounted test wrapper injecting read-only startup failures,
optional report/snapshot/check-claim errors and a failed controller task. Required
feed staleness comes from the simulator. It checks process survival, automatic
recovery, preserved session anchors/deadline and durable HALT for the failed
trading task. The wrapper is test-only; it adds no production fault endpoint and
does not replace a full load/soak gate. `--scenario all` includes this matrix.

E1 adds the `quote-guards` simulator scenario to `--scenario all`. It explicitly
enables strict guards, includes bot orders in observed depth, and tests own-only
liquidity, joint cancellation, manual breakout, deviation, partial fills, delayed
resume and a clean restart retaining anchors/deadline. Run it independently with
`python scripts/test_e2e_offline.py --image <image> --scenario quote-guards --trading-env demo`.
The diagnostic `python scripts/test_mm_load.py --image <image> --profile probe
--strict-guards` enables the policy on the ordinary short load workload. Its
effective YAML and flag are retained with evidence. Default production guards
remain disabled; this probe does not certify E6 mixed/burst/sixty-minute acceptance.

E2a adds `--scenario drawdown` to the image matrix. It checks partial-fill
preservation, retained perpetual valuation after switching to spot, stale-feed
wait/recovery, clean restart and durable risk HALT. The probe's `--drawdown` flag
explicitly sets test-only global/controller limits and can be combined with
`--strict-guards`. The effective YAML is retained; production limits remain
disabled by default. It is a short diagnostic, not a replacement for E6.

E2b adds `--scenario spot-outflow` to the image matrix: partial BUY, fee/rebate,
BUY-only cap, funded SELL recovery, terminal proof, cold restart and market-mode
changes. Explicit `--spot-outflow` enables explicit spending limits and binds
the empty test DB before seeding yesterday's historical rows. It can be combined
with `--drawdown --strict-guards`; it never bypasses the legacy-origin wait.

The bot has 1 CPU and 2 GiB; PostgreSQL has 2 CPUs / 512 MiB; simulator and load
client each have 1 CPU / 512 MiB. Swap limits equal memory limits. Record the host
architecture, amd64 emulation, immutable image identity, source/fixture hashes
and effective YAML. Seed 100,000 closed orders and 100,000 fills from yesterday,
using natural PostgreSQL NUMERIC units. Older recorded runs used the prior
scaled-six schema; each report retains its original image/schema scope. Both
markets and the deployed weighted basket run throughout the fixed workload. Tiny partial fills retain
the actual inventory/exposure limits without requiring unrealistic virtual capital.

- Warmup: 5 real minutes; mixed: another 15 real minutes. Ten FE workers receive
  an open-loop aggregate 20 reads/s, while the exchange sends 100 aggregate book
  updates/s and one valid partial fill/s. History pages include limits 1/100/500;
  daily statistics and cached V2 performance are polled alongside status/accounts.
- Bursts: 90 seconds, including 10 seconds at 500 book updates/s, older/duplicate
  fill replay, ten simultaneous retries of one command, ten stale-version
  conflicts, and actual switch/stop/start completion under concurrent reads.
- Soak: 60 real minutes in both markets, random steps every 600 seconds and a
  finite two-hour session. Rates are recorded across intentional admission gaps;
  the fixed mixed window is the sustained-rate acceptance window. Synthetic
  fills pause during the five seconds before a scheduled step handover and while
  admission is closed; the generator catches up afterward. This isolates healthy
  recovery from the separate fill-during-shutdown uncertainty/HALT regression.
- Separate 20-read/s fault windows interrupt a basket source, restart the owner,
  expire a session while book events queue, block DB reads with a real table
  lock and stop PostgreSQL. Fault errors are retained separately from healthy
  latency/error acceptance. After DB loss the service must fail closed and
  retain HALT on restart. Only the existing explicit test-only exposure-review
  helper can clear a test HALT between independent fault cases.

An observational reconciliation runs once per healthy window while fills may
continue. A concurrent-state `incomplete` report is valid; it must finish within
its existing bounds and never mutate control or trading state. End-of-window
checks compare independent unique fill identities, base/contract units, prices,
fees and actual perpetual positions with PostgreSQL/API observations. Daily
counts include both seeded history and live test fills, and real daily balance
snapshot writes must exist. FE polls must not create per-request exchange reads.

## Budgets declared before final execution

Healthy aggregate reads and command acceptance: p95 ≤500 ms, p99 ≤1 s and no
unexpected errors. Actual stop/cancel ≤5 s; actual start/switch ≤15 s. Record
per-route latency as well, so a slower individual endpoint remains visible.
Offered and achieved rates are separate. Market rate uses the simulator's
monotonic counter observation interval, with one four-book batch permitted at
the measurement boundary. A short diagnostic never replaces the full durations.

Burst book backlog must return to its steady queue within 10 seconds. Peak bot
RSS ≤1.5 GiB. Minute samples preserve CPU, RSS, loop lag, event age/backlog,
recorder latency, query counts, pool checkout latency and PG connections/locks.
Soak requires at least 55 minute samples. Comparing the first/last five samples,
median RSS growth must be ≤64 MiB, book backlog ≤max(10, initial+4), and pool
checkout p95 ≤max(20 ms, initial+5 ms). Pool timing includes pre-ping/checkout;
it is not a pure server execution measurement. Existing bounded query-count
checks remain: order pages ≤3 SQL statements, fills and cached performance ≤1.

## Opt-in causal diagnostics

```sh
make test-mm-load MM_LOAD_PROFILE=mixed MM_LOAD_TRACE=true
make test-mm-load MM_LOAD_PROFILE=soak MM_LOAD_TRACE=true
python scripts/analyze_mm_load_trace.py build/mm-load/<run> --window mixed
```

`--trace` adds a test-only writable output mount; the bot filesystem remains
read-only. Request IDs link client scheduling/network timing to server spans
for pool checkout, SQL, commit/rollback, recorder and existing observation tasks.
A watchdog samples the event-loop stack during stalls above 200 ms, GC pauses
and cgroup CPU counters. One additional PostgreSQL connection in the existing
client samples activity, waits and blocking PIDs every 250 ms. No extra service
or production endpoint is added. SQL bind parameters, request bodies, credentials
and frame locals are excluded; literal SQL values are redacted.

Server roots below 100 ms are discarded; retained roots have at most 128 spans.
Each JSONL stream has a 128 MiB limit and a 2,048-record nonblocking queue;
dropped records/spans and sampler errors are reported. Client data appends across
warmup and mixed windows. Trace files, hashes and runtime samples stay under
ignored `build/mm-load/<run>/trace/`. Keep portable findings under `reports/`.
The analyzer excludes startup using the workload's monotonic window and matches
wait observations by backend PID and overlapping time. Stack/SQL/CPU overlap is
observational evidence; a missing wait between samples does not rule out DB I/O.
Nested span durations and async thread CPU are inclusive and must not be added.
Tracing consumes CPU/I/O and adds one database connection; traced timings must
be identified as such, with original latency budgets unchanged.

For complete diagnostic windows even after latency-budget violations, use
`python scripts/test_mm_load.py --profile mixed --trace --keep-going` (and then
`--profile soak`). `--keep-going` preserves failures and the final nonzero exit.
The analyzer retains up to twenty slowest requests plus twenty failed requests,
so timeouts keep their server correlation when queued successes rank slower.


## Quick CPU replay before long-run acceptance

`python scripts/benchmark_admin_serialization.py --baseline <previous-admin-models.py> --output <result.json>`
compares the real order/fill DTO implementations with counterbalanced repetitions
and identical JSON outputs. `scripts/replay_mm_executor_reports.py` reuses the
existing report math on a stored offline executor batch without starting actors
or a database. These isolate small stages; their scope excludes live scheduling
and does not replace the zero-error soak gate. Commands, excluded state and the
measured iteration are in the [short diagnostic review](../reports/life-mm-short-repro-review.md).

The test wrapper also measures separate pool queue/connect/pre-ping operations
and wall/thread CPU for executor reports and response validation/serialization.
Synchronous stage CPU includes child work; operation timings include scheduling.
Maxima from different calls must not be added.

## E6 all-feature acceptance

Use the cached final image manifest (or a tag that resolves to it):

```sh
# Same all-feature gate through Make (MM_LOAD_PROFILE defaults to all):
make test-mm-load-enhancements CLOUDRUN_IMAGE=<image>
# Short diagnostic, including the active expiry burst:
python scripts/test_mm_load.py --image <image> --profile probe \
  --probe-seconds 150 --enhancements --trace
# Final unchanged window durations: warmup 300s, mixed 900s, burst 90s,
# random soak 3600s, followed by separately classified fault windows.
python scripts/test_mm_load.py --image <image> --profile all --enhancements
```

`all` selects every time window; the enhancement fixture is selected separately.
`make test-mm-load MM_LOAD_ENHANCEMENTS=true` is equivalent to the dedicated
target. `MM_LOAD_TRACE=true` remains opt-in. Successful fault cases are saved
individually and into the root result as they finish, even if a later case fails;
partial evidence never marks the overall run passed. Command failures retain the
last receipt and readiness reason without dumping request/configuration payloads.

E6 release acceptance uses the ordinary bounded instrumentation without the
opt-in diagnostic tracer, matching the production default. Add `--trace` for
request/SQL/GC investigation and label those results separately. The accepted
full profile and retained cold/traced stress failures are recorded in the
[E6 verification report](../reports/life-mm-e6-verification.md); passing the
ordinary gate does not certify a traced soak.

`--enhancements` selects explicit test-only strict/bootstrap guards, drawdown,
outflow, inventory skew and maker fee/NATR policies. Production defaults remain
disabled. Healthy outside depth and completed candles are supplied independently
by the existing simulator; no fills are used to qualify bootstrap. The effective
YAML, hashes and all settings are saved with each result. Individual policy flags
also work with full profiles; earlier phase probe results remain diagnostics.

The original eleven-route read stream, rates, scheduled slots, latency population,
window lengths, limits and budgets are preserved. One event-history and one
liquidity read/s are added **on top**. Their scheduled latency has its own identical
500ms p95 / 1000ms p99 gate; errors from either stream fail the zero-error gate.
Primary requested/completed counts remain comparable with earlier runs. Auxiliary
counts/statuses/latencies are reported separately. Event pages must use two queries,
liquidity one; FE reads must not multiply exchange account requests.

The fixture still contains exactly 100000 orders/fills. For all-feature tests,
all synthetic historical rows share one timestamp about a day ago: 120 seconds
before the end of the configured one-day outflow window. The 50000 spot fills
therefore enter the real projection together and expire together under the
1-CPU bot limit. Historical terminal orders remain outside the ordinary one-hour
reconciliation scope. The empty test database is bound before fixture insertion;
no production coverage/reconciliation check is bypassed. Initial retained count
and post-expiry count are recorded; warmup asserts the large burst actually expires.

Existing bounded test instrumentation also measures NATR, cached spread composition,
candle sampling, liquidity observation and outflow advance with wall/thread-CPU
distributions. No production tracing endpoint, background task or cache is added.
Healthy gates and intentional fault windows retain distinct classifications.
E6 is acceptance of the new image; the previously closed historical timeout
reproduction remains closed.

The burst emitter sends genuine book frames with 2% headroom above its requested
minimum (only above the ordinary 100/s rate), allowing modest batching/timer
jitter. Actual delivered throughput still must pass; headroom is not a guarantee
against producer saturation. The required 500/s minimum and all latency/error budgets remain
unchanged; counters increment only after actual sends. Exact delivered frames,
measurement duration and rate are retained. The burst helper restores the steady
rate in `finally`, including a failed measurement. An early E6 run that delivered
499.51/s is retained as failed; its passing HTTP results do not close the burst gate.


The simulator's internal hot readers use `/working-orders` for fill candidates
and `/market-metrics` for the actual-send counter plus a monotonic timestamp.
They avoid transferring an ever-growing request/fill ledger during generation.
Full independent `/ledger` snapshots remain the accounting source before/after
windows and during verification. These internal test endpoints are not deployed
application APIs and do not replace any scheduled admin read or lower a load gate.
