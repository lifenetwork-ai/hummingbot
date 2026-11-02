# Quote-recovery image: full local load and connected Demo

2026-11-02–03 (Asia/Saigon). **Full R1 and matched shipping R2 pass on image
`660bd38b`; connected Demo also verifies new natural maker fills and filled restart.**
[Load evidence](mm-quote-recovery-release-evidence.json),
[Demo report](../demo/okx-demo-quote-recovery.md#bounded-natural-maker-fill-follow-up--2026-11-03),
[plan](../../docs/plans/MM_RUNTIME_LOGIC_AUDIT_TDD_PLAN.md#quote-recovery-image-full-load-and-connected-demo--requested-2026-11-02).

## Artifact and conditions

Image `sha256:660bd38be0014fc83ffcade00e0909b07e4c0402abbb0c566e3041796429f32a`,
manifest `sha256:fefa25467cc6c79c34187bb88dc8ed493461f746aeb9e6822a4fc50f6e4d5007`.
Runtime fix `eee18456c` removes the fixed shutdown idle wait after terminal proof.
No runtime/source/budget changes are made between the retained failure and retry.
All 1,223 frozen measurement/runtime files remain unchanged; all 1,155 reviewed
packaged runtime/config files match the checkout after testing. The derivative
retains its reviewed base dependency layers and changes one runtime Python module.

Both gates use the same image, all 17 shared fixture hashes, two bot CPUs/2GiB,
total DB pool five, cache TTL15, 100,000 historical orders and 100,000 historical
fills. PostgreSQL has two CPUs/512MiB; simulator/client each one CPU/512MiB.
This is local amd64 emulation on arm64. No real credentials enter the offline
workload. The connected Demo app/DB are stopped during the final load pair.

Prior unchanged-source validation remains native2,356 plus11 subtests, PG296,
Swagger and three packaged E2E groups; those checks are referenced, not rerun
or counted again as part of this load task.

## Completed gates

| Gate/window | Duration | Read p99 | Unexpected API errors |
| --- | ---: | ---: | ---: |
| R1 warmup | 300s | 923ms | 0 |
| R1 mixed | 900s | 478ms | 0 |
| R1 burst | 90s | 66ms | 0 |
| R1 Random soak | 3,600s | 103ms | 0 |
| R2 shipping warmup | 300s | 64ms | 0 |
| R2 shipping mixed | 900s | 64ms | 0 |

R1 passes unchanged original rate, latency/error, control and memory budgets.
All six separate injected fault groups pass: feed, volatility defense, owner
restart, queued expiry, database delay and database failure. Expected fault
errors/restarts/HALT behavior are excluded from healthy-window latency.

The soak has58 continuous minute samples, maximum gap65.75s, and all three
Random profiles appear. Its end-of-window cumulative ledger independently
matches **4,885 synthetic fills, original fees and positions**. These are simulator
transactions, distinct from the five actual Demo executions below.

Retained growth is **14.18MiB** against64MiB; raw cgroup growth is24.14MiB. Peak
healthy cgroup memory is733.96MiB against1.5GiB. Engine/API RSS median growth is
9.38/4.84MiB; thread and FD growth are zero for all three roles. Healthy sampled
supervisor/engine/API identities remain stable. This one-hour result does not
prove absence of every slower leak or indefinite uptime.

R2 uses the ordinary `python -m hummingbot.cli.service` entrypoint without the
internal timing wrapper and matches1,200 cumulative fills/fees/positions. Its
peak is679.58MiB. R2 is a warm300/mixed900 control, not another hour soak. Its raw
`diagnostic=true`/`release_scope_complete=false` fields retain their original
meaning; matched-baseline eligibility and independent verification pass.

## Retained failure and residual probes

The first attempt `20261102-215445-f71862` remains **failed**: burst main p99
1,152.60ms and auxiliary p991,125.72ms exceed1s. Warm/mixed pass, there are zero
unexpected API errors, and1,290 simulated fills/positions match. It does not
complete soak/fault/R2 acceptance. Its raw result is never promoted or stitched
into the complete passing retry.

That attempt ran concurrently with connected Demo on the same host. Stopping
Demo for the unchanged retry improves isolation; it does **not** establish a
unique cause. The failure occurs on the internal simulator network, so it is
not a direct wait on OKX REST/WS. Retained traces show planned-stop position SQL,
HTTP tails and recovered probes; overlap alone does not prove one root cause.
A twelve-millisecond API full GC cannot by itself explain a1.2-second HTTP tail.
An earlier premeasurement Docker name-lookup failure is recorded separately:
reattaching the existing image IDs resolves it without rebuilding an artifact.

Passing gates still contain **28 recovered R1 probe episodes** (25 engine,
three API) and **one recovered R2 engine episode**. The longest R1 observed
failure-to-recovery transition is about2.49s; R2 reports1.041s. There is no
unplanned healthy child replacement. These observation intervals are not exact
quote-downtime measurements. Bounded prehistory eviction/span filtering and loss
counters remain in evidence; no unique residual-probe cause or uninterrupted
quote-coverage guarantee is claimed.

## Connected Demo and final operating state

The [separate Demo protocol](../demo/okx-demo-quote-recovery.md) completes nine
fixed cases, configured600s Random rollover and active spot/both restart, with
407 independently confirmed canceled post-only orders. A later approved bounded
40USDT-per-market preset produces **five natural maker fills: one spot and four
perpetual**, then passes filled active restart, exact original fees/quantities,
three deduplication readbacks and canonical catalogue restoration.

Final independent review confirms11 unique fills (original six plus five new),
unchanged original history, perpetual base0.0089 BTC long /0.0028 BTC short,
no pending orders/mutations/HALT, and the same canonical container/image running
with **trading stopped**. Current catalogue is back to200USDT spot /400USDT
perpetual for new sessions; the completed test retains its40/40 pinned snapshot.

Temporary load containers/networks, maker-test container and loopback registry
tag are removed. Canonical Demo app/DB, original volume/private configuration,
private backups, unrelated resources and the two diagnostic image tags remain.
No live-account orders, database repair, automatic HALT clear or Cloud Run
deployment occurs. Production quote-coverage policy/SLO remains an operator
decision separate from this completed local test scope.

Raw full-load evidence: `build/mm-load/20261102-222142-0355ab`;
R2: `build/service-supervision/production-control-20261102-235056-715c88`.
Verification/retained failure: `build/quote-recovery-release-20261102/`.
