# Release verification

[All reports](../README.md) · [Remaining work](../../docs/plans/README.md)

| Report | Scope |
| --- | --- |
| [Current image/load](mm-runtime-followup-load.md), [evidence](mm-runtime-followup-load-evidence.json) | `b090430c…`, build `d0791e563`: native2,861+16, all27 available E2E, full unchanged load/Random soak3600/five faults pass. Burst/soak p99 297.81/313.00 ms; 4,885 simulated fills, 30.33 MiB retained growth, three recovered healthy engine probes. |
| [Failed predecessor](../README.md#historical-records) | `96368970…`, Git `d0791e563` at current release paths: burst p99 failed; corrected `all` failed shutdown after fourteen groups, then thirteen individual targets passed. Retained comparison image, not an accepted production rollback. |
| [Earlier accepted record](../README.md#historical-records) | `a6fe88c0…`, Git `8544488ef` at the current release paths, original quantile-fixture/host scope. Image is absent from the current daemon. |

The signal-lock mechanism is proven fixed; the predecessor's observed nonzero
exit still lacks exclusive historical attribution. Baseline probe intervals are
not independent quote-downtime measurements. Real external acceptance remains
separate in the register and [Demo verification](../demo/README.md).
