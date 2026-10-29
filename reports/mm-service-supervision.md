# Single-service runtime supervision verification

Status: runtime integration is in progress. Native checks do not certify the
shipping image or Cloud Run deployment. The real local Demo remains stopped on
its existing image/database. The earlier [prototype measurements](mm-process-isolation-experiment.md)
retain their original source/image scope; they are not acceptance of this change.

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
