# Recovered-probe latency investigation — 2026-10-31

Status: bounded protocol complete; both sequential comparisons pass. The
historical 6.7-second engine lateness / five-probe timeout episode remains
**unexplained / not reproduced**. No new runtime defect or runtime fix is
claimed, and full release acceptance remains open. Evidence capture and its
tests are complete; no thresholds or recovery safeguards are relaxed.

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
unexpected errors, p99 129.48ms. Engine PID31 is preserved. The helper requests a 1.5s stop;
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

## Ordinary shipping-entrypoint diagnostic

Run `production-control-20261031-201926-a86d15` completes on unchanged image
`5973410d`, baseline effective YAML and matched client/exchange/fixture hashes.
The service command is the ordinary `python -m hummingbot.cli.service`; there
are no internal measurement hooks. Bot2CPU/2GiB, pool5, TTL15, 100,000 historical
orders/fills and the original client rates/budgets are retained.

| Window | p95 | p99 | Actual new fills | Result |
| --- | --- | --- | --- | --- |
| Warm300 | 52.43ms | 274.35ms | 300 | pass |
| Mixed900 | 54.61ms | 172.84ms | 900 | pass |
| Burst90 | 55.74ms | 264.10ms | 90 | pass |

All four market/side families execute in every window. All 1,290 cumulative
fills/original fees, positions and the two injected funding bills match.
Measured feed burst is 509.78/s. Engine/API PIDs32/33 stay unchanged; no child
replacement or HALT is observed. Shipping logs contain 15 recovered engine-probe
episodes, longest 1.109s. Thus short probe failures also occur without the
measurement wrapper. They do not reproduce the earlier five-probe/7.206s episode.
These durations span the first failed observation to the next successful probe
at the supervisor's polling cadence; they are not measurements of continuous
engine freezes. Likewise sampled scheduled heartbeat lateness is not exclusive
attribution to one operation.

This is explicitly a diagnostic from a failed baseline:
`baseline_passed=false`, `release_scope_complete=false`. Passing it cannot close
the full-release or passing-baseline matched-control checklist. A single
sequential comparison also cannot prove a causal wrapper effect. The matched
incident-recorded sequence follows; the current latency cause remains open.

## Matched incident-recorded diagnostic

Run `20261031-204226-b6d9ed` uses diagnostic source `bf2553679`, the same image,
effective YAML, client, exchange, health helper and fixture hashes as the shipping
comparison. Its existing metrics wrapper and new incident recording are the
intentional differences. The database/history, CPU/memory limits, pool5, TTL15,
durations, workload and budgets match. Both are sequential observations on an
ARM Mac / amd64 emulation, with the operator's previously accepted battery/host
environment; this is not a statistically controlled proof of causality.

| Window | p95 | p99 | Actual new fills | Result |
| --- | --- | --- | --- | --- |
| Warm300 | 50.43ms | 91.91ms | 300 | pass |
| Mixed900 | 55.82ms | 145.26ms | 900 | pass |
| Burst90 | 61.08ms | 165.81ms | 90 | pass |

Zero unexpected HTTP/semantic errors; all four families execute in every window. All 1,290
cumulative fills/original fees, positions and funding bills match. The feed
burst delivers 509.80/s. Per-role samples and shipping logs retain engine/API
PIDs32/33 throughout, with exactly two initial child starts and no replacement
or HALT. Peak application cgroup memory is 0.687 GiB. This approximately
22-minute diagnostic is not a new hour-long retention/leak acceptance.

Fourteen engine-probe episodes, including setup, recover. Five occur before the
measured windows; the nine measured episodes are one warm, six mixed and two
burst. The longest measured observation interval is 1.096s, versus 2.393s during
setup. Largest captured scheduled loop stalls are 231/488/562ms for the three
engine windows; 1,113ms occurs during setup. No 6.7s / five-probe episode occurs.
The API's largest captured stall is 215ms. Sampler gaps reach 525/424ms overall;
missing execution inside those gaps cannot be attributed from sampled frames.

Engine/API incident files are 3,437,055 / 3,503,345 bytes. Last atomic snapshots
report queue depth0, zero queue/disk-cap record drops, no oversized records and
no suppressed incidents. File sizes match those snapshots' written-byte totals.
Ring eviction is expected bounded history, not a lost-trade count. Active-span
snapshots never reach their 16-span cap in the retained samples.

Completed root traces intentionally retain at most 16 children; cumulative
truncation counters are 193,955 / 246, including fast roots that are never
persisted. These are **not** lost fills or HTTP errors, but they do prevent a
complete reconstruction from those child chains. The existing harness force
cleans containers: the periodic summaries are last snapshots, not graceful-close
proof for every final writer operation. Do not equate zero sink drops with
complete causal trace coverage.

## What the new evidence does and does not explain

One new short timeout ends at UTC13:50:02.924. An `observe_once` root beginning
13:50:02.618 lasts 337.83ms. A stack sample during it shows the main thread in a
PostgreSQL connection ping; 12 child spans are omitted by the root cap. This
places real observer/DB activity near the 300ms health deadline but cannot assign
all time to one SQL call or explain the historical multi-second event.

The largest measured burst stall, 562ms scheduled lateness at offset27.413s,
samples the main thread inside `BotOwnership.check_owner` / the bounded driver
wait during reconciliation. Its active SQL fingerprint is
`b9712ac913a805df3dba`, backend107: the ownership-fenced bot-state/`pg_locks` read.
This is a sampled location, not proof that this SELECT consumed the entire
562ms, nor whether its wait was locks, I/O or scheduling. The incident mode has
no external PostgreSQL wait sampler. Ownership checks are retained.

The retained host power excerpt around the old event shows video/audio assertions
but no sleep transition in that excerpt. It contains no per-process CPU or VM
scheduling attribution. It cannot establish that Chrome, sleep, OKX, PostgreSQL
or the instrumentation caused the old episode. Both new experiments use the
offline simulator, so they also say nothing about a specific real OKX outage.

## Closure and remaining scope

- Complete: automatic bounded incident capture, Red/Green source-lookup
  correction, 61 related native tests, known-pause packaged validation and one
  sequential shipping/recorded comparison with unchanged budgets.
- Still open: historical long-lag cause and the current candidate's full
  combined hour/fault release acceptance plus passing-baseline shipping control.
- No speculative engine/query/pool/watchdog change. Both comparisons pass but
  neither reproduces the earlier failure; there is no justified new runtime
  fix to validate with another blind hour repeat. A future occurrence needs the
  retained event/stack/CPU/SQL evidence before assigning a cause.

All temporary containers and the task's idle-sleep assertion are removed.
Persistent Demo image, stopped control/ack and order/fill/executor/position
fingerprints are unchanged. No connected Demo/live action or cloud deployment
is performed. The earlier verified runtime and producer bugs remain closed.

Portable [evidence](mm-incident-investigation-evidence.json); raw results and
analysis helpers remain under the ignored build directories named above.
