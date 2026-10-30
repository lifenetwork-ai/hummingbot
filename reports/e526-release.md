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

## Remaining gates

After investigation, the full warm/mixed/burst/hour/fault gate remains open. Matched ordinary-entrypoint control,
the matched ordinary-entrypoint control and final Demo preservation checks remain
open. No latency, memory, retry, rate, error or continuous-sampling budget has
been relaxed. Exact progress is tracked in the
[acceptance checklist](../docs/plans/OKX_DEMO_CURRENT_IMAGE_ACCEPTANCE.md).
