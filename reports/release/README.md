# Release verification

[All reports](../README.md) · [Remaining work](../../docs/plans/README.md)

| Report | Scope |
| --- | --- |
| [Latest Demo/release](demo-release-closeout.md), [evidence](demo-release-closeout-evidence.json) | Source `f53885fda`, image `4198f2fa…`: native3,085+16, PostgreSQL605, all30 E2E pass; full unchanged load/Random soak3600/six faults/retention pass. Historical rollout is attributed to CI; new CI artifact verification remains open. Actual30-minute quote target/perpetual handoff pass; regular catalogue restored on revision74; revision77 resumes the same session/control53 without HALT; new artifact gates remain separate. |
| [Earlier full image/load](mm-runtime-followup-load.md), [evidence](mm-runtime-followup-load-evidence.json) | `b090430c…`, build `d0791e563`: native2,861+16, all27 available E2E, full unchanged load/Random soak3600/five faults pass. Burst/soak p99 297.81/313.00 ms; 4,885 simulated fills, 30.33 MiB retained growth, three recovered healthy engine probes. |
| [Failed predecessor](../README.md#historical-records) | `96368970…`, Git `d0791e563` at current release paths: burst p99 failed; corrected `all` failed shutdown after fourteen groups, then thirteen individual targets passed. Retained comparison image, not an accepted production rollback. |
| [Earlier accepted record](../README.md#historical-records) | `a6fe88c0…`, Git `8544488ef` at the current release paths, original quantile-fixture/host scope. Image is absent from the current daemon. |

Follow-up: Cloud Build/audit attributes revision74→75 to the delayed automatic
deployment of older commit `f7da0d1e6`; no new runtime defect is reproduced.
Overlapping-owner E2E and91 native controls pass. Merged CI artifacts require
separate verification; they do not inherit image4198 release gates. See [closeout investigation](demo-release-closeout.md#later-revision-transition-under-investigation).

The signal-lock mechanism is proven fixed; the predecessor's observed nonzero
exit still lacks exclusive historical attribution. Baseline probe intervals are
not independent quote-downtime measurements. Real external acceptance remains
separate in the register and [Demo verification](../demo/README.md).
