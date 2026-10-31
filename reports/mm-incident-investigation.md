# Recovered-probe latency investigation — 2026-10-31

Status: in progress. The historical 6.7-second engine lateness / five-probe
timeout episode remains unexplained. This work adds evidence capture and a
bounded shipping-entrypoint comparison; it does not declare a speculative
runtime fix or relax the release budgets.

## Protocol

1. Red/Green test a bounded recorder that retains context before an incident,
   records failed-probe/recovery transitions and a recovery tail, and explicitly
   counts evicted, oversized, suppressed and unwritten records.
2. Validate capture against a known short offline engine SIGSTOP/SIGCONT. Record
   the actual signal timestamps separately. This injected event cannot explain
   the earlier naturally occurring episode.
3. Run one ordinary shipping-entrypoint warm300/mixed900/burst90 diagnostic,
   using the failed baseline's effective YAML, unchanged client/exchange fixtures,
   image, history size and resource limits. Run one matched sequence with incident
   recording. Preserve every failed window; do not rerun until green.
4. If the evidence identifies a runtime defect, write a reproducing Red test,
   fix it and verify Green. Otherwise retain the cause as unresolved. A full
   unchanged hour/recovery gate follows a justified candidate fix, not merely a
   separate passing diagnostic.

## Capture limits and interpretation

The test wrapper keeps at most 256 serialized records / 512 KiB per role, from
the last ten seconds. Supervisor probe transitions or a loop stall of at least
200ms retain the preceding context and ten subsequent seconds. At most 32
separate incidents per process and 32 MiB per role stream are written through a
256-record nonblocking queue. Replacement processes append to the same capped
stream; PID and role accompany records. Loss/suppression counters are explicit.

Samples include Python stack locations (no locals), up to 16 active operations
with sanitized SQL shape/backend PID, process CPU, sampler-thread CPU, shared
cgroup CPU/throttling, pressure and sampler gaps. The sampler polls every 50ms
and normally samples every 250ms. Thread scheduling/GIL/process suspension can
prevent sampling: a missing sample is not proof of an idle engine. Sampled
locations and inclusive async-span CPU do not establish exclusive attribution.
There is no additional PostgreSQL sampling connection in this mode.

This is opt-in test instrumentation. Shipping service/connector code, probe
timeouts, ownership/recovery, trading decisions and load budgets are unchanged.
The ordinary-entrypoint comparison has no internal wrapper, SQL hooks, stack
sampler or continuous internal metrics. Its resource snapshots run outside each
measured client window; its ledger and shipping supervisor logs provide progress
and recovery evidence. It cannot prove event-loop latency in unobserved gaps.

The new `sequence` load profile explicitly stops after warm300/mixed900/burst90
and is always diagnostic. A failed baseline requires the explicit production
control option `--diagnostic-baseline`; neither that option nor `--keep-going`
turns a failed window into acceptance. Full tracing and incident diagnostics are
mutually exclusive to keep their measured overhead distinct.

## Native verification

New incident tests initially fail collection before their implementation; the
failed-baseline control tests likewise fail before their guard is added.
Expanded related suite: **59 passed**. It covers prehistory/recovery retention,
byte/count/age/capture limits, snapshot independence, probe transition privacy,
bounded active-operation sampling, failed-baseline exclusion, the original
producer deadline/in-flight controls and unchanged latency/error budgets.

Raw logs and diagnostic helpers: ignored
`build/incident-investigation-20261031/`. Packaged results follow below.

## Known-pause recorder validation

Run `20261031-201446-9ea5e1` uses unchanged image `5973410d` and the test wrapper.
The 90-second window passes: 90 actual fills, matching ledger/positions, no HTTP
errors, p99 129.48ms. Engine PID31 is preserved. The helper requests a 1.5s stop;
recorded host timestamps include signal-process overhead and span 2.009s.

Both roles retain the corresponding engine probe failure and recovery, 35
pre-fault CPU/stack samples and 8/7 samples near the end of the recovery tail.
Retained files are 1.49/0.90 MiB; the latest runtime-export sink counters report
zero dropped records. The initial prototype is force-cleaned by the existing
harness, so shutdown summaries do not run. The final recorder adds periodic
atomic summary snapshots, capped append on child replacement and record PIDs;
these refinements are exercised in the next sequence. The initial source/fixture
hashes and raw evidence retain their exact prototype scope.

Several additional short startup/setup timeouts precede injection. Their stacks
show cold indicator imports and the active historical outflow rebuild/DB work.
These sampled locations describe these new setup events; none is a reproduction
or causal explanation of the historical late 6.7s episode. The automatic capture
is validated; the long-lag cause remains open.

## Remove unnecessary work in the sampler

A focused Red test confirms that the existing `traceback.extract_stack` resolves
source lines through `linecache.getline`. The recorder outputs only file/line/
function, so that source lookup and caching are unnecessary observer work.
Direct bounded frame traversal preserves the same output order/limit and omits
source resolution. The new regression and prior tests pass: **61 cases**.
This is a confirmed test-instrumentation improvement, with no measured claim
about the historical latency cause or an application-runtime speedup.
