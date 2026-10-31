# Fixed-image full release revalidation

Status: **in progress**. The operator requested a complete local release and
continuous-hour soak for the fixed image after the real OKX Demo filled-restart
acceptance. The older complete gate on image715a remains scoped to that image.
Machine-readable [evidence](e526-release-evidence.json) records each stage;
raw logs and large artifacts are under ignored `build/e526-release-current/`.

The tested image is `sha256:e526118b94324ab4a116839c171482fffa4c3e66adc5effa626d2c0242172454`,
cached manifest `sha256:566788f22f8c86f1a7df38088bf23557baf7d1c16cff2c4b411ed2f64e4cb918`,
runtime source commit `783d35c69`. Tests use an isolated simulator and disposable
PostgreSQL; the persistent Demo stays stopped. No Cloud Run deployment is implied.

## Completed preparation and regression

At the start the Mac was on AC power with its lid open. The runtime image and
all ten reviewed module hashes match the native checkout. Native regression
passes **2,047 tests and 11 subtests**; disposable PostgreSQL passes **268 tests**.
`make swagger-check` passes. In the shipping image, the API's startup GC policy
retains automatic collection and reclaims 5,000 later cycles without refreezing.
The four persistent Demo ledger/position fingerprints and stopped control state
were captured for comparison after the run.

## Full image E2E

All **17 E2E groups pass**, including the packaged ABI/JIT/TLS/SDK/size smoke,
seeded filled restart, nine MM mode/profile cases, guarded availability and
compatible previous-image upgrade/rollback preserving economics. Results:
`build/e2e-offline/20261030-141530-9aeb28/result.json`.

## Shipping process recovery

The ordinary shipping entrypoint passes API crash/repeated-crash/hang and engine
crash/hang recovery, retaining HALT where appropriate, reaping old processes and
releasing ownership at shutdown. Evidence:
`build/service-supervision/20261030-145208-f3d592/result.json`.

## First full load attempt — failed and retained

Run `build/mm-load/20261030-145524-a2b39f/` passes warm300, mixed900 and
burst90, with zero unexpected errors and exact ledgers. The continuous-hour
soak fails the unchanged 1,000ms p99 budget: primary **1,086.76ms**, auxiliary
**1,137.27ms**; p95 is 106.86/113.37ms and unexpected HTTP errors are zero.
All 4,890 exchange/recorder fills and positions match, and both funding bills
match. Random coverage includes all three profiles.

The 57 minute samples pass the original retention checks: max gap66.35s, stable
supervisor/engine/API PIDs, and no RSS/thread/descriptor/backlog violations.
Engine first/last-five median RSS is 415.73/427.37MiB; API is 303.00/303.54MiB.
This does not prove that future workloads cannot leak memory.

The slowdown clusters in the final ten minutes and affects many endpoints,
including lightweight reads. Engine and API event-loop delay maxima first
observed near minute49.7 are 8.21s and 2.91s respectively. Therefore this run
does not prove that API load cannot delay the engine, even though no fills were
lost and no HALT occurred. Instantaneous minute-level DB samples showed no lock
waits; that observation does not exclude shorter database contention. CPU
throttling and host activity also changed near the episode, but the retained
untraced data do not identify one root cause or establish causation. Do not
reinterpret this attempt as a pass or attribute the older p99 incident to it.

All six fault cases separately pass on the same image/configuration at
`build/mm-load/20261030-161919-6d9c57/result.json`: feed, volatility defense,
owner restart, queued expiry, delayed DB and DB failure. This separate result
does not satisfy the failed all-profile gate. At the operator's request the
environment is kept as-is; repeat the full unchanged gate and retain all attempts.

## Second full load attempt — failed and retained

Run `build/mm-load/20261030-162807-3036fa/` passes warm/mixed/burst latency and
the continuous-hour latency budgets: soak primary p95/p99 **75.86/499.88ms**,
auxiliary p95/p99 **87.64/493.24ms**. All 72,000 primary and 18,000 auxiliary
reads return HTTP200; 4,890 exchange/recorder fills, positions and funding match.

The run nonetheless fails the unchanged zero-error gate: 15 daily statistics
reads explicitly report stale fallback during a 4.21-second interval. PostgreSQL
logs show a 2s statement timeout on the daily aggregate, and the API logs confirm
the bounded last-good fallback. This is a correctly labelled degraded response,
but it is not a healthy statistics result. The retained minute samples do not
establish why that aggregate exceeded its deadline or prove that the engine was
unaffected. Native per-role sampling and the final ledger checks still pass.

The separate six-fault run above passes, but the second full run stops before
its own faults and is not accepted. Diagnose the slow aggregate on isolated
representative data and preserve both failed attempts; do not waive errors,
raise budgets or call an uninstrumented guess a cause.

## Isolated daily-fee measurements

PostgreSQL logs identify the timed-out statement as the original-fee currency
aggregation, with two parallel workers. On a disposable PostgreSQL14 container
limited to the same 2 CPUs/512MiB, the existing 100,000-fill daily report test
passes: 11 sequential reads have p50 **512ms**, observed p95 **1,828ms**. The
sample is small and does not reproduce the exact full-load contention.

At 104,890 synthetic fills, 11 alternating measurements of the fee query show
default parallel execution p50/p95 **269/371ms**; disabling parallel workers
gives **436/1,933ms**, and limiting to one gives **285/654ms**. Do not change
parallel settings based on the unverified hypothesis that they caused the timeout.

A separate eight-sample comparison groups identical fee JSON before decoding:
on repeated fees, original/grouped p95 is **437/200ms**; on distinct fee amounts,
original/grouped p95 is **522/1,365ms**. Both synthetic cases returned identical
fee totals, but the regression with distinct fees is material, so this proposal is
rejected. These isolated samples are diagnostic, not full-load acceptance and not
a production forecast. No SQL/runtime change or relaxed deadline was made.

## Operator-priority reporting isolation diagnostics

The operator accepts slow reporting when trading continues. Two 120-second
controlled tests on the shipping image and separate simulated exchange/PG use a
test-only API hook to trigger a real PostgreSQL 2s statement timeout: one uses a
server-side wait, the other a synthetic CPU-heavy expansion of the fee query.
In each, two exchange fills occurred during the blocked statement and an
independent PostgreSQL observer saw at least one fill already committed while it
was still running. Both complete with 120 matched fills, matching positions,
unchanged engine PID/owner/session, no HALT and clean Stop. Market-update rates
were 99.976/s and 100.007/s respectively, with one fill/s. Fourteen stale daily
responses per run retained the original observation time, had bounded age/retry
metadata, and later became fresh. The observer itself adds some workload.

These experiments show progress in those scenarios; they do not prove zero
latency impact, isolation from arbitrary SQL or Cloud Run production capacity.
Engine event-loop instrumentation showed delays elsewhere in the measurement
buffer, without establishing their cause or timing relative to the query. The
first wait-only attempt `build/mm-reporting-isolation/20261030-182447-b02fbe/`
failed its unchanged update-rate threshold (99.610/s). A supervisor
`child_unresponsive` warning appeared after the measured window near cleanup;
causation is not established. The observer was subsequently changed from frequent
Docker polling to log streaming without relaxing thresholds. Passing wait and CPU
results: `build/mm-reporting-isolation/20261030-183110-050c8f/result.json` and
`build/mm-reporting-isolation/20261030-183711-3cf10b/result.json`. In the wait
run, six guard rejection ERROR logs during bootstrap Stop preceded the measured
workload; rejected orders did not reach the exchange.

## Additional diagnostics, not release acceptance

An ordinary-entrypoint diagnostic using the failed second baseline also fails:
warm primary p99 952.50ms; mixed primary/auxiliary p99 1,708.64/1,730.24ms and
backlog drain 10.036s against the 10s limit. It has no unexpected errors and
1,200 matching fills and positions. It lacks engine event-loop measurements and
cannot certify a passing baseline. Its slow responses include multiple endpoints,
not just the aggregate. Result:
`build/service-supervision/production-control-20261030-180227-f1b35f/result.json`.

A separate 180s instrumented trace probe also fails its p99 budget at 1,221.80ms
(primary). It records up to 1,412ms API DB-worker queue wait, 780ms SQL and
4.14ms pool checkout wait in selected spans, but these maxima are not additive.
The tracer dropped 149,654 spans and the PostgreSQL sampler had one error;
instrumentation also adds overhead. These measurements cannot establish a unique
cause for either prior soak failure. Result and trace analysis:
`build/mm-load/20261030-184306-f9aa3c/`.

## Remaining gates and Demo preservation

Both unchanged full release attempts failed; the full warm/mixed/burst/hour/fault
gate and matched ordinary-entrypoint control remain open. The separate six-fault
run and the bounded isolation diagnostics do not close those gates. No latency,
memory, retry, rate, error or sampling budget was relaxed, and no unproven runtime
tuning was applied. The four persistent Demo ledger fingerprints and stopped,
no-HALT control state match their starting baseline. Exact progress is tracked in
the [acceptance checklist](../../docs/plans/OKX_DEMO_CURRENT_IMAGE_ACCEPTANCE.md).

The subsequent [engine liveness investigation](../investigations/mm-engine-liveness-investigation.md)
narrows the retained timeline and adds source-level supervisor diagnostics. Those
short tests use explicit source overlays on e526; they do not change either
historical result or certify a new packaged image. The original sustained-failure
and long-lag causes remain unproven.
