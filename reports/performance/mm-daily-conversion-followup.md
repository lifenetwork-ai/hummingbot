# Daily fee aggregation and remaining availability investigation

2026-11-04 (Asia/Saigon). **Source, three packaged E2E groups, two controlled
reporting faults and positive Demo fills/restart pass. The rich 600s probe fails
reporting/latency; the hour soak fails only the zero-degraded-response gate.**
Earlier failed results remain in the
[controller/persistence report](../release/mm-controller-persistence-release.md).
[Evidence](mm-daily-conversion-followup-evidence.json),
[checklist](../../docs/plans/OKX_DEMO_CURRENT_IMAGE_ACCEPTANCE.md#reportingprobeguard-follow-up--requested-2026-11-04).

## Confirmed redundant work and source change

The original fill aggregate inlines its conversion predicate into three aggregate
filters. A real PostgreSQL Red regression performs 600 original-fee array parses
for 200 zero-conversion fills. The candidate computes nonzero-converted and
zero-fee-converted counts once in the existing daily aggregate, then derives the
three result fields from those small daily totals. The Green query executes 200
parses. There is no additional full-history materialization or aggregate pass.

The USDT sum excludes other quote assets. Invalid zero conversions cannot alter
its numeric total; an explicit validity count retains NULL when no conversion is
valid. Regression coverage includes mixed valid/missing/zero/foreign conversions,
rebates, exact original-currency amounts, late fills, date boundaries and a single
repeatable-read database snapshot. Response fields and trading authority stay
unchanged. No cache, index, migration, dependency or runtime timeout is added.

## Paired disposable-database measurement

PostgreSQL 14 is limited to 2 CPU / 512 MiB. Five randomized-order paired samples
after warmup are retained for the first three cases; the last uses three. Every
returned value is exactly equal between the original and candidate SQL. These
are diagnostics on one host, not production latency promises.

| Fills / fees | Main fill query before → after, median ms | Complete daily read before → after, median ms |
| --- | ---: | ---: |
| 100k / repeated, nonzero conversion | 323.85 → 301.26 | 775.82 → 773.78 |
| 100k / diverse, zero conversion | 2,250.71 → 1,026.90 | 3,041.33 → 1,899.29 |
| 1M / repeated, nonzero conversion | 2,764.67 → 2,733.73 | 8,448.79 → 7,642.99 |
| 1M / diverse, zero conversion | 26,371.55 → 12,246.17 | 36,764.15 → 23,625.69 |

Diagnostic query deadlines are 90s; bulk fixture setup alone allows 300s after
initial setup-only deadline failures. Shipping statements retain their 2s limit
and 3s I/O deadline. The million-row cases remain beyond that runtime budget.
Two exploratory shapes were not retained: adding classification groups and a
per-row projection boundary did not establish a safe normal-workload benefit.

The old soak timeout used **nonzero** fee conversions, so eliminating repeated
zero-conversion decoding does not establish that historical timeout's root cause.
Plans also show temporary-file sorting in some cases. No global memory/timeout
setting or guard threshold is changed to make the gate pass.

## Independent reference freshness observation

A separate read-only connection subscribes to the same Demo `books` channel for
BTC/ETH/AVAX for 180s, without account access or mutations. It receives 88 AVAX,
401 ETH and 563 BTC messages. AVAX has a 10.96s maximum receive gap and six of
359 point samples exceed the current freshness policy; maximum measured server
book age at spot UID precision is 11.71s. ETH has no stale samples and its maximum
receive gap is 1.55s; BTC also has no stale samples. The public clock response
bounds server-minus-client offset between -0.007s and +0.228s.

This reproduces a real input condition for `stale_reference:okx:AVAX-USDT` even
outside the service. It does not prove the cause of every historical wait, a
continuous outage duration, or the source of `inconsistent_own_depth`. A bounded
private/public observation witness is prepared for the next approved Demo trial;
qualification thresholds and all order guards remain intact.

## Verified image and packaged regression

Image `50f96001` is built from source `14feb47dd`; fixture commit `2e39ad151`
changes tests only. Seven runtime file hashes match and all197 Python package
versions match the preceding image. Runtime
ABI/JIT/TLS/SDK/logging smoke passes. The first packaged matrix proceeds through
nine cases but later times out waiting for quote recovery after the
terminal-before-create fault. Its simulator logs contain transport timeouts; the
original failure lacks a captured database snapshot, so its exclusive cause is
not established. A separate unmodified recovery run passes. That repeat's fault
is armed for7.6s including startup, while the recorded time from first admission
to durable proof is1.57s; those measurements must not be conflated.

Two test-harness weaknesses are independently reproduced with Red tests:
unbounded physical cancellation admission can add replacement batches while ACKs
are held, and a global `creation_pending=0` condition includes unrelated new
intents. The final fixture admits a bounded eight-order batch from a stopped
runtime, verifies matching client/exchange/market identities and durable cancel
rows only for that batch, then releases ACKs. It checks both markets and an
unchanged5s transport envelope. Native65 targeted checks (eleven new) and seven
new real-PG cases pass. A preliminary bounded recovery E2E passes with four spot
and four perpetual cancellations: proof completes in1.28s and release is observed
by2.10s, followed by normal quote recovery, Random/expiry and the intentional DB
outage/recovery case. Existing unbounded lost-ACK faults and all application
trading/HALT/deadline behavior are unchanged. The complete final-fixture matrix
now passes, together with the separate recovery and volatility-defense groups.
No production defect is inferred solely from the first failure.

Native regression passes 2,442 tests plus eleven subtests. The first full
PostgreSQL attempt is stopped after the unchanged original-currency fee query
hits its 2s deadline in the 100k measurement test; 49 other tests passed. Image
C++ compilation overlapped that attempt. This is not a demonstrated quantity or
fee mismatch, and overlap alone does not establish causation. The complete suite
subsequently passes 345 tests without concurrent compilation; the failed log
is retained and a unique historical timeout cause is not established.

## Rich 600-second probe: failed reporting/latency gate

`build/mm-load/20261104-072930-5e4e22` uses the verified shipping image with
MM activity, bounded incident capture, operations reads and the original
thresholds. All12,000 primary and3,000 auxiliary responses are HTTP200. One real
main-fill aggregate reaches its2s SQL deadline, producing13 explicitly degraded
daily responses. Primary p95/p99 are657.20/1,138.16ms; auxiliary p95/p99
661.81/1,072.21ms. The zero-degraded-response and four latency gates fail.
All600 synthetic fills, original fees and positions reconcile exactly. The
active reconciliation command reports incomplete when local state changes
during its read; it does not report invented position differences.

Within the measured window,36 engine and3 API probe episodes recover without
child replacement. Largest logged observation intervals are2.519/1.229s;
the independent incident-clock interval for the largest selected episode is
2.533s. These are different observation points, not quote downtime. In ten
selected retained contexts, maximum tick/controller start gaps reach2.208/1.967s.
Some contexts retain11–12 live orders; others contain empty discrete venue
samples while replacement submissions are observed. Twelve connector submission
returns overlap the largest episode, but a return is not terminal venue proof.
Neither uninterrupted quoting nor zero MM delay is established.

No timing or guard threshold is relaxed. Metadata-only planner statistics are
captured outside measured windows on the disposable database. The seeded
timestamp has one distinct value; the default sort budget is4MiB. Repeated-fee
plans show disk sorting, but these observations alone do not identify the unique
historical timeout/p99 cause. Global JIT/parallel defaults in those statistics
must not be mistaken for the daily query's transaction-local overrides.

## Controlled slow-report isolation: both cases pass

Two separate120s tests inject one server-side2s timeout into the API report:
first a wait, then a CPU-heavy expansion. Both use the shipping image without
runtime-source overlays. Each independently observes two exchange fills during
the actual timeout and a committed-fill witness before it ends. Each reconciles
120 unique fills/fees/positions, retains the same engine PID/owner/session,
produces no HALT, returns to fresh daily data and stops cleanly.

Expected stale replies in these fault tests do not turn the failed healthy probe
into a pass. They demonstrate progress in these two controlled conditions,
not universal isolation from shared CPU, storage or PostgreSQL contention.
An initial runner stops before fixture construction because system Python lacks
pandas; the retained reruns use the established `hummingbot` conda environment.

## Connected Demo: positive spot/perpetual fills and restart pass

Fresh read-only image preflight verifies REST/public/privateWS, modes,
instruments, balances, positions and no pending orders. Stopped upgrade/restart
preserves four ledger hashes. The explicitly approved temporary40USDT-per-market
preset changes only sizing/spread fields; post-only, fee floor and every risk
guard stay intact. Spot follows AVAX briefly, then sideways; after the spot fill,
both markets run. These are connected OKX Demo executions, distinct from the
synthetic load fills above.

| Market | Natural maker execution | Exact original fee paid |
| --- | --- | --- |
| BTC-USDT spot | SELL0.00025896BTC at77,231.2USDT | 0.0159998332416USDT |
| BTC-USDT perpetual | BUY0.0001BTC at77,179.7USDT | 0.001543594USDT |

Individual REST trade IDs match quantity, price and fee; repeated readbacks
retain each row once. Filled-active restart preserves the pinned session,
anchors, deadline and generation. The21 prior fills remain unchanged; final23
rows are unique. Independent terminal review confirms all37 new orders:
35canceled,2filled. Spot cash delta and perpetual base exposure agree with OKX.
The original200/400 catalogue and same canonical container are restored,
trading stopped, no pending orders/mutations or HALT; no DB repair/reset occurs.

The armed bounded read-only guard witness emits no trigger records in this
trial. Historical `inconsistent_own_depth` is not reproduced or causally closed.
Fourteen warnings classify as guard recovery/reduced sizing and two server-time
refresh warnings; no unclassified ERROR remains. The separate180s public-book
observation above still establishes an AVAX freshness input condition, not a new
guard implementation defect. Original private YAML/env hashes remain unchanged.

## Standalone hour: trading/retention checks pass, reporting gate fails

`build/mm-load/20261104-080016-a1e88c` completes one continuous3,600s window
with the original2CPU/2GiB bot,2CPU/512MiB PostgreSQL, pool5, TTL15 and100k
history fixture. All72,000 primary and7,200 auxiliary replies are HTTP200.
Primary p95/p99 are485.92/991.42ms; auxiliary472.47/947.61ms. Latency budgets
pass with little p99 headroom; this is not evidence of a general speedup over
the older image on a changing host.

The only failed budget is `errors`: two statements reach the unchanged2s SQL
deadline, both in the **original-currency fee aggregate**, distinct from the
main aggregate that times out in the short probe. Each produces14 explicitly
stale daily replies,28 total, across4.654s and4.711s response spans. The simulated
venue executes4 and5 fills during those spans; their identities/fees/positions
match later. This does not prove their commits occurred inside those spans or
that quotes were continuously present.

All3,594 synthetic fills, original fees and positions reconcile exactly.
Retention growth is25.6875MiB against64MiB; peak raw cgroup memory is702.48MiB
against1.5GiB. The57 runtime samples have a maximum65.72s wall gap and pass
continuity, HALT, backlog/pool, per-role RSS, FD and thread checks. Supervisor,
engine and API PIDs remain1/32/33; early/late median RSS growth is0/15.625/10.238MiB.
This hour establishes bounded retention for this workload, not absence of every
possible long-lived leak. The runner's printed `RSS` sample is raw cgroup memory;
per-role RSS and cache-adjusted retention are separate measurements.

There are158 recovered engine and13 recovered API probe episodes, with maximum
logged observation intervals4.514/1.534s and no child replacement/service exit
during the window. Minute samples all remain running. The hour has no MM activity
stream, so it cannot prove uninterrupted quoting. Random executes six steps but
draws only follow_token/sideways; full three-scenario coverage is supplied by the
separate E2E matrix, not inferred from this random draw.

Afterward, the original Demo database/canonical service is restarted. A separate
network-isolated restore of the pre-soak backup matches current orders/fills/
positions/executors hashes. Packaged REST/WS/account preflight passes; service
is owner-available, trading-stopped, no HALT/pending mutations/unresolved orders,
on the verified image. Private configuration and seven runtime hashes remain
unchanged. Owned temporary test containers and the sleep inhibitor are removed.

## Remaining acceptance and causal limits

A clean current-image zero-degraded-response gate is **not obtained**. Source
optimization is confirmed for redundant zero-conversion decoding, not a cure
for both reporting statements under contention. Million-row reads remain beyond
the runtime deadline. Further SQL work needs statement-specific plans/profiling
on an isolated representative dataset; increasing deadlines or adding cache
without measurement would conceal the retained acceptance failure.

The unique historical p99/probe cause and historical own-depth cause remain
unproven. Collect the bounded public/private witness on recurrence; do not infer
a guard defect from an old warning or repeat hour gates to recover missing logs.
This standalone soak does not replace full R1 warm/mixed/burst/soak/six-fault and
matched ordinary-entrypoint R2 acceptance, which are not repeated on this image.
The current follow-up is complete with an incomplete release qualification;
there is no new Cloud Run/production acceptance or live-account trading.

Raw diagnostics/helpers: `build/reporting-followup-20261104/`. Discarded or
interrupted experiments are preserved there and are not passing gates.
