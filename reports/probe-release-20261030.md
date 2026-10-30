# Packaged supervisor-diagnostics release — 2026-10-30

Status: verification complete; strict release acceptance remains failed because
the Random soak exceeded the fill-generator drain deadline. Source commit
`e9a1645b7`; packaged image
`sha256:dd602fb2238df0237f2c0aaa532407f31e47cdfbbd25575452b55a021765874c`
is built (1,367,454,040 bytes). Both supervisor module hashes match the reviewed
source. All 17 offline E2E groups pass, including previous-image e526 upgrade/rollback.
Evidence: `build/e2e-offline/20261030-212044-749aa2/result.json`. Shipping
supervisor/API/engine crash/hang recovery also passes at
`build/service-supervision/20261030-215710-5520a1/result.json`. The complete load
run completed at `build/mm-load/20261030-215951-3d41e4` with a failed soak
fill-generator drain; all six fault cases pass. Ordinary control passes as
an explicitly diagnostic comparison because the baseline failed. Portable
[evidence](probe-release-20261030-evidence.json) retains both outcomes.

The operator requested a newly packaged image, full load including a continuous
60-minute soak, and ordinary-entrypoint comparison. This scope preserves strict
budgets and separately reports trading continuity during degraded reporting.
Historical e526 failures and the unproven original liveness cause remain recorded.

The Mac began with its lid open on battery. An idle-sleep assertion is active
during the tests; it does not protect against physically closing the lid. All
exchange/DB fixtures are isolated and use dummy keys. The persistent Demo remains
stopped on e526 and is not replaced.

The all-load invocation uses the existing `--keep-going` option so that a latency
violation does not discard later windows; any violation still fails the final
result. No aggregate slowdown is silently relabelled as a strict healthy pass.

See the [acceptance checklist](../docs/plans/OKX_DEMO_CURRENT_IMAGE_ACCEPTANCE.md).

## Full-load result

Original rates, resource caps and budgets were retained: 100,000 historical
orders/fills, bot 2 CPU/2 GiB, PostgreSQL 2 CPU/512 MiB, total pool 5 and daily
cache TTL 15 seconds. The amd64 image runs under emulation on this arm64 Mac.

| Window | Primary p95 / p99 (ms) | Result |
| --- | --- | --- |
| Warm 300 seconds | 53.92 / 252.14 | Pass |
| Mixed 900 seconds | 63.96 / 408.58 | Pass |
| Burst 90 seconds | 72.23 / 195.82 | Pass |
| Random soak 3,600 seconds | 82.90 / 685.33 | Fail: fill-generator drain/error |

Soak completed all 72,000 primary and 18,000 auxiliary reads with HTTP 200;
auxiliary p99 was 718.56 ms. There were no read errors. The one error was
`fill_generator: backlog_not_drained`: 3,598 synthetic fills instead of 3,600,
with 10.153 seconds to finish/drain against the unchanged 10-second budget.
The cumulative independent exchange/recorder ledger matched all 4,888 unique
fills and positions; two identified funding bills and three daily snapshots
were verified. A missing synthetic fill is not an observed lost recorder fill.
Across all four healthy-load windows, 97,800 primary and 24,450 auxiliary reads
returned HTTP 200. Setup Start/Switch/Stop measured 6.680/6.821/2.933 seconds,
within their existing budgets.

The six fault tests cover feed loss, volatility defense, owner restart, expiry
under queued events, database delay and database loss. Their own recovery/safety
assertions pass; deliberate restarts and database outages do produce request
failures and are explicitly excluded from healthy-window latency acceptance.
In particular, database failures require preserved HALT with no new submissions;
that pass must not be described as uninterrupted trading through database loss.

The last fill before the final Random transition was at 16:22:37.529 UTC.
The engine cancelled the previous step's orders at 16:22:43.758–43.869, restarted
the strategy at 16:22:44–45, and emitted fresh orders and four further fills at
16:22:48.070–48.819. New orders were then cancelled during guard/sizing refresh;
replacement orders appear at 16:22:56.448, after the drain deadline near
16:22:54.4. Thus the retained log establishes a quote gap around the scheduled
handover. It does not establish an engine crash, an HTTP failure, or a lost fill.
The client keeps seeking its remaining synthetic fills after the measurement
window; removing that check or extending the deadline would change acceptance,
so neither was done. The exact guard inputs at that instant were not captured;
do not attribute every cancellation to a confirmed cooldown or guard defect.

The independent exchange ledger narrows the immediate drain cause further:
after synthetic fill 3,598 the client requires a spot SELL. All three fresh spot
SELL orders were cancelled at 16:22:48.698–48.703 UTC; the next three were only
accepted by the simulator at 16:22:56.116–56.210. There were no uncancelled spot
SELL orders at measurement end +5, +9 or +10 seconds. This explains why that
producer could not complete within its deadline. These exchange timestamps
precede the connector log callbacks quoted above. A follow-up should isolate
the handover/refresh boundary and capture the cancellation reason and guard
inputs; do not disable protections merely to achieve synthetic throughput.

There is also an acceptance-contract distinction to resolve: the soak calls
the budget checker with rates reported but not required, because scheduled
Random handovers can pause fills. Nevertheless `load_client.fill_events()`
continues trying to reach `duration_seconds` fills after generation ends, and
its ten-second wait reports this unsatisfied synthetic target as a backlog
error. The API queue was empty at window end. That is different from proving
already-submitted exchange events could not drain. Keep the original failure;
any future producer-lifecycle correction needs a regression for an empty book
at window end, continued detection of actual stuck requests, and an explicit
unchanged-throughput/error-budget comparison. This run made no such correction.

All three profiles ran during the soak. All 58 minute samples report `running`,
with unchanged engine/API PIDs 37/38 (maximum sample gap 65.60 seconds). Engine
RSS median of first/last five samples was 416.18/427.86 MiB; API was
302.80/303.05 MiB. Threads stayed at 11 per role, API FDs at 21 and engine FDs
between 28 and 32. The existing retention checks did not fail; this finite run
does not prove absence of every long-term leak.

Across the measured load-window span, all 57 engine and seven API transient
probe-failure episodes recovered; all 64 recorded first reasons were transport
timeouts, not HTTP 503. The longest observed failure episode was
4.435 seconds for engine and 1.418 seconds for API, below the unchanged grace.
There were no unresponsive/replacement events during that span. However,
scheduled event-loop sample lateness reached 4,003.09 ms for engine and
2,960.21 ms for API. This is not evidence of complete latency isolation, nor
proof that a single function blocked for those entire durations. The original
sustained liveness-failure cause remains unproven.

The Mac was observed charging during the run (76% at approximately 15:45 UTC),
after starting on battery. That change is recorded as an environmental condition,
not assigned as the cause of any result.

## Ordinary-entrypoint comparison and preservation

Run `build/service-supervision/production-control-20261030-232929-970ba2`
passes warm300 and mixed900 on the exact same packaged image and matched
catalogue/client/exchange fixture hashes. This uses the ordinary
`python -m hummingbot.cli.service` entrypoint without the timing wrapper.
Because the full-load baseline failed, the runner copy explicitly labels this
as diagnostic and retains `baseline_passed=false`; it cannot close release.

| Window | Primary p95 / p99 (ms) | Auxiliary p99 (ms) | Result |
| --- | --- | --- | --- |
| Warm 300 seconds | 55.33 / 219.93 | 191.33 | Pass |
| Mixed 900 seconds | 57.38 / 240.13 | 228.02 | Pass |

All 24,000 primary and 6,000 auxiliary requests returned HTTP 200, with zero
errors. All 1,200 cumulative exchange/recorder fills and positions match; the
mixed drain was 0.067 seconds. Peak cgroup memory was 714,428,416 bytes. This
control does not measure internal queue/GC timings or prove long-term retention.
Its fixed profile does not exercise the failed final Random handover.

Persistent Demo order/fill/executor/position fingerprints and control state match
the baseline: stopped, version/acknowledgment 26, no HALT, original e526 image.
The test fixtures are removed; only the original Demo bot and PostgreSQL
containers remain. The task's idle-sleep assertion was stopped. No runtime
source, health grace, load deadline or acceptance threshold changed during this
packaged verification. Mac power history shows no sleep during this run.

## Remaining acceptance work

1. Isolate the final Random handover with a short deterministic regression:
   record desired/actual quote size, reference/spread, guard reason, cancellation
   acknowledgment and executor retirement. Establish whether the second refresh
   is required for protection or avoidable churn before changing runtime logic.
2. Distinguish stopping the synthetic producer from draining already-dispatched
   requests/events. Preserve zero-error checks for real transport/recorder
   failures and report actual generated fill rate. Any test-contract correction
   must retain this failed attempt and explain its comparison with the original.
3. Only then repeat the unchanged release acceptance or explicitly document an
   agreed contract revision. A passing fixed-profile ordinary control cannot
   close the Random soak failure or explain the older sustained liveness event.

No further speculative runtime changes or blind hour-long reruns were started
in this verification scope.
