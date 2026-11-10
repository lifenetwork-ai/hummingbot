# Release verification

[All reports](../README.md) · [Remaining work](../../docs/plans/README.md)

| Report | Scope |
| --- | --- |
| [Latest Demo/release](demo-release-closeout.md), [evidence](demo-release-closeout-evidence.json) | Source `f53885fda`, image `4198f2fa…`: native3,085+16, PostgreSQL605, all30 E2E pass; full unchanged load/Random soak3600/six faults/retention pass. Revision75 image/rollout verification remains open. Actual30-minute quote target/perpetual handoff pass; regular catalogue restored on revision74; current API revision75 remains running, image metadata unverified. |
| [Earlier full image/load](mm-runtime-followup-load.md), [evidence](mm-runtime-followup-load-evidence.json) | `b090430c…`, build `d0791e563`: native2,861+16, all27 available E2E, full unchanged load/Random soak3600/five faults pass. Burst/soak p99 297.81/313.00 ms; 4,885 simulated fills, 30.33 MiB retained growth, three recovered healthy engine probes. |
| [Failed predecessor](../README.md#historical-records) | `96368970…`, Git `d0791e563` at current release paths: burst p99 failed; corrected `all` failed shutdown after fourteen groups, then thirteen individual targets passed. Retained comparison image, not an accepted production rollback. |
| [Earlier accepted record](../README.md#historical-records) | `a6fe88c0…`, Git `8544488ef` at the current release paths, original quantile-fixture/host scope. Image is absent from the current daemon. |

The signal-lock mechanism is proven fixed; the predecessor's observed nonzero

Current follow-up: API now reports revision75, same running session/control53,
Demo and regular catalogue, no HALT. Its image/environment and the01:41 UTC
rollout transition await GCP reauthentication; this is not continued evidence of
revision74. See [closeout investigation](demo-release-closeout.md#later-revision-transition-under-investigation).

exit still lacks exclusive historical attribution. Baseline probe intervals are
not independent quote-downtime measurements. Real external acceptance remains
separate in the register and [Demo verification](../demo/README.md).
