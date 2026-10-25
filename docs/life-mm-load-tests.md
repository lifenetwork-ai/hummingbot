# LIFE MM local load gate

The original acceptance below is preserved for its recorded image. Subsequent
cache/SQL and task-lifecycle changes, measurements and their own validation are
tracked separately in [life-mm-optimization-review.md](life-mm-optimization-review.md).
Dedicated-account exchange integration is tracked in
[okx-demo-acceptance.md](okx-demo-acceptance.md); its conservative Demo observation
is separate from this offline load gate.

Run with the compiled environment and Docker available:

```sh
make build-cloudrun
make test-mm-load
# Short diagnostics only:
make test-mm-load MM_LOAD_PROFILE=probe
```

`CLOUDRUN_IMAGE` selects the deployment artifact. Full profiles pin its registry
manifest through a disposable loopback-only registry, then remove the registry.
Probe uses its immutable image ID and cannot count as final acceptance. Reports
are sanitized JSON under `build/mm-load/<run>/`; failures retain logs and exit
nonzero. Every test container/network is cleaned in `finally`. No local `.env`
or real exchange credentials are used. Application, PostgreSQL, exchange and
client run on an internal Docker network with dummy Demo credentials.

The gate reuses the deployment-image E2E harness, real OKX adapters, real recorder,
session/ownership guard, V2 executors and PostgreSQL schema runner. Its simulator
has no database access and records its own order/fill/fee/position truth. It is
scripted test infrastructure, not a matching engine. Runtime instrumentation is
an explicitly mounted test wrapper around the normal service entry point. It
measures SQL/pool/recorder/handler timing and book queues without replacing
trading decisions or adding a production metrics endpoint.

## Workload and resources

The bot has 1 CPU and 2 GiB; PostgreSQL has 2 CPUs / 512 MiB; simulator and load
client each have 1 CPU / 512 MiB. Swap limits equal memory limits. Record the host
architecture, amd64 emulation, immutable image identity, source/fixture hashes
and effective YAML. Seed 100,000 closed orders and 100,000 fills from yesterday,
using the recorder's original scale-six units. Both markets and the deployed
weighted basket run throughout the fixed workload. Tiny partial fills retain
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

## Findings before final acceptance

The first 60-second large-history diagnostic failed: only ~14 reads/s completed,
pool waits exceeded one second and API p95 was over three seconds. No N+1 was
observed; query count stayed constant while global history pages scanned/sorted
the whole table. Two real PostgreSQL query-plan regressions failed before the
new indexes, then passed with migration `012_add_admin_history_indexes.sql` and
matching ORM metadata. Both indexes are additive and idempotent.

Concurrent daily aggregate queries still saturated the two PG CPUs. The existing
`cachetools` dependency now provides a 32-entry process-local statistics cache;
one async lock coalesces misses. `ADMIN_STATISTICS_CACHE_TTL_SECONDS=5` by default,
0 disables, maximum 60. Its original observation timestamps remain visible;
failures are not cached. No trading/control state uses this cache, and no Redis
dependency or new cache service was introduced.

A populated upstream performance report also exposed a 422 response: JSONB
stringifies numeric `CloseType` map keys. A failing HTTP regression now covers
closed executors; the HTTP adapter rehydrates those keys before validating the
original upstream report model, without recalculating PnL.

Diagnostic harness corrections are recorded separately: the rate generator now
wakes immediately when its target changes instead of waiting on an old sleep;
market-rate intervals use server counter observation times, and a failed read
queue drain retains its evidence instead of losing the report to a timeout.
A diagnostic run overlapping unrelated PostgreSQL regressions was also rejected
for a daily 503/p99 over budget; it is not capacity acceptance. Final measurement
must run without concurrent builds or other test workloads.

Burst testing also exposed a functional upstream gap: PositionExecutorConfig has
no `position_action`, so the position-hold bucketing treated HEDGE LONG/SHORT as
one net position. A failing test reproduced equal opposing legs collapsing to
zero. The existing orchestrator now buckets PositionExecutor entries by their
configured opening side in HEDGE mode, with storage/restore round-trip coverage.
Spot and ONEWAY netting, and OrderExecutor's explicit CLOSE handling, retain their
existing behavior. A separate PG regression verifies that shutdown omits remote
readback only for failed creates proven never transported; sent/uncertain failures
still require exchange confirmation. A final send-gate rejection is not a remote
order and must not turn cleanup into an empty OKX not-found array lookup.
The rapid start/stop setup reproduced the same race with a later CANCELED
tracker state. Shutdown now retains explicit no-transport evidence from this
process's failed-create hook, reset only after complete cleanup before a new
prepare; sent/uncertain orders remain subject to readback. The independent
simulator also returns OKX's `51400` cancel-not-found item instead of a fixture
KeyError/HTTP 500. Both FAILED and CANCELED paths have real-PG regressions.

The final mixed/burst/soak/fault gate passes on the artifact recorded below.
Passing local load is not a Cloud SQL latency guarantee or evidence of live
exchange fill quality.

Fault-driver corrections: the expiry fixture respects the deployed minimum
60-second session duration. Between two independent DB faults, the reviewed
test-only HALT reset is followed by an explicit stop before starting a new
session; an active session correctly rejects a second start with HTTP 409.

Read latency includes dispatch delay from the open-loop due time, and both
in-window completions and actual completion-span rates are retained. Queued
reads at the end of generation must fit within one ten-client boundary batch;
the queue must still drain within ten seconds. This prevents a driver backlog
from appearing as healthy low network-only latency or a fabricated achieved rate.

A full 15-minute mixed run met latency/rate budgets (18,000 reads, 900 added
fills, aggregate p95 73 ms / p99 532 ms, no HTTP errors) but failed independent
ledger comparison and is **not** accepted. Logs proved the simulator had reopened
a canceled spot order after a fill-candidate/cancel race: it invented 17 fills
after the connector's canceled-order cache expired. Terminal fixture orders now
reject fill injection with an expected 409; the driver selects another live
candidate and only counts accepted fills. A second test reproduces a perpetual
fill snapshot changing to CANCELED during an awaited position publication; fill
events are now snapshotted/published before later account observations. All three
simulator regressions failed before the corrections and pass afterward. These
are test-server fixes, not relaxed recorder assertions. The affected full load
profile is rerun with a fresh database, and recorder fill evidence is retained.

The corrected 15-minute mixed run passes at p95 78 ms / p99 551 ms with exact
independent fill/fee/position observations, but its subsequent switch found a
second real recovery defect. The cancel callback moves a partial entry into the
existing `_failed_orders` list and clears the live entry slot. Hold collection
ignored that list, so ordinary quote refresh discarded historical exposure from
the V2 cache; the ownership guard correctly HALTed on restart mismatch. The
earlier partial-cancel regression had set CANCELED state without delivering the
real callback. That test now delivers the callback, fails before the fix and
passes after including old tracked orders in the existing deduplicated hold
collector. All 82 orchestrator/position executor tests pass. An accelerated
five-second-refresh burst rehearsal now passes switch/stop/start, actual held
LONG/SHORT restoration and ledger comparison. It does not replace the full gate.

Runtime samples additionally expose the existing reconciliation function's
held-plus-active position projection. Healthy end-of-window checks require this
projection to converge to the independent account ledger, not only the account
API observation; any HALT in healthy mixed/soak is a gate failure.


## Accepted artifact and measurements — 2026-10-25

The standard Dockerfile image passes the complete E2E matrix, including
upgrade/rollback, and every full load/fault profile. No real OKX account or
Cloud Run deployment was used. All test containers and networks were removed.

- Image: `sha256:3c096d7852aed5c490fe0d70742003259b0136c24ecfc8d4dbc42913ff8a06ba`
- Manifest: `sha256:9d92178bc17fa18718103687c9e3cc2d5209b6caca6c4353d5eefa086611f60b`
- Uncompressed size: 1,364,475,211 bytes; bot limited to 1 CPU / 2 GiB.
- E2E: `build/e2e-offline/20261025-075646-d838bf/result.json`
- Load: `build/mm-load/20261025-080606-4367d6/result.json`
- Portable sanitized summary: [life-mm-load-acceptance.json](life-mm-load-acceptance.json).
  Full reports retain effective YAML, fixture/driver hashes, PG image/version,
  Docker host limits, raw minute samples and recorder/exchange evidence.

These are **aggregate dispatch-inclusive HTTP read latencies**. See per-route
measurements in the JSON; injected faults are excluded from healthy SLO totals.

| Profile | Real duration | Completed reads | Valid new fills | p50 / p95 / p99 |
|---|---|---|---|---|
| Warmup | 5 minutes | 6,000 | 300 | 14 / 65 / 556 ms |
| Mixed | 15 minutes | 18,000 | 900 | 13 / 73 / 545 ms |
| Bursts/contention | 90 seconds | 1,800 | 90 | 14 / 85 / 616 ms |
| Random/both soak | 60 minutes | 72,000 | 3,600 | 14 / 74 / 584 ms |

Every healthy read completed without unexpected HTTP errors. Mixed delivered
20 reads/s, approximately 100 book updates/s (one boundary batch tolerance) and
1 valid fill/s. Soak achieved 19.9994 reads/s over its actual completion span,
approximately 99.94 book updates/s across random-step admission gaps, and 1 valid
fill/s. Offered and achieved rates are retained separately; boundary/drain timing
is not hidden by reporting only network duration.

The 5x burst delivered approximately 499.64 updates/s and drained its backlog in
0.53 seconds. Ten same-key command retries produced one receipt; ten stale-version
conflicts were counted separately. Retrying after admission preserved the session
start/deadline. Under load, actual switch/stop/start took 3.73 / 0.54 / 2.15 seconds;
command acceptance p95/p99 was 234 / 234 ms.

The soak has 58 minute samples. First/last five-sample median RSS was 324.87 /
339.42 MiB (growth 14.55 MiB), with peak process RSS 341.17 MiB. Median book
backlog stayed 2 → 2; median pool checkout p95 was 1.70 → 1.58 ms. All declared
resource-growth budgets pass. Observed steps 0–5 cover all three profiles with the
same session ID, start and expiry; the saved DB observations retain exact values.
No healthy HALT occurred.

Independent comparison agrees on **4,890 unique fills**, exact base/contract
units, original/converted fees and observed positions, including the V2
held-plus-active exposure projection. Three deduplicated daily balance snapshot
rows exist. Order pages remain at most three SQL statements, fill pages and
cached performance at most one. Polling does not multiply exchange account calls.
All five fault windows pass: feed interruption, owner restart, queued expiry,
real DB lock delay and complete DB failure. Both DB cases fail closed and retain
HALT on restart; test-only recovery between independent cases follows explicit
exposure review.

Validation: `make test-cloudrun-regression` groups 149 + 11 + 898 + 88; all 107
disposable PostgreSQL tests; Swagger drift and pre-commit checks. The 82 targeted
orchestrator/position executor tests are included in the regression coverage.

Remaining limits are explicit:

- Daily statistics cache hits have a median around 6–7 ms, but cache-miss reads
  have per-route p95 around 1.07 seconds and p99 around 1.19 seconds on the large
  fixture. The aggregate SLO passes; this is not a claim that each endpoint meets
  the aggregate threshold. A stricter chart-refresh SLO would need further query
  optimization or a separately scoped aggregate cache change.
- Repeated upstream controller teardown still logs pending-task diagnostics.
  They remain visible in retained logs; the run shows no healthy HTTP errors,
  HALT or sustained RSS/queue/pool growth. This gate does not claim those upstream
  task-lifecycle diagnostics were fixed.
- Host arm64/amd64 emulation and local PG do not establish Cloud SQL latency or
  live exchange fill quality. Dedicated-account OKX Demo P6, instrument/account
  validation and the 30-minute observation remain unverified until configured.
