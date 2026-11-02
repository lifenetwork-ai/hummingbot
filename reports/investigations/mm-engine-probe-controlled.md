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

Pending. Reuse one existing harness and one fixed profile/fixture across three
600s modes: ordinary shipping, scalar wrapper only, wrapper plus GC/incidents.
These are short diagnostics and cannot replace shipping R2 or full release
acceptance. Shipping code and deadlines remain unchanged.

## C — Interpretation / runtime fixes

Pending controlled results. No new runtime defect or fix is claimed.

## D — Demo read-only preflight

Pending. The old Demo service/DB container was removed by Docker cleanup; the
private files and original persistent volume remain. No trading/session/account
mutation or migration is part of this preflight.
