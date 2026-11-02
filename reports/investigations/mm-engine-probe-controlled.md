# Controlled engine-probe investigation

Date: 2026-11-02 (Asia/Saigon). Baseline image `39198499`, shipping source
`9db4ff56a`; [plan](../../docs/plans/MM_ENGINE_PROBE_LATENCY_TDD_PLAN.md).
The preceding full R1 and matched shipping R2 remain accepted local results.
This follow-up investigates transient probe latency without relabelling those
results or claiming unchanged PIDs prove continuous quotes.

## A — Complete existing-evidence review

The existing analyzer now selects every episode rather than only ten longest
soak examples. Original result/log/incident files are unchanged. It finds54
recovered engine-probe transitions in the healthy envelope:1 warmup,9 mixed,
4 burst,39 soak and1 between windows. Deliberate post-soak faults are excluded.
Every transition has a retained recovery. The longest supervisor-reported
interval is2.623s; these intervals are not measurements of quote downtime.

The supervisor's HTTP probe timeout is300ms, with a separate15s sustained-failure
grace. Existing top-five retained context includes observe, recorder/fill,
funding, ownership and SQL work. Largest retained GC is319.93ms, and largest
loop-stall sample is1,157.94ms. A GC pause near the probe threshold is a plausible
contributor to some transitions, not a causal explanation of all54 episodes.
Sampled stacks and temporal overlaps do not identify one faulty SQL call.

R1 uses test wrappers, scalar metrics, GC timing and bounded incident capture;
R2 uses the ordinary entrypoint for a shorter interval. R2 has no recorded
probe transition, but these differing conditions do not isolate tracing cost.
API ordinary trace/file records reach their bounded budget; missing/selected
spans prevent complete attribution. All captured analyses are diagnostic-only.

[Portable evidence](mm-engine-probe-controlled-evidence.json) contains per-window
counts, all selected episode summaries, overlapping-operation counts, GC/stall
context and original incident hashes. Raw analyses:
`build/engine-probe-controlled-20261102/`. The healthy-envelope analysis uses an
ignored metadata copy with symlinked original incidents; it never rewrites the
original release artifact.

## B — Matched diagnostic controls

The existing shipping-control script now accepts an explicit diagnostic-only
instrumentation mode. Default R2 remains the ordinary command; selecting scalar
metrics or metrics+GC/incidents requires a bounded `--probe-seconds` run. The
existing acceptance predicate rejects every such short run. Actual container
commands are checked and incident/GC settings are explicit. No shipping module,
image, deadline, quote/risk rule or pool/cache setting changes.

Six initial Red cases fail for missing independent instrumentation choices and
rejection of instrumented full-R2 scope (13 existing cases pass). The final69
control/budget/analyzer/incident regressions pass. One invocation used an
incorrect analyzer-test filename; its launcher log is retained separately and
is not a runtime failure. Matched600s measurements run sequentially on the
accepted image, with byte-identical asset fixtures, fixed `follow_market` in
both markets,100k historical fills,2 CPU/2GiB application,2 CPU/512MiB DB,
TTL15s and unchanged traffic/budgets. All measurement source hashes remain
unchanged across the three runs. These are one fixed-order repeat per condition
on the same local emulated host, not a replicated causal experiment.

| Condition | API p95 | API p99 | API errors | Recovered probes | Max reported interval | Approx. CPU cores |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Ordinary shipping | 51.47ms | 246.80ms | 0 | 1 | 1.033s | 0.325 |
| Scalar wrapper | 51.98ms | 360.08ms | 0 | 1 | 2.827s | 0.348 |
| Wrapper + GC/incidents | 57.55ms | 383.26ms | 0 | 4 | 1.054s | 0.422 |

All three pass the original healthy rate/latency/error/command/memory budgets;
600 independent fills/original fees and positions reconcile in each fresh DB.
Supervisor/engine/API PIDs stay unchanged. CPU is container CPU-counter delta
across snapshot boundaries divided by600s, including boundary inspection cost;
it is a local estimate, not a whole-service performance guarantee. Container
peak memory includes startup:892.50/693.63/701.34MiB, all within the existing
1.5GiB gate. These short peaks do not measure long-running memory retention.

The fresh rich run retains all four recovered incidents. GC records in retained
contexts precede, rather than intersect, failure intervals. Observe/recorder,
ownership/SQL and loop stalls appear at different sampling times. Neither this
nor absence of a retained GC intersection proves a universal cause/absence. The
analyzer's marker intervals differ slightly from supervisor-reported intervals;
publication and sampling times are distinct. They are not quote downtime.

These diagnostics do not replace the earlier full R1/shipping R2 acceptance.

## C — Interpretation / runtime fixes

A short probe timeout is reproduced in the ordinary shipping path, so the54
historical episodes cannot all be assigned to tracing. The rich condition has
more recovered probes and higher measured CPU in this comparison, consistent
with additional observer cost; fixed order, single repeats and different hot
paths do not establish one unique explanation or a universal multiplier.

No sustained unresponsiveness, child restart, HALT, lost/duplicate fill, position
mismatch or new shipping logic defect is established. Scalar maxima include
slow recorder/commit/checkpoint work, but lack timestamps that identify one
probe cause. [PostgreSQL operations](../../docs/postgres-runtime.md) already
states that synchronous durable DB calls can delay the engine loop within the
existing3s response bound. A300ms HTTP probe can miss during such work; its
recovery interval also includes the supervisor poll cadence. It must not be
interpreted as measured quote unavailability.

No speculative runtime patch, asynchronous acknowledgment before durable commit,
cache, larger pool, longer probe timeout/grace or relaxed authority check is
introduced. Red/Green applies to the diagnostic harness extension, not a claimed
engine bug fix. Shipping application bytes/image remain unchanged, so there is
no new application artifact requiring another hour gate. The prior accepted
full release scope remains intact; unique attribution remains open.

## D — Demo read-only preflight

The current accepted image `39198499` passes authenticated Demo REST reads for
both key sets (same account), Futures/Hedge mode, balances, recent fills and
instruments. Public books5 updates arrive for BTC/ETH/AVAX spot and BTC perpetual;
both private order subscriptions authenticate. There are no pending exchange
orders. All calls/subscriptions are read-only; no order/session/account action
or migration is sent. The helper verifies no offline transport override.

Because the original Demo container is absent, preflight mounts its original
volume **read-only**, fingerprints every file, copies it to a task-owned volume,
and verifies the copy before boot. PostgreSQL runs only on that isolated copy,
with default transaction read-only and autovacuum off. Startup/recovery may
write the copy; no original DB/application/ownership write is allowed. Original
file hashes before/after are identical, as are private configuration hashes.
Task-owned copies/containers are removed afterward.

Snapshot:850 orders,6 fills,3 held-position rows,21 identified funding bills,
zero pending commands. Desired state is stopped, versions acknowledged, no
HALT or pending mutations. Four durable ledger fingerprints match the last
stopped acceptance. Exchange contracts convert exactly to DB perpetual base
quantities: long0.0084 BTC and short0.0027 BTC. Exchange quantity/avgPx also match
the previous connected preflight. Original strategy breakeven/cost basis is
preserved; equality with the exchange avgPx is not asserted.

The first SDK launcher omitted the application import path and exited before
calling OKX; logs are retained. Adding the correct image source path fixes that
helper launch, not application runtime. The repeated read-only check passes.
No new active MM session, service restoration, natural fill or running-service
recovery/availability acceptance is claimed. This is current-image SDK
connectivity plus a read-only recovery snapshot, not permission to trade.

## Completed scope and limits

[Portable evidence](mm-engine-probe-controlled-evidence.json) contains the matched
raw-result hashes, source/fixture identity, conditions, independent budgets,
incident hashes and sanitized Demo read-only results. Raw source analyses,
controlled logs and private-output files remain under ignored
`build/engine-probe-controlled-20261102/`; original raw run directories are
recorded there. Credentials/private YAML stay outside the repository.

Phase A review is committed `366ea28ff`; diagnostic harness `9cdb1bc89` has69
passing selected tests after six Reds. All three600s controls and current-image
Demo preflight pass. Shipping source is still `9db4ff56a`/image `39198499`.
Completing this investigation does not close the unique historical cause or
assert uninterrupted quotes, unlimited-duration leak freedom or Cloud Run
production acceptance.
