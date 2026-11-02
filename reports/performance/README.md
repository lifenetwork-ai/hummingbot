# Performance and economic experiments

[All reports](../README.md) · [Current release](../release/mm-release-closeout.md)

Load, memory, optimization and synthetic economic measurements.
Compare runs only within their declared image, fixture and measurement scope.

| Report | Evidence and scope |
| --- | --- |
| [Reporting/startup optimization](mm-reporting-startup-optimization.md), [evidence](mm-reporting-startup-evidence.json) | Per-filter bounded daily coalescing, exact lightweight historical rows and transaction-local report resource bound; image39198499 full R1 and matched shipping R2 pass; soak p99 553ms, zero errors and +14.03MiB retained. R1 recovered probes/causal limits remain explicit |
| [Long-running memory](mm-memory-retention-review.md), [evidence](mm-memory-retention-evidence.json) | Red/Green retention fixes, component heap experiment and scoped image lifecycle/RSS diagnostics |
| [Full-image soak retest](life-mm-soak-retest.md) | [Gate evidence](life-mm-soak-retest-evidence.json); sixty-minute gate passed; further reproduction closed by user decision |
| [Load and soak results](life-mm-load-tests.md) | [Original acceptance JSON](life-mm-load-acceptance.json) |
| [MM optimization review](life-mm-optimization-review.md) | Cache, SQL and task-lifecycle follow-up |
| [Runtime flow optimization](life-mm-runtime-optimization.md) | All three Nov1 changes complete; native2178/PG268/Swagger and three targeted candidate-image groups pass. Full hour/release scope stays separate. [Review](runtime-flow-review-20261101.json), [implementation](runtime-flow-remediation-20261101.json) |
| [Pre-listing economic stress](life-mm-economic-stress.md) | [Portable evidence](life-mm-economic-stress-evidence.json); paired 648-case native/Linux replay, synthetic costs/liquidity/PnL, rolling-credit and margin caveats; no profit forecast |
