# Partial-fill quote size: unnecessary cancellation fix

2026-11-04 (Asia/Saigon). **Confirmed bug fixed with Red/Green; targeted local
verification passes.** Runtime source `905bff7e3`, candidate `354fec1e`.
[Evidence](mm-partial-quote-size-evidence.json),
[plan](../../docs/plans/MM_OWNER_PUBLICATION_TIMEOUT_INVESTIGATION.md#spot-quote-size-churn-and-controller-outlier-follow-up--2026-11-04).

## Defect and narrow correction

A resting entry could be partially filled while its inventory-skew size ceiling
changed after the account update. `_defense_refresh_actions()` validated the
executor's **original amount**, including the already-filled portion. It could
cancel a still-safe resting remainder with `spread_quote_invalid` even though
its spread, fee floor and actual remaining size were valid. This affects BUY
and SELL; the retained diagnostic particularly exposed spot SELL churn.

A controlled stable-reference/candle replay separates size from spread and fee.
For example, a three-level SELL has original quote notional about24.75247525USDT;
a small fill changes its current skew ceiling to24.75245050. Its remaining
notional is about24.75237485, already below that ceiling. The original amount
fails while the actual remainder passes. This illustrates the reproduced
mechanism, not an exact replay of every historical venue order.

Two native cases first fail on the original controller. The fix reuses existing
in-flight entry data when it has one matching identity/side/pair/price,
acknowledged exchange ID, live OPEN/PARTIALLY_FILLED state and finite consistent
partial quantities. Only resting-quote validation receives `amount - filled`.
Missing, ambiguous, pending or inconsistent metadata keeps the full config amount.

**No config amount is mutated.** Hard account exposure and pending allocation
continue reserving the original full entry. Spread, fee, volatility-size,
readiness, ownership and send checks remain in force. Stop/guard still cancels
quotes and preserves filled positions. New orders still validate their full size.
There is no new config, SQL/schema, cache, dependency or service. The helper
performs at most three existing in-memory order-map lookups per executor and
retains no additional state.

## TDD and lifecycle verification

- Initial Red: two failing BUY/SELL cases,20 existing control cases pass.
- Final source: **528 passed plus two subtests**, including38 new cases covering
  cash/fill-driven size drift,18-decimal late fills and duplicate snapshots,
  pending/missing/ambiguous entry metadata, genuine spread/fee/size reduction,
  full allocation reservation and full BUY exposure reservation.
- Targeted packaged E2E **LIFE MM lifecycle** and **volatility defense** pass:
  three profiles and spot/perpetual/both, restart, delayed cancellation,
  protective pause/recovery and ledger checks retain their declared scope.
- Source commit `905bff7e3`; the later test-only addition extends late-fill
  coverage without changing the candidate's runtime bytes.

Dependency/test warnings remain in the logs. This does not claim a new full
native/PG/release matrix; the relevant controller/executor/orchestrator/strategy
suite and the two packaged groups are the checks actually repeated.

## Matched-settings 600-second diagnostics

Baseline `086454de`: `build/mm-load/20261104-190227-81e4bb/`.
Candidate `354fec1e`: `build/mm-load/20261104-230000-16d899/`.
Both run fixed follow_market/both with identical fixture and measurement-source
hashes:100,000 historical orders/fills, original read rates,2 application CPUs,
TTL15, enhancements and volatility defense. Existing bounded MM activity,
incident/GC/market and independent quote instruments are enabled in both.

| Observation | Baseline | Candidate |
| --- | ---: | ---: |
| HTTP200 / unexpected errors or stale | 13,200 /0 | 13,200 /0 |
| Exact unique synthetic fills/fees/positions | 600 | 600 |
| Primary API p99 | 347.99ms | 279.97ms |
| Auxiliary API p99 | 118.91ms | 92.68ms |
| Successful submit returns | 219 | 141 |
| Successful cancel returns | 219 | 141 |
| Spot spread-invalid proposals / referenced executor IDs | 40 /120 | 19 /35 |
| Spot BUY+SELL point samples | 494/597 | 574/597 |
| Perpetual BUY+SELL point samples | 574/597 | 576/597 |
| Both markets two-sided point samples | 490/597 | 566/597 |
| Aggregate-empty point samples | 13 | 8 |
| Longest empty-point first-to-last span | 2.004s | 1.006s |
| Tick execution p99 / maximum | 60.70/89.47ms | 47.02/180.54ms |
| Controller execution p99 / maximum | 27.15/568.53ms | 22.62/262.85ms |

Observed spread-invalid referenced executor IDs decrease120→35 (about71%); total
successful send/cancel counts each decrease219→141 (about36%). They are proposal
and connector-call counts, not all proven unnecessary cancellations. The native
Red/Green establishes the defect; this sequential pair shows observed workload
benefits. Different host scheduling, candle/guard paths and lifecycle trajectories
prevent exclusive attribution of every metric change. **Not every latency
maximum improves**: maximum tick execution is higher in the candidate. Samples
are point coverage, not elapsed-time uptime or continuous liquidity guarantees.

The candidate is a reviewed Python-file-derived image from the exact baseline
parent. All parent filesystem layers remain; a manifest comparison of1,316
application files finds exactly one changed runtime file, `life_mm.py`, matching
the committed source hash. Dependency layers remain intact. Its Dockerfile,
parent/image identities and build provenance are retained. The canonical
Dockerfile is not rebuilt in this bounded phase; this artifact has its own ID
and cannot inherit the baseline's old full-release/hour acceptance.

## Controller jitter, probes and remaining gaps

The candidate completes600 ticks and1,184 controller cycles successfully. Its
maximum tick/controller start gaps are1,163.41/1,264.31ms. Controller execution
outlier is262.85ms; this run does not reproduce or exclusively explain the older
568.53ms case. The old overlap with observer992ms remains an attribution limit;
inclusive event-loop thread CPU across awaits is not exclusive observer CPU.
No additional cache, pool increase or publication/health deadline change is
justified or made from that overlap.

Two in-window engine probes recover at about+509.125s and+575.325s, with failed
requests303.738/301.146ms and transition intervals1.046/1.036s. MM ticks/controller
cycles continue. The first context samples12→1→1→0→6 orders during replacement;
the second0→6→12→12→12. The bounded context includes cancellation and successful
replacement submissions. These are real observed quote gaps, not a claim that
MM quotes continuously or that the probe caused the withdrawal. No unexpected
service failure, publication failure or supervised child restart appears.

Both activity/venue streams retain zero file drops; bounded internal spans and
rolling context loss remain explicit. Preparation close summaries are not final
measured-engine flush proof. The35 remaining spread-invalid referenced executors
may include legitimate size/spread changes or conservative unknown-entry handling;
this fix does not suppress them or certify a production quote-coverage SLO.

## Restoration and remaining release scope

A private backup precedes the isolated verification. Actual Demo is restored
**stopped on its prior086454de image**, owner available, no recovery block or
pending mutation. All four counts/hashes match:3,513 orders,23 fills,three
positions and2,848 executors. Candidate354fec1e is tested separately; actual Demo
is not silently upgraded, repaired or traded.

The code fix, safety regressions, targeted E2E and short comparison are complete.
No new full R1/R2,60-minute candidate soak,full PG suite, actual Demo trade or
Cloud Run deployment is claimed. Prior gates keep their original image scope.
The historical unique cause of all probe/controller outliers remains unproven.
