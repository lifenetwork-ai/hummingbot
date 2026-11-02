# Engine status-publication responsiveness

Date: 2026-11-02 (Asia/Saigon). Image `e2d393b1` completes full local R1 and
matched shipping R2 after source, smoke/E2E and paired diagnostics. One avoidable
blocking path is fixed; transient probe attribution remains partly unresolved.
[Portable evidence](mm-engine-status-publication-evidence.json). Earlier image
`39198499` retains its original acceptance scope. [Plan](../../docs/plans/MM_ENGINE_STATUS_PUBLICATION_TDD_PLAN.md).

## Confirmed path

The deeper review finds frequent retained stacks under observe_once →
publish_status → BotStateStore.publish/AdminStore.record_observation → bounded
PostgreSQL wait. In the existing healthy envelope, the retained publication
frames occur repeatedly; these are bounded contextual samples, not unique-event
counts or proof that every historical probe has this cause.

The legacy publication is synchronous on the trading event loop. A deterministic
native Red shows a slow status write finishing before the loop can service its
scheduled health task. A stronger legacy-source PostgreSQL replay injects 400ms
only into the status-publication SELECT: an already-started external HTTP probe
with the shipping 300ms deadline returns TimeoutError. This establishes a
sufficient cause for a probe miss without changing trading/ownership logic.
The first HTTP replay accidentally scheduled its client only after the blocking
call; that false-negative test is retained separately. An explicit client-ready
barrier fixes the test and produces the retained Red.

## Runtime change

Publication captures and normalizes a detached JSON snapshot, identity, version
and daily-checkpoint decision on the event loop. Only the unchanged store's SQL
transaction runs through asyncio.to_thread. The observer awaits the committed
result; errors still propagate and fenced rejection still does not advance the
checkpoint cadence. Connector/strategy state and availability updates never run
on the DB worker.

One async admission lock plus one retained physical task/context bound the work.
Cancellation shields the physical write; close stops admission and drains it
before final status and owner release. An unconsumed physical-write failure blocks clean
release. The existing executor, pool, SQL version/generation fences, receipt
transaction, query/IO/probe deadlines and restart grace are reused unchanged.
Fill/intent acknowledgment and exchange mutations remain on their original
paths. There is one asynchronous publication method, not a parallel legacy mode.

Three initial Reds cover loop blocking and async lifecycle/cadence. A separate
candidate concurrency Red detects overlap between queued drainers; serialized
admission fixes that before packaging. It is not labelled a pre-existing legacy
concurrency bug. A second prototype race allowed cancellation on the same
loop turn as a failed physical write to clear its retained result; its own Red
now verifies that shutdown still sees the failure and retains ownership.
Eight focused publication cases and the existing service tests
pass after the change; targeted PostgreSQL 40 and real HTTP/CAS 2 cases pass.
The final native regression passes 2,279 tests plus 11 subtests. Final-source
PostgreSQL passes all 284 cases, including the HTTP/CAS additions. Swagger
passes. Four failures
in the first broader CLI attempt were synchronous test doubles for the newly
async method; converting those doubles to AsyncMock preserves their original
HALT/send-gate assertions. The failed run remains retained.

The prior accepted local image was externally removed. A full current Dockerfile
build with existing dependency cache produces image
`e2d393b1` (1,368,913,677 bytes), source `a67233dc9`. All application/config hashes,
actual import path and commit metadata match. Runtime smoke and targeted
spot-outflow, funding and LIFE MM groups pass on the same artifact. Config ships
under `conf/`, which the initial verification helper mapped incorrectly; the
next helper exceeded Docker's argument size. A stdin-based verifier fixes both
without rebuilding the image. The initial counterfactual launcher also treats a
bare image ID in FROM as a registry name; a verified local tag fixes that before
any measurement. Every failed launcher/verification log is retained separately;
none is classified as an application runtime failure.

## Same-base diagnostic pair

The counterfactual derives from this rebuilt image with exactly the legacy
`e20530257` service.py restored. All shared dependency layers and other packaged
application/config hashes match. Its label explicitly identifies a hybrid
counterfactual, never a release source. Fixed-order legacy then candidate runs
use byte-identical fixtures, 100k historical fills, both markets, ordinary
shipping entrypoint, 2 CPU/2GiB application and the original DB/rate/TTL budgets.
Synthetic metadata supplies fixture references only: passed=false and diagnostic
scope prevent borrowing the old image's acceptance.

| 600s condition | API p95 | API p99 | API errors | Recovered engine probes | Independent fills |
| --- | ---: | ---: | ---: | ---: | ---: |
| Legacy publication | 52.97ms | 302.45ms | 0 | 2 | 600 |
| Off-loop publication | 50.79ms | 65.03ms | 0 | 2 | 600 |

Both pass all diagnostic budgets, exact original-fee/fill/position reconciliation,
and preserve sampled supervisor/engine/API PIDs. Probe count does **not** improve
in this pair. A lower API p99 is a one-pass observation, not proof of probe
resolution or a universally faster API. The deterministic 400ms DB/300ms HTTP
Red/Green establishes the specific blocking cause; it does not establish that
this cause generated any particular historical or paired timeout. Reported
supervisor recovery intervals are not quote downtime. Local emulation, fixed
order and no repeated sampling limit broader inference.

Raw pair metadata and summaries: `build/engine-publication-20261102/probe-*.json`.
The 600s pair is diagnostic only. The subsequent full R1 and matched shipping
R2 below provide separate final-image local acceptance.

## Final image gates

Source and harness hashes remain frozen through the entire sequence. Image ID,
manifest, fixture/resource/TTL match, declared durations, ledger and sampled PIDs
are independently checked after each full gate. No failed measured window is
waived, shortened or relabelled; all three launcher/helper failures occur before
measurement. No new DB pool, service, cache or risk bypass is introduced.

| Gate/window | Duration | API p95 | API p99 | Unexpected API errors |
| --- | ---: | ---: | ---: | ---: |
| R1 warmup | 300s | 50.39ms | 72.46ms | 0 |
| R1 mixed | 900s | 54.40ms | 86.41ms | 0 |
| R1 burst | 90s | 56.19ms | 90.21ms | 0 |
| R1 Random soak | 3,600s | 59.71ms | 112.71ms | 0 |
| R2 ordinary warmup | 300s | 50.86ms | 61.80ms | 0 |
| R2 ordinary mixed | 900s | 53.51ms | 71.80ms | 0 |

All unchanged healthy budgets pass. R1 independently reconciles 4,886 fills,
original fees, positions and two funding bills. This Random soak covers all
three profiles. R2 independently reconciles 1,200 fills/fees/positions and two
bills in its fresh DB. R2 is the ordinary production entrypoint with matched
warm300/mixed900 scope, not another instrumented hour soak.

The hour retains 58 complete runtime samples, max gap 65.50s, one PID per
supervisor/engine/API and no healthy-window HALT/restart. Measured retention is
+13.18MiB versus the unchanged 64MiB budget; raw cgroup growth is +24.51MiB.
The declared basis subtracts file cache while retaining shmem. Peak container
memory is 722.51MiB, below 1.5GiB. Threads are unchanged across early/late sample
medians; engine/API FD medians grow by 3/2 and remain within the existing gate.
This supports the measured one-hour contract, not an indefinite no-leak proof.

All six post-soak fault groups pass: feed, volatility-defense, owner restart,
queued expiry, database delay and database failure. Their deliberate failure
intervals are excluded from healthy latency statistics, with original evidence
retained. Probe deadline 300ms, sustained-failure grace and ownership/recovery
rules are unchanged.

Raw runs:

- R1: `build/mm-load/20261102-093032-923f89`.
- R2: `build/service-supervision/production-control-20261102-105947-340daf`.

## Residual probe findings

The healthy R1 envelope has 15 engine failure/recovery transitions, all in soak;
its warmup/mixed/burst windows have none. The longest supervisor-reported
recovery interval is 1.205s. R2 has six recovered transitions (two warmup, four
mixed), longest 1.070s. No healthy child restart occurs. These intervals include
waiting until a subsequent probe and do not measure quote downtime.
The preceding image's rich gate had 54 recoveries and its shipping R2 had zero;
this differing result and the paired 2-vs-2 comparison prevent a claim of universal
probe improvement. Each is a single local run with different host timing and
Random profile sequence.

All 15 R1 episodes have retained recovery markers and were reviewed using the
existing analyzer with an ignored envelope metadata copy and symlinked original
incidents. Original result/log/incident files remain unchanged. Bounded
retained context shows:

- A main-thread ownership check at 627.17ms wall / 267.23ms thread CPU, including
  a 623.66ms client SQL span. The SQL span precedes the published failure marker;
  a later stack samples another ownership query. Client time includes transport,
  decoding/runtime and scheduling, not just server query execution.
- A main-thread executor-report span at 358.36ms wall / 144.47ms thread CPU in
  pre-marker context, followed by an ownership-check sample. This is a useful
  next profiling target, not proof of the cause of every timeout.
- Other samples include recorder/commit and per-order ownership checks on the
  loop. Some SQL spans are instead on worker threads; those are not evidence
  that publication still occupies the event loop.
- Largest retained loop-stall sample is 681.08ms; largest retained GC is 20.70ms.
  The retained GC does not by itself explain a 300ms miss. Missing/evicted
  context prevents ruling out unretained pauses or assigning one unique cause.

The final engine/API summaries show no dropped trace-file records or suppressed
incident captures, but discard 200,498/14,866 spans and evict 19,072/19,621 ring
records. These budgets are intentional; retained top-five samples are not a
complete timeline. R2 has no stack instrumentation, so its six episodes cannot
be assigned these R1 causes. Further optimization should first isolate observer
ownership-query cost and executor-report construction. The observer calls
try_acquire (which checks an existing owner) and then check_owner again before
yielding; deduplicating this observer-only round trip is a candidate for a
separate Red/Green measurement. Per-order ownership, intent and fill fences must
remain authoritative rather than cached. No further unmeasured runtime change
is included in this accepted image.

The counterfactual image and temporary registry alias are removed after evidence
capture. No task-owned test containers remain; current tag
`hummingbot-cloudrun:status-publication` is retained. Original Demo volume remains
present and was never mounted by this follow-up; unrelated Docker resources stay
untouched.

## Scope limits

Other owner/recorder/commit calls can still block the loop within existing DB
limits. This change does not establish the unique cause of every historical
probe, guarantee uninterrupted quotes or create a new production acceptance.
Raw original/counterfactual/Green logs: `build/engine-publication-20261102/`.
Demo configuration, volume and account are not modified in this follow-up.
