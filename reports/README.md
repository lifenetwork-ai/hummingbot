# Reports and verification results

This directory contains implementation history, audits, benchmark observations
and sanitized acceptance artifacts. Each report retains its original image,
source revision, environment and verification limits. A passing historical run
does not certify a later image. Operational guides and plans live in [docs/](../docs/README.md).

| Report | Associated artifact or scope |
|---|---|
| [LIFE / OKX progress](implementation/LIFE_OKX_PROGRESS.md) | Phase history and Red/Green verification |
| [Service availability audit](life-mm-availability-audit.md), [evidence](life-mm-availability-audit-evidence.json) | Five confirmed lifecycle/DB/control-supervision findings; remediation open, runtime unchanged |
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
| [Explicit Demo preset validation](okx-demo-preset-validation.json) | Nine static-metadata selections and runtime/package smoke; no connected or full release gate |
| [Decimal precision evidence](postgres-decimal-precision.json) | Exact decimal storage migration and verification |
| [Funding backfill evidence](funding-backfill-evidence.json) | Implementation, image E2E/load probe and Demo acceptance scope |

Keep portable result summaries here. Large raw logs, ledgers, dumps and per-run
artifacts belong under ignored `build/`; credentials and private account setup
remain outside the checkout. Future acceptance exporters should write summaries
to `reports/` and update the corresponding plan checklist only after verification.
