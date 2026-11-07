# Current image: latest full-load repeat failed

The latest attempt on **`a6fe88c0…` fails** the mixed-window main and auxiliary
API p99 budgets. The runner stops before burst, Random soak and fault injection.
The earlier full pass remains in Git `dfad4a4d1` at this report/evidence path;
it used the preceding incident fixture and does not pass this new attempt.

## Candidate and scope

- Same amd64 image `sha256:a6fe88c0bba185d27ed68d1fbc1f43dfa39c5a560c80df1f28e720f2afeb16b2`;
  build `745a86e3c`, 1,374,449,982 bytes, tag `hummingbot-cloudrun:runtime-copy`.
- Same cached immutable manifest
  `sha256:7e615f650e8c7a6c5265bef45f685e74aae7b2096ebdf5d895916a8deb20e6aa`.
- Test-fixture commit `dfad4a4d1`: only `test/e2e/offline/load_incidents.py` differs
  among recorded harness sources from the preceding full gate. All **1,224**
  packaged source/config hashes match before and after the attempt.
- Exact baseline: `all --operations --security --incident-diagnostics --cpus 2
  --daily-cache-ttl-seconds 15`, without diagnostic mode, extra stage timers,
  full tracing or MM activity instrumentation. No resource/rate/probe/I/O/pool
  or acceptance-budget changes. No automatic retry or winning-run selection.
- 100k orders/100k fills/100k audit events; 20 main and 5 auxiliary reads/s,
  100 book updates/s and one synthetic fill/s. Bot 2 CPUs/2 GiB; PostgreSQL
  2 CPUs/512 MiB, client and simulator each 1 CPU/512 MiB. amd64 is emulated
  on an arm64 Mac. The host remains on battery during the measured attempt.
- Raw result `build/mm-load/20261107-221644-b1d037/result.json`;
  runner, inventory and analysis `build/incident-lock-load-20261107/`.
  [Portable evidence](mm-runtime-followup-load-evidence.json) retains the failed
  exit, measurements, hashes, earlier same-image checks and scope limitations.

## Measured windows

| Window | Seconds | Main reads | Main p95 / p99 (ms) | Auxiliary p99 (ms) | Unexpected HTTP errors | New simulated fills | Result |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| warmup | 300 | 6,000 | 56.12 / 394.79 | 170.16 | 0 | 300 | pass |
| mixed | 900 | 18,000 | 206.09 / 1,315.91 | 1,169.10 | 0 | 900 | fail |
| burst | — | — | — | — | — | — | not run |
| Random soak | — | — | — | — | — | — | not run |
| five injected faults | — | — | — | — | — | — | not run |

All **24,000 main / 6,000 auxiliary** requests return HTTP200. Both completed
windows meet their fixed market/fill-rate, backlog, command and query-count
checks. The final **1,200 unique simulated fills**, exact fees and positions
reconcile. These are offline simulator records, not OKX Demo trades. The main
and auxiliary p99 limits remain **1,000 ms**; the mixed window exceeds them by
**315.91 / 169.10 ms**, respectively. No sixty-minute retention result exists
for this attempt, so no new leak/soak acceptance is claimed.

## Recovered probes and comparison

| Window | Prior same-image main p99 (ms) | Latest main p99 (ms) | Prior → latest engine probes |
| --- | ---: | ---: | ---: |
| warmup | 315.63 | 394.79 | 0 → 1 |
| mixed | 353.23 | 1,315.91 | 3 → 6 |

The single warmup probe recovers in **1.059 s**; six mixed engine probes recover
in **1.025–1.722 s**. The retained marker inventory has no additional failure
between/before/after these measured windows. Intervals are supervisor
observations, not measured quote downtime. Full MM activity instrumentation is
disabled, so these results cannot establish continuous quotes during each gap.

Four warmup and fourteen mixed runtime samples all record `running`, with fixed
supervisor/engine/API PIDs **1/38/39** and maximum sample gaps **65.45 / 65.52 s**.
Parsed healthy logs contain **zero ERROR/CRITICAL and zero child lifecycle
events**; post-window shutdown also has zero errors. Before measurement there
are **8 ERROR `order_submission_blocked`** records: connector/executor reports
for four distinct locally blocked order IDs, all `SubmissionBlocked`. None of
the IDs appears in the simulator ledger. Their generic safety reason does not
uniquely identify the earlier control transition; keep them explicit rather
than describing every phase as error-free. Six initial child starts belong to
preparation/seed/security startup, outside the measured windows.

## What the retained data establishes

The mixed minute ending at 480 seconds has a **2,596.61 ms p95**, versus about
58 ms in the first minute. Tail latency rises across all sixteen routes,
including `/status` at 1,191 ms p99, order/fill pages around 1,189–1,704 ms and
daily statistics at 1,743 ms. This is not evidence for one uniquely slow
aggregation query. The group of delayed requests raises the overall p99.

Bounded traces include synchronous engine PostgreSQL/ownership waits and
observer/admission work. One ownership check takes **470.23 ms wall / 37.25 ms
thread CPU**; another SQL span is **426.33 / 60.12 ms**. Around the slow minute,
retained API request spans reach **1,462 ms**, so the retained evidence includes
server-side delay as well as client-observed latency. API GC events and waits
also overlap, but overlap is not a unique causal diagnosis. Async inclusive
spans, rolling/cumulative distributions and omitted spans must not be added
or treated as a complete timeline. Engine/API sink drops and incident-limit
suppression are zero at mixed end; detailed child-span omission remains.

The source change removes a tested producer-lock dependency. This long repeat
does **not** demonstrate a whole-system performance improvement or establish
that the lock change caused the regression. The previous short diagnostic's
258 ms p99/zero probes did not predict this longer-window cluster. Do not blame
host power, GC, one query or the fixture without a matching controlled witness.

## Remaining work and preserved environment

The latest gate stays **failed**. Further optimization is paused pending a
controlled before/after witness of the slow request cluster. Preserve fresh
authority/durability checks and all latency thresholds. Once any confirmed
cause is addressed with Red/Green tests, repeat the complete unchanged gate;
the unrun burst/soak/fault phases remain open.

All **10 existing container identities/start states and 14 volumes** are
preserved, including the running Demo service/database. The harness removes
only its disposable containers. No image was built or removed; candidate
`a6fe88c0…`, rollback `e00cef38…` and all existing image IDs remain. No actual
OKX, Google/GCP or deployment operation occurs.

Earlier same-image targeted E2E, enhancement90/activity300 and the original
full pass retain their scope in Git `dfad4a4d1`; the corresponding evidence
entries are labelled as prior measurements. The [source audit](../implementation/MM_RUNTIME_FOLLOWUP.md#combined-image-regression-audit--2026-11-07)
retains the native/TDD and short ABBA results separately.
