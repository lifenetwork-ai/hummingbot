# Remaining acceptance investigation — 2026-10-31

Status: short diagnostics in progress. The three previously verified bugs stay
closed. This follow-up does not claim an additional trading runtime defect.

## Starting evidence

Combined run `20261031-082445-9a99bd` failed burst emission at 498.033/s against
the existing 500/s minimum. Its warm/mixed/60-minute soak and six recovery faults
passed. Separate burst retry `20261031-095522-67306b` passed at 509.80/s. Image,
source and fixture hashes matched, but workload state differed: the retained
exchange ledgers show 319 orders created before the combined burst's first
control, versus 17 before the fresh retry's control. The final states of those
orders cannot all be assigned retroactively to that timestamp.

The latest image `81fa1448` is absent from the current Docker daemon. Rebuilding
the unchanged application source is required before packaged diagnostics; no
persistent Demo image or database is replaced.

## Tested diagnostic correction

The burst assertion previously raised before its result was added to the output.
Failed windows therefore omitted exact frame count/duration/control metadata.
Five new tests fail before the diagnostic change; 36 related tests pass after it.
The existing feed threshold, counting tolerance and restore-to-steady behavior
are unchanged.

Opt-in simulator diagnostics retain at most 64 observed seconds: actual delivered
and failed frames, scheduling lateness, book construction and socket-send maxima,
active subscription counts, process CPU and cgroup CPU/throttling counters.
Counters increment only after actual successful sends. Native tests also cover
closed transports and verify snapshots cannot mutate retained metrics. Ordinary
load does not enable the collector. This is test instrumentation, not a deployed
admin endpoint or a new production monitoring service.

Raw Red/Green logs: `build/burst-investigation-20261031/`.

## Pending

- Matched short producer diagnostics, with the workload-state difference explicit.
- Bounded review of the old supervisor timeline and missing evidence.
- Current candidate combined release gate and matched ordinary-entrypoint control.

No historical failed result is changed or merged with a separate passing retry.
