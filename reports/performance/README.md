# Performance and resource evidence

[All reports](../README.md) · [Current full load](../release/mm-runtime-followup-load.md)

| Report | Scope |
| --- | --- |
| [Quiet-feed / unsent-quote recovery](mm-feed-quote-recovery.md) | Bounded per-book REST recovery, final-consumer WS race protection, first-entry repricing and operational summaries; targeted offline qualification, separate from Cloud/full release |
| [Managed local quote activity](mm-managed-local-quote-activity.md), [evidence](mm-managed-local-quote-activity-evidence.json) | Two 180-second diagnostics, independent venue samples and actual tick/order timings; explicit refresh gaps and pre-window probe scope; separate from the approved 30-minute operating target |
| [Reporting snapshot copy](../implementation/MM_RUNTIME_FOLLOWUP.md#reporting-snapshot-copy-cost--2026-11-07), [evidence](../implementation/mm-runtime-followup-evidence.json) | One retained-history traversal instead of two; paired component CPU improves, allocation peaks mixed; native 2,788 + 11 subtests / related PG 36 pass; packaged in the previously qualified `a6fe88c0…` image; current `b090430c…` full gate passes, with nonuniform whole-system changes |
| [Ownership reads / write-fence projection](../implementation/MM_RUNTIME_FOLLOWUP.md#fresh-write-fence-identity-projection--2026-11-07), [evidence](mm-owner-read-evidence.json) | Component/source: fresh write proof columns14→1, paired payload costs, original read-COMMIT TDD and failure semantics; latest native 2,780 + 11 subtests / full PG 556 pass; projection retained in current `b090430c…`; full gate passes separately from component/source results |
| [Fill-flush follow-up](../implementation/MM_RUNTIME_FOLLOWUP.md#probe-follow-up-fill-autoflush--2026-11-07), [evidence](mm-fill-flush-evidence.json) | Component counts/timing and failure semantics; native passes, PG530 + corrected benchmark/new-case rerun9 pass; image load measured separately |
| [Latest runtime remediation](../implementation/MM_RUNTIME_FOLLOWUP.md), [evidence](../implementation/mm-runtime-followup-evidence.json) | Fresh exposure reads, per-report summaries, readiness and availability boundaries |
| [Recorder intent projection](mm-intent-checkpoint-projection.md), [evidence](mm-intent-checkpoint-projection-evidence.json) | SQL/payload Red/Green, rejected variants and paired component costs |
| [Memory retention](mm-memory-retention-review.md), [evidence](mm-memory-retention-evidence.json) | Retention fixes and original scoped heap/resource verification |
| [Economic stress](life-mm-economic-stress.md), [evidence](life-mm-economic-stress-evidence.json) | Synthetic cost/inventory/liquidity assumptions; no profit forecast |
| [Original load results](life-mm-load-tests.md), [acceptance](life-mm-load-acceptance.json) | Historical baseline/result definitions, not current-image acceptance |
| [Worker lanes / stopped Demo](mm-admin-worker-lanes.md), [evidence](mm-admin-worker-lanes-evidence.json) | Original `086454de…` image and stopped-Demo restoration scope |

Other completed experiments, earlier load attempts and measurements are retained
in the [Git history](../README.md#historical-records). Component CPU savings and inter-run
full-load latency have different scopes; preserve both improvements and regressions.
