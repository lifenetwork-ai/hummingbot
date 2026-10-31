# E6: final image verification

Status: **complete**, 2026-10-27 (Asia/Ho_Chi_Minh). Baseline `dd31d134b`.
No push, deployment or live trading was performed.

Linux amd64 image `sha256:739ad5692355965dad5b1ad0f32de17935cbfd1f26c8967626e0d5b4fd5fd191` is 1364804595 bytes
(1.271 GiB). All 51 packaged runtime/config/schema paths
match source, including sixteen SQL scripts. Native regression: 1567 tests plus
eleven subtests. Real PostgreSQL: 196 tests. Swagger and hooks pass. All fourteen
image scenario groups pass, including nine profile/market combinations, the
private-cancel-before-create race, all enhancement policies and compatible
upgrade/rollback to the retained `61c035...` image. Earlier E3-to-61 compatibility
proof remains separate; the vanished E3 cache is not claimed as a fresh rollback
candidate. See [portable evidence](life-mm-e6-verification.json).

## Unchanged full load gate

All feature flags are explicitly enabled in the offline fixture. The bot retains
1 CPU/2 GiB, 100000 historical orders and fills, the original eleven-route
20-read/s population, 100 book updates/s and one fill/s. One history and one
liquidity read/s are additional, separately budgeted reads. Normal bounded runtime
instrumentation is enabled; the opt-in diagnostic tracer is **off**.

| Window | Primary reads | Extra reads | Primary p95 / p99, ms | Errors |
| --- | ---: | ---: | ---: | ---: |
| warmup | 6000 | 600 | 73.24 / 372.74 | 0 |
| mixed | 18000 | 1800 | 91.38 / 464.50 | 0 |
| bursts | 1800 | 180 | 90.79 / 310.85 | 0 |
| soak | 72000 | 7200 | 178.61 / 694.66 | 0 |

The unchanged latency gate applies to the full window. Six retained
one-minute samples have p95 above 500 ms; they remain visible in portable evidence
and are not represented as passing per-minute latency gates.

The sixty-minute random-profile soak records in-window profiles **follow_market, follow_token, sideways**,
with actual overlap established by persisted step events after timing. All three
profiles are independently covered by the nine-case image matrix.
Median RSS growth is 11.81 MiB against the unchanged 64-MiB limit. The final
independent ledger has 4890 unique fills, matching recorder amounts,
prices, fees and positions; identified funding bills match. All five subsequent
fault windows pass. Query bounds remain two for events and one for liquidity;
FE reads add no exchange account request. Bounded runtime distributions retain a
sample tail, rather than claiming complete exclusive per-window populations.

## Confirmed fixes and reuse

| Confirmed problem | Implemented correction | Verification |
| --- | --- | --- |
| Decimal liquidity status failed final shutdown publication | Reuse ordinary JSON normalization | Native Red/Green and image clean shutdown/restart |
| Owner and outflow poller rebuilt the same projection concurrently | Poller waits for existing initialization | Native Red/Green; four rather than seven observed rebuilds |
| Executor shutdown committed per row and delayed commands | Existing recorder persists the group with one lookup and fenced transaction; caches advance only after commit | Native failure retention; real-PG 32-row lookup/fence, update/insert, rollback and stale-owner checks |
| Positive private cancellation preceded REST ACK and left creation pending | Existing cancel handler accepts verified exchange identity; missing/conflicting IDs remain unresolved; fill-only provisional rows still await original parameters | Eight focused PG cases, old-image Red and final image race/recovery case |
| Early-terminal orders lost their original BUY/SELL direction | Write existing PendingBuy/PendingSell history atomically with intent; reuse bulk API side lookup | Spot/perpetual BUY/SELL regressions and API identity before ACK release |

No new production cache, service, owner protocol or schema was introduced in E6.
The harness now reads compact working-order/rate views instead of serializing its
full growing ledger every fill; the independent full ledger and actual-send
counters remain. Burst headroom sends real frames and preserves the existing
500/s threshold. Failed attempts below remain failures.

## Real OKX Demo acceptance

A separate restored database/config copy was used; the original private config and
backup remain unchanged. All nine spot/perpetual/both × follow_market/sideways/
follow_token quote cases pass with strict price guards, maker fee floor and NATR
enabled. Orders are authenticated exchange-confirmed post-only quotes. Commands
retain idempotent receipts, event history is complete, an actual both-market
profile switch is verified, and restart preserves the
same session identity, start, deadline and anchors.

New natural Demo fills compared exactly with authenticated exchange truth:
**spot: 2, perpetual: 6**. Amounts (including perpetual contract conversion), prices and fees in
their original currencies match. No market order, self-match or fabricated fill
was used. The separate natural-fill phase explicitly disables the optional spot
fee floor to permit its narrow-spread paper executions; perpetual fee floor,
strict price guards and NATR remain enabled. This scope does not claim spot
natural fills with its fee floor enabled. Deterministic risk breaches remain
local simulator checks, rather than forced Demo losses.

The first Demo exposed the ACK race and was retained as failed evidence. Before
retrying, one reviewed maintenance transaction repaired only its isolated copy:
private backup first, exclusive owner lock, fresh authenticated canceled/zero-fill
proof, zero open quotes and exact held positions; the original stored intent
restored its side and removed only terminal tracking. This is not automatic HALT
reset behavior. Final reconciliation states: matched, incomplete.
The active fill-window check records spot `exchange_fill_coverage_incomplete`
and local/exchange state changes while fills continue. Those limitations remain
`incomplete`; the separate post-stop comparison verifies all eight settled new
fills against authenticated exchange amounts, prices and original-currency fees.

There are zero remaining test quotes; the test service and copied PostgreSQL are
stopped. Held Demo positions and the copied volume are preserved. No deployment
or live account was involved.

## Residual limits

Cold repeated 50000-fill admissions with diagnostic tracing exceeded read-latency
budgets under amd64 emulation. This successful ordinary untraced full profile does
not certify that stress workload or establish tracing as its sole cause. All
failures and scopes are retained below. The host is arm64 running an amd64
runtime; no Cloud Run staging was requested. BTC-USDT Demo acceptance does not
certify LIFE instrument availability or strategy profitability.

## Historical diagnostics and verification scopes

The following entries retain each attempt's status at the time, including
failed attempts. They do not override the current summary above.

The pre-batch candidate was Linux amd64 image
`sha256:084b78d9bec3dbc7bea4ca40d0d89b6e3ec62f615697f9d14837a779ec319e99`,
1364801133 bytes. Fifty runtime/config/schema paths, including all sixteen SQL
scripts, match packaged source. Native regression passes 1562 tests and eleven
subtests. Full real-PG passes 185 cases, with 31 relevant PG cases repeated after
the two lifecycle changes below. Swagger passes. Raw artifacts: `build/e6/`.

The first final-image matrix reproduced live-liquidity Decimal serialization
failing at SIGTERM publication. A Red/Green regression now proves shutdown uses
the same JSON publication format as ordinary observations, preserving exact
Decimal strings and numeric checkpoint clocks. The final candidate matrix now passes all scenarios, including clean shutdown/restart, all nine profile/market combinations, all optional policy scenarios and compatible upgrade/rollback. Raw result: `build/e2e-offline/20261027-021834-7ad803/result.json`.

A 150-second combined-feature diagnostic with 100000 orders/fills and a real
50000-fill same-time outflow expiry passed read p95 74.49ms / p99 388.79ms and
auxiliary p99 238.91ms, but failed start/switch budgets (15.70s/17.69s) while another
matrix was running. It is retained as a failed diagnostic, not accepted or hidden.
An isolated 60-second diagnostic on that pre-fix image passed all unchanged budgets
(start/switch 12.77s/12.81s), but tracing showed seven projection reconstructions:
one initial plus duplicate owner/poller rebuilds for three starts. A Red/Green
regression now makes the poller wait while the owner reconstructs its guard;
four rebuilds suffice. The final candidate has this single-flight correction.
The final mixed/burst/sixty-minute gate has not yet been accepted.

The original eleven-route read stream and latency population remain unchanged.
Two bounded history/liquidity reads/s are added on top, with separate identical
latency limits and a shared zero-error requirement. The history fixture retains
100000 rows and its historical reconciliation exclusion; 50000 spot fills expire
at one timestamp within a one-day window. No production risk check is bypassed.
Bounded instrumentation measures wall/thread CPU for existing NATR, spread,
liquidity and outflow code. No new production cache or service is added.

The original local Demo database is unavailable. A separate stopped database
copy was restored from the existing private pre-funding backup (320 orders,
5 fills, released owner, stopped intent, no HALT). Original config/backup remain
unchanged. No external Demo request or trading session has been started by E6;
that step follows the final local gates. Any comparison will distinguish this
restored history from newly observed fills and funding backfill.


## Final candidate warmup (passed; long gates still open)

The unchanged 300-second warmup on the final candidate passed all budgets with
all features enabled, 100000 historical orders/fills and a real 50000-fill expiry
burst. Exactly four rebuilds occurred (previous isolated diagnostic: seven).
Start/switch/stop: 9.01s/9.09s/1.30s. Primary stream: 6000 completed reads,
p95 64.50ms / p99 495.56ms, zero errors. Additional history/liquidity stream:
600 reads, p95 74.60ms / p99 178.92ms, zero errors. Market rate 100.00/s and
fills 1.00/s; independent fill/fee/position ledger checks pass. Final retained
fill count proves the synthetic large window actually expired. Event/liquidity
reads retain two/one queries and do not multiply exchange account polling.

Raw gate: `build/mm-load/20261027-023841-7dadc6/result.json`. The measured
startup comparison uses the earlier isolated 60-second probe versus this final
warmup; it is an observed diagnostic comparison, not a statistical benchmark.
Mixed/burst/soak and external Demo are not closed by the warmup result.


## First full attempt: mixed passed, burst fixture underfed (not accepted)

The 900-second mixed window passed (18000 primary and 1800 auxiliary reads,
zero errors, p95/p99 92.92/564.73ms). Burst HTTP also had zero errors (1800 +180
HTTP 200 responses, primary p99 601.82ms), but the emitter delivered 499.51/s
against the existing minimum 500/s after the existing four-frame allowance.
The controls assertion correctly failed and soak never started. This remains a
failed full attempt, `build/mm-load/20261027-023841-7dadc6/result.json`.

The test-only producer now sends 2% extra real frames during the burst; ordinary
100/s load and the 500/s minimum/error/latency budgets are unchanged. Counters
still count only actual sends. Red/Green coverage also fixes the burst helper
leaving its high rate enabled after a failed assertion: it now always restores
the steady feed in `finally`. That fixture correction requires no runtime change. The next full attempt and
its separately confirmed runtime fix are recorded below.


## Second full attempt: command-accept p95 failed (not accepted)

The corrected producer delivered 509.58 actual updates/s. Warmup and mixed
passed, with primary p99 246.32/454.84ms and zero HTTP errors. The burst then
failed command-accept p95 at 539.11ms against the unchanged 500ms limit;
p99 was 540.82ms. All 1800 primary and 180 auxiliary burst reads returned HTTP
200. Soak never started. Raw result: `build/mm-load/20261027-031226-186643/result.json`.

Trace correlation showed event-loop blocking during repeated executor shutdown
writes and ownership-fenced commits. The existing recorder now persists a group
with one lookup and one atomic fenced transaction, retaining the single-executor
entry point. The existing orchestrator advances its caches and removes active
executors only after commit. No cache, schema or persistence framework was added.
Red/Green tests cover one lookup/fence for 32 rows, update plus insert, atomic
rollback, stale-owner rejection and orchestrator retention on failure.

The rebuilt image is
`sha256:61c035dc661b7ee2b88f4fbf306bdcbcf4dec0fbfe2982c1094fe4216e20e427`,
1364801514 bytes; all 50 packaged paths match source. Full native regression
passes 1566 tests plus eleven subtests; full real PostgreSQL passes 188 cases.
Final image matrix and unchanged full load acceptance are rerunning sequentially
to avoid competing local load.

An additional diagnostic used a 2-second refresh instead of the normal
60-second fixture. It exceeded the 1-CPU latency/backlog/fill-rate budgets
(primary p95/p99 1249.75/1789.09ms, 84/90 fills); command-accept p95/p99
was 42.35/206.22ms. This deliberately accelerated workload is a failed stress
diagnostic, not a substitute for the unchanged release gate. Raw result:
`build/mm-load/20261027-035246-54b943/result.json`.


The final batch image now passes the complete fourteen-scenario matrix, including
all nine profile/market combinations and compatible upgrade/rollback. Raw result:
`build/e2e-offline/20261027-035723-4c41a3/result.json`. The unchanged full
all-feature load is running; no load/Demo acceptance is claimed yet.


## Batch image full attempt: latency passed, producer underfed (not accepted)

`build/mm-load/20261027-041543-8d3d29/result.json`: warmup and mixed passed
(primary p95/p99 66.51/369.56ms and 109.03/576.89ms). Burst API p95/p99
172.73/776.95ms, command-accept p95/p99 42.95/138.92ms; all original HTTP
requests succeeded. Batch executor-store spans had p50/p95/p99/max
7.01/18.32/45.50/66.88ms across 127 calls. These observations support the
fenced batch fix but do not close release acceptance.

The burst producer delivered only 460.42/s, so the minimum-throughput assertion
failed correctly and soak did not start. Retained simulator history contained
176 orders, 1290 fills and 14105 requests. A hot fill-selection poll serialized
the entire 2,358,794-byte ledger once per fill; the eight working orders required
only 2706 bytes. Native serialization was 16.8ms versus 0.03ms per response;
these host timings are diagnostic, not container certification.

Test-only `/working-orders` and `/market-metrics` reads now return only the data
needed for fill selection and delivered-rate measurement. The independent full
ledger, actual-send counter, original bot routes/rates, resource ceilings, 500/s
burst minimum, fill rate and latency/error budgets remain unchanged. Red/Green
coverage proves terminal orders/history are excluded without deleting history or
fabricating counters. No new runtime cache or image change. Focused harness tests:
ten pass, including one additional case after the 1566-case native run.
A short default-refresh diagnostic precedes another unchanged full gate.


The compact-reader diagnostic delivered 509.82 real updates/s and passed
command-accept p95/p99 228.91/269.01ms, switch/stop/start
9.08/0.55/7.91s and 3.07s burst drain, with zero HTTP/fill errors. It still
failed primary/auxiliary latency: primary p95/p99 1381.01/2058.50ms. This cold
90-second stress overlaps repeated 50000-fill reconstructions (max 4.82s wall),
initial NATR calculation (812ms wall), and the same-time expiry. It differs from
the declared full profile, whose five-minute warmup precedes mixed/burst. Retain
this limitation: repeated cold admissions into a large active window can exceed
the 500/1000ms read budgets at 1 CPU under amd64 emulation, even though command
completion stays within bounds and the process remains available. Raw result:
`build/mm-load/20261027-044117-d7adda/result.json`. It is not accepted as a
passing release window. The unchanged full profile is running on the same image.


## Compact-reader traced warmup: p99 failed (not accepted)

`build/mm-load/20261027-044439-74182b/result.json`: 6000 primary and 600
auxiliary reads, zero errors; primary p95/p99 231.58/1046.89ms failed the
unchanged 1000ms p99 limit. Auxiliary p99 812.71ms, command-accept p99 41.30ms
and start/switch/stop 9.12/10.25/1.64s passed. No later window ran.

The worst requests overlap a 503ms generation-2 GC. Captured daily-aggregate SQL
ran for 1083ms on an active backend with no observed blocker; other samples show
synchronous owner status publication on the event loop. These are correlated
observations, not exclusive attribution. Cgroup usage was 107.75 CPU seconds
over the 300-second window, including 3.77 seconds throttled. Do not tune GC,
weaken owner fencing or add a new aggregate cache based on this one trace.

A controlled full run now disables only the opt-in diagnostic tracer. The same
image, compact simulator readers, all enabled policies, 100000 rows, original
read/fill/market rates, extra event/liquidity reads, resource ceilings and every
acceptance threshold are retained. Production has no such tracer by default.
Traced stress failures remain visible; a passing untraced gate will not be
reported as traced-soak certification or as proof that tracing alone caused
the observed tail.


## Same-image untraced full profile (in progress)

`build/mm-load/20261027-045416-8fb76f/result.json`: warmup passed
p95/p99 98.91/596.94ms; mixed passed 78.37/305.30ms (18000 primary reads).
Burst passed p95/p99 128.60/590.57ms, command-accept 48.21/50.63ms,
switch/stop/start 3.44/0.58/3.68s, actual burst 509.61 updates/s and drain 2.04s.
All three windows had zero errors. Extra history/liquidity reads passed their
separate unchanged budgets. The sixty-minute random-profile soak is running.
These results are explicitly untraced; bounded ordinary instrumentation remains
active for query, CPU, memory and loop-lag measurements. No source/image or
acceptance-threshold change separates the controlled traced/untraced runs.


The full sixty-minute untraced random-profile soak passed: 72000 primary reads
(p95/p99 100.14/580.97ms), 7200 additional history/liquidity reads
(79.41/244.85ms), zero errors, all growth/accounting budgets passed. Fifty-eight
runtime samples show median RSS growth 13.30 MiB; peak RSS 518774784 bytes.
The independent ledger has 4890 unique fills, matched exactly by the recorder;
positions and two identified funding bills match.

Persisted E4 step events, read after the healthy window, prove actual in-window
running coverage: sideways 599.14s, four follow_market steps of roughly 596s
each, and follow_token 595.83s. The next boundary step is excluded. This evidence
uses the existing event table and adds no in-window endpoint traffic or sampler.
Five fault-injection checks are still running before final local acceptance.


## Real Demo uncovered a recorder ACK race (delivery not closed)

The first connected Demo attempt passed all nine profile/market quote cases,
enabled guards and fee/NATR status, complete event history and exact restart
session/anchor/deadline preservation. During natural maker observation a private
`canceled` update with a real exchange order ID preceded the create ACK. The
recorder saved cancellation but ignored its positive exchange identity, leaving
`creation_pending=true`. The conservative owner then required recovery; cleanup
reported incomplete and the service exited. This is a confirmed runtime bug,
not a credential/network failure or an accepted Demo result.

Authenticated readback confirms the exact pending order was canceled. There are
zero remaining test or unrelated open quotes after cleanup. The original data
and backup remain unchanged; the isolated Demo copy remains halted until review.

Five real-PG regressions cover spot/perpetual cancel-before-create, late create
not reopening canceled state, missing exchange identity still requiring HALT,
and conflicting identity rolling back. Red: three failures/two passes. Green:
five passes. The small recorder fix reuses the existing cancellation handler
and fenced transaction; no task map, new owner protocol or automatic HALT reset.
The simulator now supplies this exact private-terminal-before-REST-ACK order
within the existing MM scenario. New-image matrix/load/Demo gates remain open.


Full regressions after the terminal-ACK fix: 1567 native tests plus eleven
subtests, 193 real-PG cases, Swagger and pre-commit pass. The old image also
fails the new deterministic image case (halt before eight normal quotes); raw
Red result: `build/e2e-offline/20261027-064230-81252d/result.json`.


Rebuilt candidate `ef317c70accc716e0dca15388d38faf934a6acf08b2824dfe1d87c9b14310351`
is 1364802413 bytes; all fifty packaged paths match. Native/PG/Swagger/hooks
pass. The full image matrix includes the deterministic terminal-before-create
case, followed sequentially by the unchanged ordinary full load profile.


The first rebuilt candidate (`ef317c70...`) restores normal quoting through the
new ACK race. Its extended MM matrix then fails observational reconciliation:
terminal orders without a Created event also lack a durable side for the
existing read model. The additional case also crossed the old check's 100-order
scope; that case now runs after the unchanged bounded observational check.
No reconciliation limit or production acceptance rule is relaxed.
Raw result: `build/e2e-offline/20261027-070513-a3f235/result.json`.

A second Red/Green contract covers BUY/SELL on spot/perpetual: Red four failures
(API side missing), three passing negative cases; Green seven cases. Intent now
records its existing PendingBuy/PendingSell status in the same transaction, and
the existing bulk side lookup includes those statuses. No new column, query per
row, fake Created event or external request. The cancellation test also asserts
complete API identity before releasing delayed REST responses. New-image gates
remain open.


Complete terminal-identity candidate:
`daa5f7d0791187d6d1eb3d9c6c33a2d90a9acf9a0012db3cd20f3556e742ad4d`,
1364804041 bytes; 51 packaged paths match, including the changed admin side
lookup. Full native 1567/11 subtests, real PG 195, Swagger and hooks pass.
The ordinary full matrix/load sequence is running on this immutable image.


Candidate `daa5f7d...` passed the full fourteen-scenario matrix, including
terminal-before-create API identity and compatible upgrade/rollback to `61c035...`:
`build/e2e-offline/20261027-074450-2a5b27/result.json`. The old E3 image had
disappeared from Docker's cache; earlier E3-to-61 compatibility proof remains
separate. No final load was started on this candidate after compatibility review.

That review added a recorder compatibility regression: a partial-fill-only row
lacks original order quantity. Red proves unconditional cancellation completion
would prevent a late Create from replacing its provisional quantity. The final
handler therefore requires original parameters (a prior complete record or the
existing durable intent status). Ordinary acknowledged cancels add no query;
the early-cancel path uses the existing order-status index. Eight focused PG
cases now pass, including preserved provisional quantities and unchanged fills.
The final artifact must include this protection before delivery.


## Protected final artifact: complete image matrix passed

Linux amd64 image `739ad5692355965dad5b1ad0f32de17935cbfd1f26c8967626e0d5b4fd5fd191`
is 1364804595 bytes; all 51 runtime/config/schema paths match source. Native
regression passes 1567 tests/11 subtests; real PostgreSQL passes 196. Swagger and
hooks pass. The full fourteen-scenario matrix passes, including all nine
profile/market cases, terminal-before-ACK side/identity, all enhancement policies
and compatible upgrade/rollback to the retained `61c035...` artifact. Raw result:
`build/e2e-offline/20261027-083732-abb9b7/result.json`. Full load and Demo remain
open until their matching-artifact results below.

An earlier same-image run (`20261027-082541-a22ff2`) interrupted the very first
create dispatch when entering the DB-outage case. Missing transport truth
correctly kept HALT and forced an incomplete-cleanup exit. That case now waits
for eight actual live quotes and zero pending ACKs before testing DB loss in an
established runtime. The separate lost-ACK/uncertain-intent cases remain intact;
no runtime recovery rule, reconciliation bound or error budget was relaxed.


## Final Demo scope correction and delivery

The first matching-image Demo run (`build/e6/acceptance-20261027T032829Z.json`)
passed nine quote cases, restart and six exact new fills. It had not exercised
actual switching, and its post-stop observational check correctly reported
`authenticated_connector_unavailable`. This was not counted as a matched check.
The final rerun (`build/e6/acceptance-20261027T033625Z.json`) adds a real
both-market switch, an active matched check, an active fill-window incomplete
check with explicit coverage/state-change reasons, and eight independently
settled exact fills (two spot, six perpetual). No runtime/image change separates
the runs; full local gates remain on the same artifact.

A preceding authenticated GET transiently returned 503/50001 before any copy
maintenance or trading. Subsequent account/bills/archive GETs and all reviewed
copy-maintenance proofs passed. No uncertain create was retried blindly.
