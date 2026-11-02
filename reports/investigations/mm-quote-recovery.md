# Faster quote replacement after confirmed order termination

2026-11-02 (Asia/Saigon). **The avoidable executor shutdown wait is fixed and
locally verified. Protective stops and control-cycle replacement gaps remain.**
[Evidence](mm-quote-recovery-evidence.json),
[plan](../../docs/plans/MM_RUNTIME_LOGIC_AUDIT_TDD_PLAN.md#quote-replacement-follow-up--2026-11-02).

## Cause and scope

`PositionExecutor.control_shutdown_process()` previously slept five seconds
after requesting cancellation, even when terminal callbacks had already made
every tracked order complete. This asynchronous sleep did not block the entire
service event loop, but delayed executor retirement and replacement quotes.
The existing controller/orchestrator cycles then added their own scheduling time.

The earlier [continuity diagnostic](mm-trading-continuity.md) had three groups
of seven empty venue samples. Cancellation batches preceded each group. The
first two are consistent with scheduled refresh; the new baseline directly
captures two refresh batches, matching all 24 executor identities to shutdown
spans and venue terminal order IDs. The historical run did not retain these
executor spans, so this does not retrospectively establish every old event ID.

The historical third group includes a perpetual liquidity guard change at
158.883s and recovery at 160.010s. In `both` mode, existing joint readiness
requires both selected controllers to be ready: one market's guard can stop
both. This protective pause is distinct from the subsequent fixed idle wait.
The new candidate also captures this policy working for a spot-only liquidity
qualification failure; the guard returns to normal about one second later.

## Change and TDD

Runtime commit `eee18456c` keeps the existing terminal-state predicate and
five-second no-ACK deadline. Relevant cancel/fill/failure callbacks wake a
bounded wait; it finishes early only after **all tracked orders** are complete.
Partial confirmation and unrelated callbacks cannot release the barrier or
cause extra cancellation retries. Known-terminal retirement skips the idle
timer. Existing partial-fill holding, precision, admission, ownership and risk
checks remain in place. There is no new service, schema, configuration or cache.

Tests cover callback-before-registration, partial/unrelated events, no ACK,
18-decimal held quantity, cancellation cleanup, deadline failure and notification
failure. Replaying the committed legacy module yields five failing regression
cases; the final broad native and PostgreSQL suites pass. Prototype issues found
by tests, and the bare-constructor logging fixture adjustment, are retained in
raw logs rather than counted as additional confirmed legacy runtime defects.

Measurement commit `ffc32f7ec` adds bounded executor identity/start/end and stop
proposal records to the existing opt-in diagnostic. A separate whole-probe
8MiB/128-queue archive permits correlation outside private health-probe episodes;
production decisions and guard evaluations are unchanged.

## Matched local comparison

One sequential baseline/candidate pair uses identical 17-file fixtures, two CPU
limits, 2GiB bot memory, TTL15, 100,000 historical orders and 100,000 fills.
Each measured window lasts 180s with enhancements, volatility defense and operator
reads enabled. Both artifacts run under amd64 emulation on the arm64 host.

| Observation | Baseline `4ef2c50a` | Candidate `660bd38b` |
| --- | ---: | ---: |
| Matched pending shutdowns | 24 | 48 |
| Median time from last venue terminal to shutdown-wait return | 4,993ms | 99ms |
| Maximum of that interval | 5,003ms | 225ms |
| Empty aggregate quote samples | 14 / 180 | 7 / 179 |
| Longest first-to-last empty-sample span | 6.023s | 2.006s |
| API p99 | 90ms | 127ms |
| Unexpected API errors | 0 | 0 |
| Reconciled synthetic fills/original fees/positions | 180 | 180 |
| Engine probe failures/recoveries in window | 3 / 3 | 1 / 1 |

Both diagnostics pass unchanged existing budgets. This is evidence for removing
the fixed wait, not a general API-speed claim or proof that residual probe causes
are solved. Different later guard/refresh trajectories mean counts are not a
controlled one-for-one comparison of all trading outcomes.

Baseline refresh gaps contain seven empty samples each, spanning about six
seconds. Candidate refresh gaps contain two and three empty samples, spanning
about one and two seconds; an intervening readiness/guard gap contains two.
The post-terminal joins use local simulator timestamps and include callback
delivery/scheduling. They are not actual OKX latency guarantees. The original
15-second stop-loss cooldown does not explain these refresh/hold intervals.

Point samples do not establish exact uninterrupted absence between samples,
nor two-sided coverage whenever at least one order exists. Candidate maximum
sample spacing is 1.640s, versus 1.092s baseline. Observer clock/I/O/tracking
errors and file drops are zero. Lifecycle loss counters are zero at the final
window export; its final-close summary is absent, so a teardown flush is not
certified. Recent-ring evictions remain explicit, separately from file loss.

## Verification, artifact and remaining acceptance

- Native regression: **2,356 passed plus 11 subtests**; PostgreSQL: **296 passed**;
  Swagger check passes. Dependency warnings remain in raw logs.
- Packaged V2 spot, V2 perpetual and LIFE MM lifecycle groups all pass on the
  same candidate manifest `fefa2546`.
- Candidate image `sha256:660bd38be0014fc83ffcade00e0909b07e4c0402abbb0c566e3041796429f32a`
  retains the baseline dependency layers; exactly one of 1,155 reviewed
  runtime/config files changes: `position_executor.py`. All 1,288 frozen
  measurement/runtime source files remain unchanged through the pair.
- The first E2E launcher attempt failed before scenario measurement because
  `registry:2` had been deleted. Restoring it and rerunning the ordered sequence
  succeeded; the launcher failure remains recorded as an environment issue.

This image has **no new full R1/R2, sixty-minute soak, connected Demo or Cloud Run
acceptance**. Older image gates retain their original scope. Production quote
coverage policy/SLO still needs to distinguish normal replacement gaps from
protective pauses; this work does not promise uninterrupted quoting or remove
guards to achieve it.

Raw checks and joins: `build/mm-quote-recovery-20261102/`. Baseline:
`build/mm-load/20261102-213351-88ecb6`; candidate:
`build/mm-load/20261102-213756-2d03e0`. Owned containers/networks and temporary
registry tags are removed. The two diagnostic images remain for reproduction;
original Demo volume/private configuration and unrelated resources are untouched.
