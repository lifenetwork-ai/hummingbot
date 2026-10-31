# Reports and verification results

This directory contains implementation history, audits, benchmark observations
and sanitized acceptance artifacts. Each report retains its original image,
source revision, environment and verification limits. A passing historical run
does not certify a later image. Operational guides and plans live in [docs/](../docs/README.md).

Use the current status at the top of each report. The implementation progress
file is a chronological journal; superseded pending items are history rather
than active checkboxes. The remaining cloud/FE checklists explicitly name their
external scope. Known evidence/performance limitations are not marked as done.

| Report | Associated artifact or scope |
|---|---|
| [Current-image closeout](mm-release-closeout.md), [evidence](mm-release-closeout-evidence.json) | Current full R1 passes all healthy windows and six recovery cases;4,884 fills match, soak p99 717ms and zero errors. Matched shipping R2 also passes; earlier failures and unresolved causes preserved. |
| [Focused historical-cause review](mm-historical-cause-review.md), [evidence](mm-historical-cause-evidence.json) | Test CPU/heartbeat timestamp defect reproduced and fixed;119 tests and packaged old/new comparison pass. Old H1/H2/H3 causes remain unproven; accepted R1/R2 unchanged. |
| [Measurement follow-up](mm-measurement-remediation.md), [evidence](mm-measurement-remediation-evidence.json) | On-loop observer cost corrected; measured disk-cache retention contract declared before new gate. Native114, bounded probes, full R1 and matched shipping R2 pass. |
| [Automatic incident capture](mm-incident-investigation.md), [evidence](mm-incident-investigation-evidence.json) | 61 related tests and matched bounded diagnostics pass; full release is subsequently accepted in current closeout, historical long-lag cause remains unproven |
| [Current-image OKX Demo](okx-demo-current-image.md), [evidence](okx-demo-current-image-evidence.json) | Image715a read-only preflight, nine profile/mode cases, real Random step/restart and 404 exact canceled post-only orders; no new natural fills |
| [Runtime supervision](mm-service-supervision.md) | Local acceptance complete: final image/regression/E2E/lifecycle, hour/load/faults and matched ordinary-entrypoint control pass; prior failures retained |
| [LIFE / OKX progress](implementation/LIFE_OKX_PROGRESS.md) | Phase history and Red/Green verification |
| [Cloud Run launch preparation](cloudrun-readiness-review.md) | Template/review/project-selection TDD, stopped-startup checklist and pending cloud inputs |
| [Local Demo startup](local-demo-startup.md), [evidence](local-demo-startup-evidence.json) | Rebuilt runtime smoke, preserved Demo DB/config and authenticated stopped-service API |
| [Single-service process isolation](mm-process-isolation-experiment.md), [evidence](mm-process-isolation-evidence.json) | Historical experiment and retained failures; later runtime adoption/acceptance is in the runtime supervision report |
| [Daily reporting latency](mm-reporting-latency.md), [evidence](mm-reporting-latency-evidence.json) | SQL scaling unresolved; fallback verified; later final architecture/load acceptance is complete in the runtime supervision report |
| [P99 investigation](mm-p99-investigation.md), [evidence](mm-p99-investigation-evidence.json) | Reproduced traced/untraced tails, confirmed daily SQL timeout, production-entrypoint control and remaining attribution limits |
| [Service availability audit](life-mm-availability-audit.md), [evidence](life-mm-availability-audit-evidence.json), [TDD remediation](life-mm-availability-remediation.md) | Five TDD fixes complete; 16 image E2E groups, continuous hour and six post-hour faults pass; original failures retained |
| [MM volatility defense](mm-volatility-defense.md), [evidence](mm-volatility-defense-evidence.json) | Weighted levels, reduced sizing, durable pause/recovery and current acceptance status |
| [Admin API / dashboard UX review](admin-api-dashboard-ux-review.md) | Current contract, browser access, operator workflow gaps and proposed FE follow-up; no runtime changes |
| [Operations API implementation](admin-operations-api-implementation.md) | YAML-only profile configuration, operator API changes and [scoped TDD/image/polling evidence](admin-operations-api-evidence.json) |
| [Working-order activity](mm-quote-activity.md) | Per-side proposal reasons, current-order fill observations, bounded absence warnings and [read-only API evidence](mm-quote-activity-evidence.json) |
| [OKX position startup](okx-ws-position-startup.md), [evidence](okx-ws-position-startup-evidence.json) | Cold WS instrument metadata ordering, cancellation/reconnect and image regression |
| [Long-running memory](mm-memory-retention-review.md), [evidence](mm-memory-retention-evidence.json) | Red/Green retention fixes, component heap experiment and scoped image lifecycle/RSS diagnostics |
| [Until-stopped sessions](mm-until-stopped.md), [evidence](mm-until-stopped-evidence.json) | Explicit continuous fixed/random mode, pinned restart clocks, finite transport/bootstrap limits and scoped verification |
| [E0 service availability](life-mm-e0-availability.md) | Failure isolation, task supervision, recovery and switch/checkpoint regression evidence |
| [E1 strict quote guards](life-mm-e1-quote-guards.md) | Target-market qualification, manual ranges, own-order bounds and recovery verification |
| [E2a managed drawdown](life-mm-e2a-drawdown.md) | Covered V2 report limits, durable peaks, retained-market valuation and position-preserving HALT |
| [E6 final verification](life-mm-e6-verification.md) | Final image matrix, measured fixes, all-feature load/soak and Demo scope; see current status |
| [E5 fee/NATR spreads](life-mm-e5-spread-policy.md) | Completed candles, fee-aware prices, rounding and seven-second outage recovery |
| [E4 history and liquidity](life-mm-e4-history-liquidity.md) | Atomic event/state updates, bounded APIs, sampled counters and constant queries |
| [E3 spot inventory skew](life-mm-e3-inventory-skew.md) | Original PMM interpolation, cash-only sizing, bounded allocations and local verification |
| [E2c listing bootstrap](life-mm-e2c-listing-bootstrap.md) | Bounded fresh-listing quotes, atomic episode, outside-market qualification and local image evidence |
| [E2b spot USDT outflow](life-mm-e2b-spot-outflow.md) | Rolling spending, BUY reservations, account binding, terminal proof and large-window measurements |
| [Full-image soak retest](life-mm-soak-retest.md) | [Gate evidence](life-mm-soak-retest-evidence.json); sixty-minute gate passed; further reproduction closed by user decision |
| [Short diagnostic iteration](life-mm-short-repro-review.md) | [CPU/probe evidence](life-mm-short-repro-evidence.json); datetime-only serialization and nested UTC fix; historical short-iteration results; follow-up closed after passing full gate |
| [Extended traced load review](life-mm-trace-load-review.md) | [Portable trace evidence](life-mm-trace-load-evidence.json); mixed passed, sixty-minute soak failed p99/timeouts |
| [MM logic-flow review](life-mm-logic-review.md) | [TDD/image/load evidence](life-mm-logic-review-evidence.json) for control, exposure, reconciliation and funding |
| [LIFE / OKX audit](LIFE_OKX_IMPLEMENTATION_AUDIT.md) | Historical source inventory and reuse review |
| [PostgreSQL verification](postgres-verification.md) | Port, packaging and release evidence |
| [PostgreSQL performance review](postgres-performance-review.md) | SQL, cache and recovery measurements |
| [Image optimization](cloudrun-image-optimization.md) | Image/package sizes and startup comparisons |
| [Load and soak results](life-mm-load-tests.md) | [Original acceptance JSON](life-mm-load-acceptance.json) |
| [MM optimization review](life-mm-optimization-review.md) | Cache, SQL and task-lifecycle follow-up |
| [Runtime flow optimization](life-mm-runtime-optimization.md) | Reader reuse, daily SQL, fresh state projections and exact outflow expiry; healthy soak plus separate five-fault retest |
| [Maintainability verification](life-mm-maintainability.json) | Six refactor items; native/PG, 13 functional groups plus separate baseline-fixture upgrade, short load and five faults; details in the progress report |
| [Cloud Run logging verification](cloudrun-logging-verification.md) | [Portable evidence](cloudrun-logging-verification.json); severity/UTC/redaction, SQL/warning fixes, rebuilt image and 13 functional groups; no new upgrade/soak or cloud deployment |
| [Pre-listing economic stress](life-mm-economic-stress.md) | [Portable evidence](life-mm-economic-stress-evidence.json); paired 648-case native/Linux replay, synthetic costs/liquidity/PnL, rolling-credit and margin caveats; no profit forecast |
| [OKX Demo acceptance](okx-demo-acceptance.md) | [Protocol JSON](okx-demo-acceptance.json), [natural-fill JSON](okx-demo-fill-acceptance.json) |
| [Current natural-fill follow-up](okx-demo-natural-fill.md) | [Evidence](okx-demo-natural-fill-evidence.json); six real maker fills, reviewed aggregate repair and passing real fixed-image filled restart |
| [Fixed-image full release](e526-release.md) | [Evidence](e526-release-evidence.json); strict full release gates failed; bounded reporting-isolation diagnostics passed |
| [Packaged supervisor-diagnostics release](probe-release-20261030.md) | Image dd602fb2: E2E/recovery/ordinary control pass; Random soak fails fill-producer drain, despite passing API latency |
| [Random handover investigation](mm-random-handover.md) | [Evidence](mm-random-handover-evidence.json); three verified bugs closed; burst-rate and historical liveness investigations remain open; combined full run failed burst rate |
| [Remaining burst/supervisor follow-up](mm-burst-followup.md) | [Evidence](mm-burst-followup-evidence.json); historical failed burst and verified client fix retained; later R1/R2 pass, original causal attribution remains limited |
| [Engine liveness investigation](mm-engine-liveness-investigation.md) | [Evidence](mm-engine-liveness-investigation-evidence.json); probe diagnostics and retained incident timeline; original sustained-failure cause remains open |
| [Explicit Demo preset validation](okx-demo-preset-validation.json) | Nine static-metadata selections and runtime/package smoke; no connected or full release gate |
| [Decimal precision evidence](postgres-decimal-precision.json) | Exact decimal storage migration and verification |
| [Funding backfill evidence](funding-backfill-evidence.json) | Implementation, image E2E/load probe and Demo acceptance scope |

Keep portable result summaries here. Large raw logs, ledgers, dumps and per-run
artifacts belong under ignored `build/`; credentials and private account setup
remain outside the checkout. Future acceptance exporters should write summaries
to `reports/` and update the corresponding plan checklist only after verification.
