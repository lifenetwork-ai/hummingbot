# Performance and resource evidence

[All reports](../README.md) · [Current full load](../release/mm-runtime-followup-load.md)

| Report | Scope |
| --- | --- |
| [Latest runtime remediation](../implementation/MM_RUNTIME_FOLLOWUP.md), [evidence](../implementation/mm-runtime-followup-evidence.json) | Fresh exposure reads, per-report summaries, readiness and availability boundaries |
| [Recorder intent projection](mm-intent-checkpoint-projection.md), [evidence](mm-intent-checkpoint-projection-evidence.json) | SQL/payload Red/Green, rejected variants and paired component costs |
| [Memory retention](mm-memory-retention-review.md), [evidence](mm-memory-retention-evidence.json) | Retention fixes and original scoped heap/resource verification |
| [Economic stress](life-mm-economic-stress.md), [evidence](life-mm-economic-stress-evidence.json) | Synthetic cost/inventory/liquidity assumptions; no profit forecast |
| [Original load results](life-mm-load-tests.md), [acceptance](life-mm-load-acceptance.json) | Historical baseline/result definitions, not current-image acceptance |
| [Worker lanes / stopped Demo](mm-admin-worker-lanes.md), [evidence](mm-admin-worker-lanes-evidence.json) | Original `086454de…` image and stopped-Demo restoration scope |

Other completed experiments, earlier load attempts and measurements are retained
in the [Git history](../README.md#historical-records). Component CPU savings and inter-run
full-load latency have different scopes; preserve both improvements and regressions.
