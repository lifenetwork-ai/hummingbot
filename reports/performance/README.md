# Performance and economic experiments

[All reports](../README.md) · [Current release](../release/mm-release-closeout.md)

Load, memory, optimization and synthetic economic measurements.
Compare runs only within their declared image, fixture and measurement scope.

| Report | Evidence and scope |
| --- | --- |
| [Long-running memory](mm-memory-retention-review.md), [evidence](mm-memory-retention-evidence.json) | Red/Green retention fixes, component heap experiment and scoped image lifecycle/RSS diagnostics |
| [Full-image soak retest](life-mm-soak-retest.md) | [Gate evidence](life-mm-soak-retest-evidence.json); sixty-minute gate passed; further reproduction closed by user decision |
| [Load and soak results](life-mm-load-tests.md) | [Original acceptance JSON](life-mm-load-acceptance.json) |
| [MM optimization review](life-mm-optimization-review.md) | Cache, SQL and task-lifecycle follow-up |
| [Runtime flow optimization](life-mm-runtime-optimization.md) | Reader reuse, daily SQL, fresh state projections and exact outflow expiry; healthy soak plus separate five-fault retest |
| [Pre-listing economic stress](life-mm-economic-stress.md) | [Portable evidence](life-mm-economic-stress-evidence.json); paired 648-case native/Linux replay, synthetic costs/liquidity/PnL, rolling-credit and margin caveats; no profit forecast |
