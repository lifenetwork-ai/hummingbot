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
