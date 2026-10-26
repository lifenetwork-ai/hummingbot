# Short MM latency reproduction and UTC serialization fix

Subsequent result: the [full-image sixty-minute gate passed](life-mm-soak-retest.md).
The user closed the remaining reproduction follow-up on 2026-10-26 after accepting the passing full-image gate. The scope below records the earlier short iteration.

The original ten-timeout cluster **was not reproduced** by four short probes.
A measured serialization cost was reduced and a nested UTC contract bug was
fixed with Red/Green tests. At this short-iteration checkpoint, the prior
sixty-minute failure remained open; the subsequent passing gate and user closure
are linked above.

[Portable evidence](life-mm-short-repro-evidence.json) includes source/image
hashes, matched fixture hashes, stage timings, both CPU replays and all probes.
The historical [extended trace report](life-mm-trace-load-review.md) is unchanged.

## Confirmed change

`APIModel` previously attached a Python wrapper serializer to every field.
Profiling 100 serializations of a 500-order page recorded 750,200 UTC-wrapper
calls, including strings, numbers and lists. All response datetime annotations
now reuse Pydantic's `Annotated[datetime, PlainSerializer(...)]`; ordinary fields
use the existing compiled serializers. Python-mode values retain their offsets,
Decimal JSON strings retain their precision, and the generated OpenAPI contract
is unchanged.

Red/Green tests also reproduced an existing contract gap: `pair_observations`
values inside a datetime mapping retained `+07:00`, while the direct observation
field returned UTC `Z`. The datetime type now normalizes both locations in JSON.
This is a confirmed correctness fix, separate from the unconfirmed timeout cause.

## CPU comparison

The benchmark validates identical JSON outputs, warms both DTO implementations,
then alternates baseline/current order over five repeats of 100 serializations.
It uses thread CPU time and excludes DB, validation and HTTP scheduling.

| 500-row response | Before, native CPU ms | After, native CPU ms | Before, amd64 container CPU ms | After, amd64 container CPU ms | Container reduction |
|---|---:|---:|---:|---:|---:|
| Orders | 5.49 | 1.95 | 8.58 | 2.95 | 65.66% |
| Fills with nested fees | 8.63 | 2.46 | 13.80 | 4.36 | 68.44% |

The container comparison ran both implementations in one process, with one CPU,
512 MiB, a read-only filesystem and no network. The baseline source hash matches
the parent deployment image's `admin_models.py`; the patched source hash matches
the diagnostic image. Native timings are ARM; container timings include amd64
emulation. The unprofiled paired comparison is separate from the call-count
profile, which carries profiler overhead.

## Short deployment-image probes

All four runs used identical fixture hashes, real PostgreSQL, the existing
simulator/adapters/recorder and the same resource limits: bot 1 CPU/2 GiB,
PostgreSQL 2 CPUs/512 MiB, client/simulator 1 CPU/512 MiB each. Each seeded
100,000 orders/fills and offered 20 reads/s, 100 book updates/s and one fill/s.
The original p95/p99/error budgets were unchanged.

| Image | Trace | Duration | p95 ms | p99 ms | Errors | Outcome |
|---|---|---:|---:|---:|---:|---|
| Parent | On | 60 s | 123.64 | 578.90 | 0 | Pass |
| Parent | Off | 60 s | 79.48 | 560.50 | 0 | Pass |
| Patched diagnostic | On | 60 s | 52.06 | 179.47 | 0 | Pass |
| Patched diagnostic | Off | 60 s | 45.27 | 139.23 | 0 | Pass |

Each run completed 1,200 reads and independently reconciled all 60 fills, fees,
perpetual positions and both funding bills. These are single short trials,
not statistical evidence of a permanent latency bound or a full trace-overhead
isolation. Neither parent probe reproduced the long-run failure, so the improved
short-run numbers do not establish that this patch fixes the ten timeouts.

## What the additional diagnostics show

The test wrapper now separates total checkout, the pool queue operation,
connection creation and pre-ping. It also measures wall/thread CPU for executor
report construction and response validation/serialization in both trace modes.
Regression tests preserve normal/classmethod binding, return values and the
original exception without logging its message.

In the traced parent probe, queue-operation maximum was 33.58 ms versus
112.37 ms total checkout, 111.67 ms connection creation and 50.37 ms pre-ping
(these are maxima from different calls and must not be added). Queue-operation
time includes locking/scheduling and is not a pure capacity-wait counter.
Pool exhaustion was not reproduced. The runtime and API still share the existing
pool of three connections plus two overflow; no pool or admission setting changed.

Traced response serialization thread CPU p99 decreased from 30.54 to 4.90 ms;
maximum decreased from 164.78 to 10.00 ms. Executor-update CPU p99 remained
approximately 10.5 ms in these fresh-process probes. No recorder, fencing,
order/executor decision or SQL/query cache changed.

The stored executor replay selected all 80 terminal snapshots in generation 4
of the failed soak, hydrated 40 held IDs in three position buckets, and ran the
existing orchestrator's report math 100 times: native median 3.09 ms CPU,
maximum 5.05 ms. This avoids waiting for elapsed time, but it does **not** restore
the exact process: live executor PnL properties, in-flight state, DB, scheduling
and concurrent requests are excluded. It cannot rule those factors out. The
full probe history was pre-seeded; the entire 49-minute runtime was not recreated.

## Verification and remaining work

- 457 CLI tests pass, including the UTC/Decimal and timing-wrapper regressions.
- 8 selected real-PostgreSQL history/HTTP/UTC tests pass; 119 unrelated integration
  cases were deselected. No full native or full integration acceptance is claimed.
- OpenAPI check passes with no schema change. Scoped pre-commit checks pass.
- Runtime change committed as `ca1a0d77a`; raw Red/Green and probe logs live under
  ignored `build/load-repro/` and the evidence-indexed `build/mm-load/` directories.

The patched image is a diagnostic derivative of
`sha256:868cc6642d2aa1c272c69a5dc8707509a78eee676c1f18ef0218efcb6c621a35`,
with only the reviewed Python DTO file overlaid and hash-verified. Its ID is
`sha256:689a2cbcebd8e7ea9b63dccd51579487daccbb5d6dbdc1221c248adaafaaf5a7`.
This iteration did not rebuild/pin a final Cloud Run deployment manifest or run
another one-hour soak. Both follow-ups were open at this checkpoint; the
subsequent full-image retest passed and the user closed further reproduction.

The next diagnostic target is the original pool/event-loop stall under concurrent
reads, using the separated stage measurements. A further fix should follow a
reproducible failure; the historical cluster should not be attributed solely to
UTC serialization or profile handover from these measurements.

## Reproduce the quick checks

```sh
mkdir -p build/load-repro
git show b82bf0ace:hummingbot/cli/admin_models.py > build/load-repro/admin_models_before.py
python scripts/benchmark_admin_serialization.py \
  --baseline build/load-repro/admin_models_before.py \
  --output build/load-repro/serialization-paired.json
python scripts/replay_mm_executor_reports.py \
  build/mm-load/20261026-092449-630a24/final-executors.json \
  --generation 4 --output build/load-repro/executor-replay.json
python scripts/test_mm_load.py --image hummingbot-cloudrun:serialization-diagnostic \
  --profile probe --probe-seconds 60 --trace
```

The executor snapshot is a local ignored artifact; its hash is in the portable
evidence. It must be available to replay that exact batch. Omit `--trace` for the
paired untraced probe. Short probes are diagnostic, as documented in the
[load guide](../docs/life-mm-load-tests.md#opt-in-causal-diagnostics).
