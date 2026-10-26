# MM latency tracing and extended load review

Status: instrumentation and the traced smoke are verified; extended runs
are pending. This report must not be treated as a completed load acceptance.

The earlier sixty-second probe exceeded p99 (1,053.14 ms versus a 1,000 ms
budget); a repeat passed. Aggregate timing distributions did not establish a
cause. This follow-up adds bounded diagnostics and measures the existing
five-minute warmup/fifteen-minute mixed profile plus a separate one-hour random
session soak. Original budgets and workload remain unchanged.

The previous image was absent from the current Docker image store. It was
rebuilt from commit `ed98eaf18eeba375a598ffb96befee7c3f0cc895`; nineteen selected
runtime/SQL SHA-256 values match the previously accepted source. New image ID:
`sha256:868cc6642d2aa1c272c69a5dc8707509a78eee676c1f18ef0218efcb6c621a35`.
It has the same size (1,364,516,628 bytes), PostgreSQL image/version and Docker
host resource limits as the earlier probe. A rebuilt image is a separate
artifact; source equality does not imply binary image identity or establish
the cause of the historical spike.

Changes are limited to the test harness and documentation. No production cache,
query, trading logic, exchange call or deployment setting changed. See the
[diagnostic procedure](../docs/life-mm-load-tests.md#opt-in-causal-diagnostics).

Native verification: fifteen focused tests pass, covering request/worker span
parentage, SQL literal redaction, context lifecycle, queue/file/span bounds,
append across windows and matching waits by PID/time within the measured window.
Scoped pre-commit hooks and documentation link checks pass.

The sixty-second traced smoke (`build/mm-load/20261026-090043-aecc5c`)
completed all 1,200 reads and 60 fills with no HTTP errors, and ledger/position
checks passed. It **failed** p95 (668.67 ms), p99 (1,041.87 ms) and the end-window
backlog budget. Tracing produced 235 in-window PostgreSQL samples without sampler
errors or dropped stream records. Startup exceeded one root span bound; no
dropped spans were observed in retained slow workload roots. This smoke is
diagnostic evidence, not an acceptance pass.

Pending: extended measured durations, read latency/rates/errors, ledger assertions,
random handover/soak growth checks, trace loss and overhead, and evidence-based
attribution. Raw per-run traces/logs remain under ignored `build/mm-load/`.
