# Runtime follow-up code review

Date: 2026-11-07 (Asia/Saigon). Reviewed `f82d90a44`; recorder change `c9889da28`
remains unchanged. This is further analysis after the
[new-intent checkpoint TDD phase](../performance/mm-intent-checkpoint-projection.md).
No runtime patch, new tests, DB mutation, image build or load run is made.
[Portable evidence](mm-runtime-followup-code-review-evidence.json).

## Follow-up status

F1/F2 are hardened in `45707b06e`, F3's steady branch in `1f4f19de6`, and F4/F5
fresh exposure/report reuse in `504343b9b`. Follow-up Red/Green tests also reject
non-finite current/held exposure. F6 is measured and deliberately deferred:
normalization and publication detachment retain their distinct correctness roles.
See [remediation and final verification](../implementation/MM_RUNTIME_FOLLOWUP.md)
and [evidence](../implementation/mm-runtime-followup-evidence.json).
The findings below describe the reviewed baseline; they are retained as history.
They do not establish a unique cause for the previous six probe events.

## Findings in priority order

### F1 — P2: malformed stopped outflow checkpoint escapes the observer

`ServiceHost.observe_once()` reads a saved outflow checkpoint when there is no
live outflow guard. `checkpoint_outflow_status()` directly indexes `binding`,
`policy` and `coverage_start_ms`; it has no validation/error boundary.

A controlled stopped-host fixture with `recovery_state.spot_outflow={"policy":{}}`
raises `KeyError` before publication or a scoped recovery reason. It does so even
with `desired_state="stopped"`. This is a malformed stored-state/recovery edge,
not evidence of corruption in the existing Demo or a valid API request causing it.

Source inspection shows that an uncaught exception reaches `run_service()` as
`service_internal_failure`, then process exit/supervisor replacement. The diagnostic
invokes `observe_once()` only; no real process restart is executed. Repeatedly loading
the same invalid checkpoint can therefore keep failing instead of presenting a
stable, explicit operator diagnosis.

Required future behavior: validate/project invalid checkpoints into a clear
unavailable/operator-action status while retaining the original durable data and
blocking affected trading. Do not erase/reset the risk guard or silently treat it
as disabled. Actual guard restoration and persistence failures remain critical.

Sources: `hummingbot/cli/service.py:575`,
`hummingbot/core/spot_outflow_guard.py:314`.

### F2 — P2: drawdown observation exceptions can escape snapshot construction

`ServiceObservations.capture()` calls outflow/drawdown status methods before its
guarded engine/reporting projections. Typed drawdown/outflow serialization is also
outside those guards. The engine/reporting exception handling does not cover these
paths.

Inject an unexpected `RuntimeError` in the real drawdown runner's valuation-ready
dependency. `mm_runtime_ready()` returns false with `readiness_check_unavailable`,
but a subsequent `snapshot()` raises that exception. Normal stale-price `ValueError`
handling already exists and is not the injected case. This demonstrates a fault
boundary, not a healthy live engine crash or a normal-input defect.

Future hardening must preserve the independent fail-closed trading decision and
show structured unavailable/stale diagnostics. A blanket catch around actual
drawdown checkpoint writes, ownership loss or recorder failure would weaken
safety; these are separate critical paths and must retain their existing fences.

Sources: `hummingbot/cli/service_observations.py:10`,
`scripts/v2_with_controllers.py:138`, `hummingbot/cli/service.py:187`.

### F3 — P2 optimization candidate: duplicate steady-session readiness work

`admit_mm_session()` checks `mm_runtime_ready(allow_bootstrap_start=True)` and later
`mm_runtime_ready()` even when the session is already running and bootstrap is
absent. The method contains zero `await` expressions; this steady branch performs
no session write between its two traversals.

With real ServiceHost/LIFE controller methods and an in-process provider fixture,
one steady admission runs reference updates twice per controller and exposure
checks twice per controller: four of each across spot/perpetual, with zero session
writes. This confirms duplicated local computation; it does not measure latency
savings or establish a probe cause.

This is the first narrow performance candidate: remove only redundant steady
work, keeping the final strict readiness check. Preparing/bootstrap transitions
need validation around durable admission and anchor changes. Per-order and
after-throttle checks must remain fresh; no cross-call authority/readiness cache.

Sources: `hummingbot/cli/service.py:314`, `controllers/market_making/life_mm.py:505`.

### F4 — P2 optimization candidate: valuation readiness builds complete DTOs

`ExecutorOrchestrator.mm_valuation_ready()` obtains each resident executor's full
`executor_info` to inspect `filled_amount_quote`. Full DTO construction also evaluates
PnL, fees and custom information. A direct call over twelve fixture residents
constructs twelve full DTOs, outside `get_all_reports()`' existing one-DTO-per-report
reuse.

The drawdown runner calls this path through `_mm_report_values()`; status/readiness
calls can therefore repeat the traversal. This path matters with drawdown enabled.
The accepted six-probe baseline had drawdown/outflow disabled, so it is not an
explanation for those six events. DTO counts use fixture executor getters and real
orchestrator traversal; they are not real PositionExecutor CPU profiling.

Consider narrower current-exposure reads only after tests cover filled/held
positions, finite amounts, missing valuation and changes since the previous report.
Reusing an old report must not conceal newly filled exposure or a stale price.

Sources: `hummingbot/strategy_v2/executors/executor_orchestrator.py:274`,
`hummingbot/strategy_v2/executors/executor_base.py:113`.

### F5 — P3: held-position summaries are built twice per unified report

`get_all_reports()` calls `get_positions_report()` and then performance generation,
which recomputes each held position's summary. A warmed one-position fixture records
two `get_position_summary()` calls and two local cached-price reads in one report.
These are not demonstrated SQL/REST N+1 queries.

A future per-report reuse can follow the existing executor DTO pattern, while
preserving scope filters, fees and fresh marks on the next call. Current timing is
unmeasured; do not add a resident cache just for this duplicate.

Sources: `hummingbot/strategy_v2/executors/executor_orchestrator.py:891`, `:908`, `:937`.

### F6 — P3 measurement lead: repeated status normalization/copying

`capture_reporting()` deep-copies previous reporting and performs a JSON round trip.
`_prepare_status_publication()` then performs another JSON round trip for the complete
snapshot. The real empty-core path records two dumps/two loads. This establishes
structure, not the duration or dominance of serialization under current load.

The inner path rejects non-finite float data; the outer path detaches loop-owned
references before a worker uses them. These are real correctness requirements.
Profile representative payloads before combining copies; tests must preserve NaN
rejection, stale/unavailable behavior and mutation isolation during slow/cancelled
publication. No new serializer, role cache or Redis is justified by these counts.

Sources: `hummingbot/cli/admin_observations.py:9`, `hummingbot/cli/service.py:777`.

## Verification and recommended sequence

The native component diagnostic reproduces the two escaping-exception cases and
the four structural counts above. It uses existing fixture controllers/provider/
owner objects; no DB, HTTP, actual OKX or OS process restart is performed. It does
not claim new end-to-end fault acceptance or attribute any old timeout.

Fifty existing native tests pass across MM service, service availability, admin
observations, report snapshots and drawdown reports. They do not cover all the
identified fault boundaries. No new regression cases are added in this review;
the controlled fault injections must become Red/Green tests before fixing them.

Prioritize F1/F2 availability diagnostics, then F3's narrow steady branch. Measure
F4 and F5 before optimizing, and leave F6 until payload profiling justifies a
change. Preserve ownership, persistence and risk-failure behavior throughout.
The previous image/load qualifications remain unchanged; this analysis adds no
production deployment claim.
