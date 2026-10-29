# Single-service runtime supervision verification

Status: prior runtime/image/regression and lifecycle checks pass, but the long
load has exposed a real position-checkpoint race during random profile rollover.
A terminal-first checkpoint fix is in TDD verification. A replacement image and
unchanged load/hour-memory/control gates are required; adoption is not accepted.
Local checks do not certify Cloud Run
deployment. The real local Demo remains stopped on
its existing image/database. The earlier [prototype measurements](mm-process-isolation-experiment.md)
retain their original source/image scope; they are not acceptance of this change.

Portable scoped results: [verification evidence](mm-service-supervision-evidence.json).

## Runtime integration

The ordinary foreground service now supervises two processes inside one
container. The engine retains the existing `ServiceHost`, trading core,
PostgreSQL migration, ownership/fencing, commands, recorder and recovery gates.
The API reuses `create_service_app`, handlers, models and `BotStateStore`; its
initialization validates the existing schema without migrating, ensuring state
or acquiring trading ownership. Child roles skip dotenv loading. The API child
receives database/admin settings but no connector/wallet credentials.

`PORT` remains the public ingress port. Engine health binds only to loopback at
the adjacent port (65534 when ingress is 65535). `DB_POOL_SIZE` is the total
ceiling: API two slots, engine the remainder, no overflow. The engine preserves
the configured `ENABLE_AUTO_MIGRATION` flag; API-only restart does not replay DDL.

The tested bounded supervisor policy is shared with diagnostics, eliminating a
second implementation. It physically reaps before replacement, independently
restarts failed children with capped backoff and disables new launches after
shutdown. It probes startup before switching to liveness, allowing initial
imports and the existing 180s migration budget within a 240s startup allowance.
Trading readiness/normal guard waits/HALT are not process-liveness failures.

Definitive configuration/schema failures exit 78 and stop the parent/peer;
transient DB bootstrap failures remain retryable. Shutdown uses a shared child
budget plus an independent nine-second parent deadline. Engine replacement
retains all existing recovery/HALT restrictions; process restart does not grant
permission to trade.

## Red/Green evidence

- Initial role/module contract tests fail before integration.
- Replaying the old policy fails three behavioral tests: startup grace,
  configuration exit handling and a child exiting during termination.
- A real signal injected during mocked `Popen` exposes an integration race:
  the parent initially launched the API after SIGTERM. Recheck stop before/after
  launch; the regression passes.
- **95 selected native tests pass**, including role/pool/port settings, child
  credential/dotenv isolation, API stop during initialization, read-only schema
  validation, transient versus permanent bootstrap failures, logging/redaction,
  actual OS child reaping and existing service/availability tests.
- Logging keeps the existing `service_failed`/`service_exit` event names;
  definitive invalid configuration now intentionally exits 78 rather than 1.

Raw logs are retained under ignored `build/service-supervision/`. Runtime-image,
PostgreSQL/E2E, instrumentation and continuous-hour/load verification remain open
in [R6](../docs/plans/MM_REPORTING_LATENCY_TDD_PLAN.md).

## Instrumentation and actual-role probe

Focused native regression now passes **124 tests**. Additional Red/Green fixes
preserve the legacy OKX public-site setting when removing API connector secrets
and propagate failed/forced child shutdown to the parent's exit code.

The temporary overlay `f0d6d98e…` passes actual production-entrypoint lifecycle
checks: nondefault ingress 8090, loopback-only engine 8091, no API connector keys
or dotenv path, two recorder fills and nonempty positions, independent API
crash/hang recovery, engine crash/hang recovery with HALT retained, physical
reaping, no unexpected orders and clean parent shutdown. Three engine starts
apply 17 scripts each; API restarts apply none. Raw case:
`build/service-supervision/20261029-220641-128082/`.

Both instrumentation probes are retained: the 60s window at
`build/mm-load/20261029-221859-37d704/` fails p95/p99 at **1383.51/2152.84ms**
with zero errors; the subsequent traced 90s window at
`build/mm-load/20261029-223021-9a4898/` passes at **66.46/422.42ms**, zero errors.
The latter includes an extra PG sampling connection and proves instrumentation,
not a unique cause or final-image acceptance. An earlier preflight fails before
measured load because its PID1 assertion expected the old wrapper; that harness
assertion is corrected without relaxing the shipping entrypoint requirement.

Each real role supplies metrics; missing, stale or mismatched samples fail the
measurement preflight. Container memory uses cgroup current/peak, including page
cache; per-role RSS is separate. Distribution aggregation reports the maximum
per-role quantile, explicitly not an exact pooled percentile. Continuous-hour
checks additionally reject role PID changes, RSS growth over 64MiB, thread growth
over four or descriptor growth over eight. HTTP/error/latency gates are unchanged.

The selected local/template policy is two CPUs, 2GiB and explicit existing daily
cache TTL15; the shared code default remains five seconds. One container still
fits one service. Cloud Run's [container contract](https://docs.cloud.google.com/run/docs/container-contract)
requires amd64 support and ingress on `0.0.0.0:$PORT`, allows four minutes for
startup and ten seconds after SIGTERM; the current template keeps continuous CPU.
Local probes do not certify Cloud Run deployment.

## Final Dockerfile artifact

Built from clean commit `78901e673` with the ordinary Dockerfile `cloudrun`
target, not the temporary overlay:
`sha256:4da2f008f111603e92bc74d6e5b64ec9da29a8669af384abb33b32aa6ba571d9`.
It is amd64, UID/GID 10001, 1,367,425,805 image bytes; CMD remains
`python -m hummingbot.cli.service`. Five changed shipping modules match current
source SHA-256 exactly; subsequent commits affect isolated harness/docs only.

The full native selection passes 2,009 tests plus 11 subtests; Swagger and
deployment-template review pass. Final-image runtime smoke passes Numba JIT,
TA-Lib, TLS trust, 63 connector registrations, 17 packaged SQL scripts, removal
of build/test tools, JSON severity/context/redaction and fatal startup logging.
The PostgreSQL/E2E/load/hour/control gates remain open until their own retained
results pass; this artifact metadata does not imply acceptance.

PostgreSQL regression initially reports **257 passed / two failed**: the native
subprocess test helper passed a config directory to the new CLI parser. Captured
logs confirm the argument rejection. Reexecute the same isolated config/network
bootstrap in both actual children and parse only role arguments. The three
service/API/schema lifecycle checks then pass; no production code changes. Raw
logs retain both original failure and focused recovery under
`build/service-supervision/postgres-{regression,lifecycle-retest}.log`.

## E2E finding: idle API SIGTERM does not wake its selector

The first rebuilt image's full E2E reaches funding after passing schema, Spot,
V2 Spot/Perpetual, the nine LIFE mode/profile combinations and continuous Random.
It then **fails**: engine cleanup is clean, but an idle API is forcibly killed
after the parent's 7.5s budget. The parent correctly returns exit 1. Original
evidence `build/e2e-offline/20261029-232141-ef1252/` remains failed; image `4da2…`
is not accepted as the final runtime.

A minimal real OS-process test reproduces SIGTERM while an asyncio loop has no
timers or pending I/O. The Python signal handler's direct `Event.set()` schedules
a callback but does not wake the blocked selector. The old engine normally has
periodic work; the new idle API exposes this shared shutdown assumption. During
parent shutdown, its normal health probes also stop, removing that incidental
wakeup. Red: the child exceeds its one-second clean-exit assertion. Green:
capture the running asyncio loop at signal installation and schedule the stop
through `call_soon_threadsafe`, which writes the selector's self-pipe. Synchronous
parent events remain immediate; the independent hard deadline is unchanged.

All 95 focused service/supervision/logging/availability tests pass with this
fix. A new Dockerfile build and final-image gates are required; the previous
partial E2E success is not a substitute.

The replacement Dockerfile image is
`sha256:f5e7ab3282688787876623d176f774370df3dba8ecbf8eb8ae9ca714ad2a3d25`
from `6de4001cd`, 1,367,426,271 image bytes. All five changed shipping-module
fingerprints match the working runtime source. Full native regression passes
**2,012 tests plus 11 subtests**; three real-PG lifecycle/API/schema checks pass
again, and Swagger/runtime smoke pass.

The identical isolated idle-signal probe is also replayed on both actual Linux
shipping images: old `4da2…` waits for its 3s hard deadline and exits 1
(3.12s observed); new `f5e7…` exits 0 in 0.72s, including Docker command overhead.
No health traffic or periodic polling is added. Raw causal evidence:
`build/service-supervision/linux-idle-signal-evidence.json`.
Full E2E and load/hour/control verification of this replacement remain required.

The replacement's first full E2E passes funding (all relevant normal exits 0),
availability, quote guards, drawdown and outflow, then fails in the bootstrap
test's orchestration. It retains a 30s listing episode across a cold restart,
but waits for the new Start receipt to succeed before supplying the external
depth needed to leave `bootstrap_expired`. That is a circular test dependency;
the service correctly remains available and blocks quotes. Evidence
`build/e2e-offline/20261030-000203-0744c6/` stays failed.

Red/Green adds an explicit test-harness accepted-only command option, preserving
the same API payload/idempotency checks. The bootstrap test submits the intent,
asserts the original episode/deadline and blocked quotes, supplies single-leg
then joint external qualification, and finally requires the **same receipt** to
succeed. Production deadlines, guard policy, receipt semantics and image are
unchanged. Twenty-nine focused harness checks pass; actual-case replay and full
E2E/load gates remain required.

Final-image ordinary-entrypoint lifecycle replay passes at
`build/service-supervision/20261030-003706-4b55b3/`: nondefault public 8090/private
loopback 8091, filtered API environment, real retained positions, API crash,
repeated crash and hang, active engine crash and HALTed-engine hang. API faults
preserve the engine/owner/session; engine replacement physically reaps the old
process, advances owner generation, preserves HALT/positions and submits no new
orders. Normal quote-refresh boundaries remain included. Three engine starts
apply 51 scripts; API restarts apply none. Parent shutdown releases ownership in
3.28s. This functional run overlaps another isolated functional E2E environment;
it is not a calibrated performance result. Load/hour/control gates remain open.

The corrected full E2E at `build/e2e-offline/20261030-003620-4745f4/` now passes
**all sixteen groups**, including compatible image upgrade/rollback. Its manifest
is `sha256:cee15b5a2644cb0ff27366ba27a784b7ee775f5987afe679a10163fb17f4c578`,
matching selected image `f5e7…`. The earlier full E2E failures retain their
original results. The R6 image/regression phase is complete; the unchanged
all-feature load/hour gate starts with two CPUs, 2GiB, five total DB slots and
explicit daily TTL15. No other benchmark/build/regression job runs alongside it.

## Measurement finding: emulated cross-process proc inspection retains FDs

The first full load at `build/mm-load/20261030-011239-d491a4/` passes its 300s
warmup (p95 56.26ms, p99 408.83ms, zero errors), then is deliberately interrupted
during mixed after minute samples show roughly twelve extra descriptors per
minute in each instrumented child. Two read-only FD listings show that the
added handles point to the other child's `/proc/<pid>/auxv`, not DB or exchange
sockets. Those inspections are recorded; this run is not hour/load acceptance.

A controlled identical two-child sampler with no DB, exchange or Hummingbot
imports reproduces the effect inside the same amd64 image/emulation: twenty old
samples add twenty auxv FDs per child; twenty corrected samples add zero.
Raw proof: `build/service-supervision/fd-sampler-repro-evidence.json`. This
attributes the observed growth to repeated cross-process cmdline inspection
in the emulated measurement path; it does not establish a native Cloud Run or
trading leak.

Long-lived probes now use their already-known role/PID and the supervisor's
exactly-two-direct-children contract, with physical reaping before replacement.
Unexpected/missing children fail measurement admission instead of guessing.
External one-shot diagnostics retain discovery and exit/release their inspection
handles. Two Red / 31 focused Green checks cover no cmdline read and rejection
of ambiguous role metrics. FD budgets remain unchanged. The runtime image and
trading code are unchanged; a short full-wrapper probe precedes a fresh complete
load/hour gate. The interrupted run retains `passed: false`.

Corrected real-wrapper 180s probe
`build/mm-load/20261030-013146-6a12e3/` passes p95 56.97ms / p99 295.37ms,
zero errors, original rates and all budgets. Its minute samples retain fixed
descriptors supervisor/engine/API = 5/31/21. This is a short diagnostic, not an
hour acceptance substitute. A fresh complete all-profile gate is required.

## Long-load finding: fill during position checkpoint

Run `build/mm-load/20261030-013705-7a6a10/` retains passed warmup300s
(p95/p99 81.81/336.36ms), mixed900s (132.34/656.92ms) and burst90s
(56.91/155.26ms), all with zero unexpected API errors. These are three windows,
not completed hour/adoption acceptance. During the soak's first 600s random-step
rollover, an active perpetual entry fills from 0.011 to 0.012 LIFE while the old
shutdown path has already captured the held-position snapshot. The existing
post-cancel readback guard correctly detects this and retains HALT on replacement.

The healthy soak is interrupted instead of clearing HALT or counting stopped
trading as healthy operation. Before HALT, cgroup memory is about 675MiB and
role descriptors are stable at 5/32/21; the later memory drop follows stopped
trading and is not evidence of successful garbage collection. The original
result remains `passed: false`. Raw order/position/engine state and logs are
retained under `build/service-supervision/unexpected-soak-*`.

The fix reuses the existing PositionExecutor order references, cancel/readback,
trade-ID deduplication, position model/recorder and recovery guard. Controllers,
action listener and supported executor loops stop before cancellation. Terminal
readback must complete before projecting/persisting the position hold; exact
base, quote and fill/fee data are checked before making any hold visible to
report ticks. Unavailable/mismatched proof never commits a partial checkpoint.
Older/unsupported holds and any fill after the verified checkpoint keep the
original HALT restriction. No new ledger/schema or HALT reset is introduced.

Behavioral replay of the old persistence-before-read order fails all three
positive/retry assertions. The terminal proof validation tests additionally
reject missing proof, nonterminal state, base/quote mismatch and missing fills.
Real PostgreSQL tests cover late fills from zero/partial exposure, idempotent
readback, persisted amount/fees and recovery roundtrip, plus continued HALT for
uncovered and post-checkpoint fills. Shipping-image replay and the complete
unchanged gate remain required.

The deterministic cancellation-fill case runs on the original `f5e7…` shipping
image at `build/e2e-offline/20261030-024209-940a65/` and fails as expected:
`Position checkpoint changed during shutdown`, then restored-position mismatch.
Both spot and perpetual receive a real simulator fill immediately before cancel
ACK, using the ordinary fill/balance/WS implementation and duplicate trade IDs.
This retained failed case is the image-level Red, not an accepted E2E run.

The native checkpoint fix passes the complete Make selection of **2,026 tests
plus eleven subtests**, plus a **91-test focused run** including the additional
short-side case. **Eight real-PostgreSQL checkpoint tests pass**, including an
order retired from the connector active map, exact position/fees reload and
continued HALT on uncovered/post-barrier changes. Full PostgreSQL regression is
still running. These results authorize a replacement build; they do not certify
its image/E2E/load acceptance.
