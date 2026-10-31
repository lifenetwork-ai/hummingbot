# Service availability audit remediation — TDD

Baseline audit: [five findings](life-mm-availability-audit.md). No cloud deployment or
live trading is part of this work. Retain the original audit/probe evidence.
[Portable verification summary](life-mm-availability-remediation-evidence.json).

Status: complete. All five TDD fixes, regression, immutable-image E2E, continuous
sixty-minute load and six post-hour fault gates pass. Earlier interrupted and
failed attempts remain recorded below; their root-cause limits are unchanged.

- [x] AUD-001: Immediately fence shutdown new orders/account setup; preserve cancels.
- [x] AUD-002: Isolate optional PostgreSQL statement/lock failures; preserve mandatory failures.
- [x] AUD-003: Bound established DB I/O and prove fenced recovery under response loss.
- [x] AUD-004: Bound API admission and prioritize control without increasing DB concurrency.
- [x] AUD-005: Supervise the required executor-action consumer.
- [x] Re-run regression, real PostgreSQL, Swagger and a newly verified immutable image (all 16 E2E groups).
- [x] Reject discontinuous or unverifiable soak timestamps with Red/Green coverage.
- [x] Re-run the unchanged healthy/fault load gates on the final source/image.

Each fix records Red/Green before its local commit. Prior image/load results remain
historical, not acceptance for modified runtime.

## AUD-001

Red: seven new process-shutdown cases failed before implementation. Green: 39
focused shutdown/service/availability/lifecycle tests pass. The owner now has a
process-lifetime shutdown latch and a stop-event predicate, checked before and
after its synchronous ownership query for new risk. Cleanup closes order/setup
permission before awaiting optional tasks; cancel/read fencing remains active.
Raw logs: `build/availability-fixes/aud001-red.log` and `aud001-green.log`.

## AUD-002

Red: three new real-PostgreSQL cases failed (daily row lock, canceled optional
statement and guard-history table lock); five fatal-error classification cases
already passed. Green: 14 focused optional/critical publication/event tests pass.
Only SQLSTATE 55P03/57014 on a non-invalidated connection may degrade an optional
write after its savepoint rollback. Lost connections, mandatory writes/commits,
unknown operational errors and recovery rules remain fatal. Daily observation
backoff is reused. Raw logs: `aud002-red.log` / `aud002-green.log`.

## AUD-003

Red: query, commit, rollback and streamed-fetch response-loss cases fail before
the driver change. The corrected owner worker also reproduces a >4.5s stall with
the exact engine factory from `5c8bc5575`, separately from its initial setup error
(`aud003-owner-baseline-red.log`).
Green: 27 real-PostgreSQL cases pass, including query, commit, rollback, streamed
fetch and owner loss, optional statement failures and migration tests; six native
deadline tests pass. The owner worker fixture explicitly registers existing ORM
models before acquisition (its initial setup error was not a runtime defect).
The existing Psycopg wait-callback mechanism bounds managed runtime operations to
3s and discards the physical session on failure. Owner loss remains latched and
its replacement requires recovery. Bootstrap retains its longer total budget.
This bounds synchronous stalls; it does not make DB work asynchronous. No SQL,
DSN or credentials enter the timeout event. Raw logs: `aud003-red.log`,
`aud003-green.log`, `aud003-unit-red.log` and `aud003-unit-green.log`.

## AUD-004

Red: two new native tests and the seven-slow-reads/Stop HTTP probe fail before
the change. Green: nine native executor/cache tests and four real-PostgreSQL
control/capacity/workflow tests pass. A small dispatcher reuses the standard
two-worker thread executor, admits at most 32 outstanding jobs (12 reports),
and puts commands/individual command receipts ahead of pending reports. Already
running jobs retain their capacity after client cancellation. Close cancels the
pending queue and joins running workers; exceptions release admission normally.
The HTTP probe accepts Stop and reads its receipt each within 2.7s despite seven
1.8s reads, retaining an available owner connection. Pool size stays five.
Raw logs: `aud004-unit-red.log`, `aud004-pg-red.log`, `aud004-unit-green.log`
and `aud004-pg-green.log`.

## AUD-005

Red: four new canceled/failed-consumer cases fail; the healthy/intentional-close
case already passes. Green: 36 focused consumer, availability, shutdown and
service tests pass. The cheap final runtime gate now checks the existing action
listener task. A stopped listener immediately blocks new risk before the next
observer tick; the observer uses the existing recovery latch and teardown rather
than inventing a second consumer or restart mechanism. Ordinary shutdown does
not create an operator incident. Raw logs: `aud005-red.log` / `aud005-green.log`.

## Final-source verification

`make test-cloudrun-regression`: 1,857 passed plus 11 subtests, including six
additional harness-continuity cases. Bot runtime source remains `04cca6caf`.
Full disposable PostgreSQL suite: 257 passed. Swagger check passes.
The first full PG run exposed historical bulk-fixture operations with explicit
30/60s SQL budgets but no corresponding client budget. Only those seed/baseline
measurement scopes now use the existing bootstrap budget context; runtime read
and fault budgets are unchanged. The corrected full suite passes without skips.
The initial failure is retained in `postgres-setup-green.log` (a failed attempt,
despite its original filename); current results are in `postgres.log`.

Image `be3efe46…` matches all 29 selected source files and is pinned to a local
registry manifest. All 16 image E2E groups pass in one invocation, including
compatible-image takeover/rollback against `c924030a…`. The full unchanged load
profile and six post-hour faults also pass. No real exchange requests, Demo bot
restart, cloud deployment or persistent Demo DB changes were
performed. Driver stalls are bounded per operation; pool checkout, initial
connection and reconnect attempts retain separate budgets. Mandatory persistence
or owner loss still fences trading and may deliberately restart the service.

### Control-burst admission follow-up

Before starting the hour gate, review of the unchanged load client revealed its
explicit ten-reader plus ten-idempotent-command burst. A new test reproduces the
initial 16-total admission limit rejecting four valid queued controls. Increase
only the bounded total to 32; reporting remains capped at 12 and execution at two
workers. Tests also prove the 33rd outstanding control/read job is rejected and
canceled running awaiters do not release capacity. Six executor/admission tests
pass. Raw logs: `aud004-burst-red.log` / `aud004-burst-green.log`.

The first image `e8bb8897…` E2E invocation was deliberately interrupted after
finding this incompatibility; its partial passes do not count as final
acceptance. Retain its artifacts and repeat verification on the corrected image.

## New-image load verification

All results below bind image `be3efe46…`, pool size five, two API DB workers,
100k historical orders/fills and the unchanged enabled-feature workload.
The separate diagnostic probe and all six preflight faults pass. The structured
ERROR review of all 16 E2E groups classifies 55 records within explicitly induced
schema, ownership, recorder, valuation or final-send-rejection fixtures; no
unclassified message type was found.

Final full-profile run: `build/mm-load/20261029-051533-6462ca/result.json`.
The application has one CPU / 2 GiB, PostgreSQL two CPUs / 512 MiB and the client
and simulator one CPU / 512 MiB each. The amd64 application runs emulated on the
ARM host. Tracing is disabled for this acceptance run.

| Healthy window | Seconds | Read p95 | Read p99 | Unexpected errors |
|---|---:|---:|---:|---:|
| Warmup | 300 | 295.32 ms | 800.26 ms | 0 |
| Mixed | 900 | 196.28 ms | 575.65 ms | 0 |
| Burst | 90 | 153.07 ms | 475.97 ms | 0 |
| Continuous soak | 3,600 | 190.73 ms | 674.21 ms | 0 |

The soak completes 72,000 main plus 18,000 auxiliary reads (auxiliary p99
676.97 ms), all HTTP 200. It has 58 actual runtime samples, with a maximum gap of
65.70s; the unchanged continuity and resource budgets pass. First/last-five
median RSS is 422.32 → 431.79 MiB, growth **9.46 MiB**. Pool-checkout p95 medians
are 5.65 → 6.97 ms; peak book backlog is two. Independent cumulative exchange and
recorder ledgers both contain **4,885 fills**, with matched positions, two
identified funding bills and three daily snapshots.

Burst verifies ten matching idempotent retries and ten expected stale-version
conflicts; actual Stop takes **0.590s**. These healthy-window timings are separate
from the adverse seven-slow-reads priority test. Existing rate gates apply to
fixed windows; the random soak retains its original intentional step-transition
rate scope. This random draw visited `follow_market` and `sideways`; it does not
claim all three scenarios in this hour. All three profiles have separate E2E
coverage in the completed 16-group invocation.

All six post-hour fault assertions pass: stale feed, volatility defense,
owner restart, queued-command expiry, delayed DB and failed DB. Owner restart
and both DB fault windows deliberately produce unavailable HTTP responses; these are
retained in raw and portable evidence, excluded from healthy latency/error gates,
and assessed against their fencing/recovery assertions. This is not a claim of
zero HTTP errors while forcibly restarting the service or removing its database.
Volatility defense preserves positions and verifies reduced allocation, stale
pause submission blocking, durable pause checkpoints and recovery.

The closed-executor diagnostic array is capped at 20 snapshots; its length is
not a count of every resident executor. Actual held-executor IDs peak at ten in
the soak. Memory growth and held-ID observations support only this measured
window, not a multi-day leak-free guarantee.

### Host interruption and acceptance continuity

macOS records **Clamshell Sleep at 02:35:00 +0700 on 2026-10-29**. The run
`build/mm-load/20261029-013006-02b737` contains a **966.889s** gap between
minute samples. It was explicitly interrupted and cleaned up with 48 measured
samples; it is not accepted as a continuous hour and is not classified as a bot
runtime defect. Earlier warmup/mixed/burst results retain their declared scope.
No post-hour faults were claimed for this interrupted run.

Red: six synthetic gap/reversed/missing/NaN/infinite/boolean timestamp cases were
incorrectly accepted. Green: ten focused budget tests pass. The gate now requires
finite, strictly increasing timestamps with no gap over 90s, in addition to the
existing minimum 55 samples and unchanged workload/latency/error/resource limits.
An explicit replay retaining the observed real gap plus synthetic continuation
to 59 samples is rejected as `sampling_continuity`; that continuation is a
diagnostic, not additional measured runtime. Full native regression and Swagger
pass after this host-side harness change; the application image is unchanged.
Raw evidence: `interrupted-soak.json`, `continuity-red.log`,
`continuity-green.log` and `regression-continuity.log` under
`build/availability-fixes/`.

The user subsequently confirmed a lid-open/on-power host. Both conditions were
verified before restarting the full unchanged load profile at **04:41 +0700 on
2026-10-29**, using the same immutable image. A foreground `caffeinate` session
is bound to that command; continuity is still verified from actual sample
timestamps. The persistent Demo PostgreSQL remains untouched. That first resumed
attempt failed the latency gate described below; a later full run supplies final
acceptance.

### Resumed mixed-window failure and scoped diagnostics

The lid-open 04:41 retry (`build/mm-load/20261029-044105-46228d`) stopped at
mixed-window **p99 1,013.10ms**, above the unchanged 1,000ms limit. All 18,000
main plus 4,500 auxiliary reads returned 200; fills/rates, p95 (249.10ms), Stop
(1.429s), memory and query-count budgets passed. This is a failed latency gate,
not a passed hour. Pool-queue p99 was 0.045ms; event-loop p99 was 378.82ms with
1,251ms maximum lag. These observations do not establish a root cause.

Two separate 180s cold diagnostics use identical fixture hashes, the same image,
rates and limits, with GC timing enabled: traced p99 **505.92ms**, untraced p99
**465.71ms**, both zero-error/pass. The traced window records full-GC pauses up to
329ms and delayed handler dispatch, but does not reproduce the 900s mixed failure.
No runtime fix or latency-threshold change is inferred from these short results.
Raw artifacts and trace analysis are retained in `build/availability-fixes/` and
the respective `build/mm-load/` runs. The full unchanged profile restarted at
05:15 +0700 on the same image and completed all healthy and post-hour fault
gates. This successful repeat closes acceptance, while the earlier transient
latency failure remains unexplained; no runtime performance fix is claimed.
The 29 runtime-source hashes and all recorded load-harness hashes were checked
again after completion. Test containers were cleaned up; the persistent Demo DB
was kept and its bot remains stopped.

Subsequent investigation: the [p99 follow-up](../investigations/mm-p99-investigation.md) reproduces
mixed-window latency failures on the rebuilt image with tracing on and off, plus
one daily-statistics SQL timeout. A matched production-entrypoint control passes.
That investigation retains the remaining uncertainty; it does not revoke or
relabel this historical image's completed gate, nor claim a runtime fix or fresh
release acceptance for the rebuilt image.
