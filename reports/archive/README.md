# Historical plans and verification archive

Completed/superseded snapshots are compressed to reduce working-tree size and
keep current indexes readable. Nothing here is a new pass or a closed open item.
Original failed attempts, unchecked boxes, image/date scopes and evidence bytes
are retained. Current status lives in [reports](../README.md) and
[remaining verification](../../docs/plans/README.md).

Snapshot baseline: `4ed4365cd86cd06018d81432343c3cbb9aa34b52`. [Machine-readable manifest](manifest.json)
contains original paths, SHA-256, sizes, archive members, disposition and full
historical unchecked-item text. Exact original files also remain in Git history.

## Consolidation result

Plans: **27 → 5** current files. Reports: **205 → 48** current files plus the
archive/catalog/manifest. All 192 historical snapshots match Git byte-for-byte;
current relative Markdown links and archive anchors are checked. Detailed completed
plans and journals remain recoverable. The remaining-work register preserves
external acceptance, policy and historical attribution limits.

Working-tree size figures are in the manifest. Git object history and ignored
`build/` artifacts are retained; this is not a Git-history rewrite or disk-prune claim.

## Read or restore a snapshot

List/extract into a separate directory; avoid overwriting current condensed plans:

```sh
python -m zipfile -l reports/archive/history-20261107-plans.zip
python -m zipfile -e reports/archive/history-20261107-plans.zip /tmp/mm-history
# Or print a single original file:
unzip -p reports/archive/history-20261107-plans.zip docs/plans/MM_RUNTIME_LOGIC_AUDIT_TDD_PLAN.md
```

Archive members retain repository-relative `docs/plans/` and `reports/` paths.
Historical paths/commands inside JSON still describe the original run layout.
Current links to retired records point to catalog entries below. Compare an
extracted member with its manifest SHA-256 before relying on its historical scope.

## Bundles

| Bundle | Members | Bytes |
| --- | ---: | ---: |
| [history-20261107-demo.zip](history-20261107-demo.zip) | 1 | 1,234 |
| [history-20261107-implementation.zip](history-20261107-implementation.zip) | 43 | 305,211 |
| [history-20261107-index.zip](history-20261107-index.zip) | 1 | 10,407 |
| [history-20261107-investigations.zip](history-20261107-investigations.zip) | 54 | 360,272 |
| [history-20261107-performance.zip](history-20261107-performance.zip) | 23 | 163,149 |
| [history-20261107-plans.zip](history-20261107-plans.zip) | 26 | 186,061 |
| [history-20261107-platform.zip](history-20261107-platform.zip) | 8 | 32,472 |
| [history-20261107-release.zip](history-20261107-release.zip) | 36 | 206,944 |

## Original records

Unchecked counts refer to the original snapshot, not newly required current gates.

| Original path | Bundle | Historical unchecked boxes |
| --- | --- | ---: |
| <a id="record-001"></a>`docs/plans/ADMIN_ACCESS_OKX_CREDENTIALS_TDD_PLAN.md` | [history-20261107-plans.zip](history-20261107-plans.zip) | 1 |
| <a id="record-002"></a>`docs/plans/ADMIN_OPERATIONS_API_TDD_PLAN.md` | [history-20261107-plans.zip](history-20261107-plans.zip) | 0 |
| <a id="record-003"></a>`docs/plans/LIFE_MM_ECONOMIC_STRESS_PLAN.md` | [history-20261107-plans.zip](history-20261107-plans.zip) | 0 |
| <a id="record-004"></a>`docs/plans/LIFE_OKX_IMPLEMENTATION_PLAN_TDD.md` | [history-20261107-plans.zip](history-20261107-plans.zip) | 0 |
| <a id="record-005"></a>`docs/plans/LIFE_OKX_MM_ENHANCEMENTS_TDD_PLAN.md` | [history-20261107-plans.zip](history-20261107-plans.zip) | 0 |
| <a id="record-006"></a>`docs/plans/MM_ADMIN_WORKER_LANES_TDD_PLAN.md` | [history-20261107-plans.zip](history-20261107-plans.zip) | 0 |
| <a id="record-007"></a>`docs/plans/MM_CHECKPOINT_POLL_OPTIMIZATION_TDD_PLAN.md` | [history-20261107-plans.zip](history-20261107-plans.zip) | 0 |
| <a id="record-008"></a>`docs/plans/MM_CONTROLLER_PERSISTENCE_OPTIMIZATION_TDD_PLAN.md` | [history-20261107-plans.zip](history-20261107-plans.zip) | 0 |
| <a id="record-009"></a>`docs/plans/MM_ENGINE_PROBE_LATENCY_TDD_PLAN.md` | [history-20261107-plans.zip](history-20261107-plans.zip) | 0 |
| <a id="record-010"></a>`docs/plans/MM_ENGINE_RESIDUAL_PROBE_INVESTIGATION.md` | [history-20261107-plans.zip](history-20261107-plans.zip) | 0 |
| <a id="record-011"></a>`docs/plans/MM_ENGINE_STATUS_PUBLICATION_TDD_PLAN.md` | [history-20261107-plans.zip](history-20261107-plans.zip) | 0 |
| <a id="record-012"></a>`docs/plans/MM_MEMORY_RETENTION_REVIEW.md` | [history-20261107-plans.zip](history-20261107-plans.zip) | 0 |
| <a id="record-013"></a>`docs/plans/MM_OWNER_PUBLICATION_TIMEOUT_INVESTIGATION.md` | [history-20261107-plans.zip](history-20261107-plans.zip) | 1 |
| <a id="record-014"></a>`docs/plans/MM_QUOTE_ACTIVITY_TDD_PLAN.md` | [history-20261107-plans.zip](history-20261107-plans.zip) | 0 |
| <a id="record-015"></a>`docs/plans/MM_QUOTE_WAIT_DIAGNOSTICS_TDD_PLAN.md` | [history-20261107-plans.zip](history-20261107-plans.zip) | 0 |
| <a id="record-016"></a>`docs/plans/MM_RECORDER_CHECKPOINT_PROJECTION_TDD_PLAN.md` | [history-20261107-plans.zip](history-20261107-plans.zip) | 0 |
| <a id="record-017"></a>`docs/plans/MM_REPORTING_LATENCY_TDD_PLAN.md` | [history-20261107-plans.zip](history-20261107-plans.zip) | 0 |
| <a id="record-018"></a>`docs/plans/MM_REPORTING_STARTUP_OPTIMIZATION_TDD_PLAN.md` | [history-20261107-plans.zip](history-20261107-plans.zip) | 0 |
| <a id="record-019"></a>`docs/plans/MM_RUNTIME_LOGIC_AUDIT_TDD_PLAN.md` | [history-20261107-plans.zip](history-20261107-plans.zip) | 1 |
| <a id="record-020"></a>`docs/plans/MM_SHUTDOWN_CHECKPOINT_TDD_PLAN.md` | [history-20261107-plans.zip](history-20261107-plans.zip) | 0 |
| <a id="record-021"></a>`docs/plans/MM_UNTIL_STOPPED_TDD_PLAN.md` | [history-20261107-plans.zip](history-20261107-plans.zip) | 0 |
| <a id="record-022"></a>`docs/plans/MM_VOLATILITY_DEFENSE_TDD_PLAN.md` | [history-20261107-plans.zip](history-20261107-plans.zip) | 0 |
| <a id="record-023"></a>`docs/plans/OKX_DEMO_CURRENT_IMAGE_ACCEPTANCE.md` | [history-20261107-plans.zip](history-20261107-plans.zip) | 3 |
| <a id="record-024"></a>`docs/plans/OKX_EXPIRED_SUBMISSION_TDD_PLAN.md` | [history-20261107-plans.zip](history-20261107-plans.zip) | 1 |
| <a id="record-025"></a>`docs/plans/OKX_WS_POSITION_STARTUP_TDD_PLAN.md` | [history-20261107-plans.zip](history-20261107-plans.zip) | 0 |
| <a id="record-026"></a>`docs/plans/POSTGRES_CLOUD_RUN_PORT_PLAN.md` | [history-20261107-plans.zip](history-20261107-plans.zip) | 0 |
| <a id="record-027"></a>`reports/README.md` | [history-20261107-index.zip](history-20261107-index.zip) | 0 |
| <a id="record-028"></a>`reports/demo/README.md` | [history-20261107-demo.zip](history-20261107-demo.zip) | 0 |
| <a id="record-029"></a>`reports/implementation/ADMIN_ACCESS_CREDENTIALS_PROGRESS.md` | [history-20261107-implementation.zip](history-20261107-implementation.zip) | 0 |
| <a id="record-030"></a>`reports/implementation/LIFE_OKX_IMPLEMENTATION_AUDIT.md` | [history-20261107-implementation.zip](history-20261107-implementation.zip) | 0 |
| <a id="record-031"></a>`reports/implementation/LIFE_OKX_PROGRESS.md` | [history-20261107-implementation.zip](history-20261107-implementation.zip) | 0 |
| <a id="record-032"></a>`reports/implementation/README.md` | [history-20261107-implementation.zip](history-20261107-implementation.zip) | 0 |
| <a id="record-033"></a>`reports/implementation/admin-api-dashboard-ux-review.md` | [history-20261107-implementation.zip](history-20261107-implementation.zip) | 2 |
| <a id="record-034"></a>`reports/implementation/admin-operations-api-evidence.json` | [history-20261107-implementation.zip](history-20261107-implementation.zip) | 0 |
| <a id="record-035"></a>`reports/implementation/admin-operations-api-implementation.md` | [history-20261107-implementation.zip](history-20261107-implementation.zip) | 0 |
| <a id="record-036"></a>`reports/implementation/funding-backfill-evidence.json` | [history-20261107-implementation.zip](history-20261107-implementation.zip) | 0 |
| <a id="record-037"></a>`reports/implementation/life-mm-availability-audit-evidence.json` | [history-20261107-implementation.zip](history-20261107-implementation.zip) | 0 |
| <a id="record-038"></a>`reports/implementation/life-mm-availability-audit.md` | [history-20261107-implementation.zip](history-20261107-implementation.zip) | 0 |
| <a id="record-039"></a>`reports/implementation/life-mm-availability-remediation-evidence.json` | [history-20261107-implementation.zip](history-20261107-implementation.zip) | 0 |
| <a id="record-040"></a>`reports/implementation/life-mm-availability-remediation.md` | [history-20261107-implementation.zip](history-20261107-implementation.zip) | 0 |
| <a id="record-041"></a>`reports/implementation/life-mm-e0-availability.json` | [history-20261107-implementation.zip](history-20261107-implementation.zip) | 0 |
| <a id="record-042"></a>`reports/implementation/life-mm-e0-availability.md` | [history-20261107-implementation.zip](history-20261107-implementation.zip) | 0 |
| <a id="record-043"></a>`reports/implementation/life-mm-e1-quote-guards.json` | [history-20261107-implementation.zip](history-20261107-implementation.zip) | 0 |
| <a id="record-044"></a>`reports/implementation/life-mm-e1-quote-guards.md` | [history-20261107-implementation.zip](history-20261107-implementation.zip) | 0 |
| <a id="record-045"></a>`reports/implementation/life-mm-e2a-drawdown.json` | [history-20261107-implementation.zip](history-20261107-implementation.zip) | 0 |
| <a id="record-046"></a>`reports/implementation/life-mm-e2a-drawdown.md` | [history-20261107-implementation.zip](history-20261107-implementation.zip) | 0 |
| <a id="record-047"></a>`reports/implementation/life-mm-e2b-spot-outflow.json` | [history-20261107-implementation.zip](history-20261107-implementation.zip) | 0 |
| <a id="record-048"></a>`reports/implementation/life-mm-e2b-spot-outflow.md` | [history-20261107-implementation.zip](history-20261107-implementation.zip) | 0 |
| <a id="record-049"></a>`reports/implementation/life-mm-e2c-listing-bootstrap.json` | [history-20261107-implementation.zip](history-20261107-implementation.zip) | 0 |
| <a id="record-050"></a>`reports/implementation/life-mm-e2c-listing-bootstrap.md` | [history-20261107-implementation.zip](history-20261107-implementation.zip) | 0 |
| <a id="record-051"></a>`reports/implementation/life-mm-e3-inventory-skew.json` | [history-20261107-implementation.zip](history-20261107-implementation.zip) | 0 |
| <a id="record-052"></a>`reports/implementation/life-mm-e3-inventory-skew.md` | [history-20261107-implementation.zip](history-20261107-implementation.zip) | 0 |
| <a id="record-053"></a>`reports/implementation/life-mm-e4-history-liquidity.json` | [history-20261107-implementation.zip](history-20261107-implementation.zip) | 0 |
| <a id="record-054"></a>`reports/implementation/life-mm-e4-history-liquidity.md` | [history-20261107-implementation.zip](history-20261107-implementation.zip) | 0 |
| <a id="record-055"></a>`reports/implementation/life-mm-e5-spread-policy.json` | [history-20261107-implementation.zip](history-20261107-implementation.zip) | 0 |
| <a id="record-056"></a>`reports/implementation/life-mm-e5-spread-policy.md` | [history-20261107-implementation.zip](history-20261107-implementation.zip) | 0 |
| <a id="record-057"></a>`reports/implementation/life-mm-e6-verification.json` | [history-20261107-implementation.zip](history-20261107-implementation.zip) | 0 |
| <a id="record-058"></a>`reports/implementation/life-mm-e6-verification.md` | [history-20261107-implementation.zip](history-20261107-implementation.zip) | 0 |
| <a id="record-059"></a>`reports/implementation/life-mm-latent-bug-audit-20261101.json` | [history-20261107-implementation.zip](history-20261107-implementation.zip) | 0 |
| <a id="record-060"></a>`reports/implementation/life-mm-latent-bug-fixes-20261101.json` | [history-20261107-implementation.zip](history-20261107-implementation.zip) | 0 |
| <a id="record-061"></a>`reports/implementation/life-mm-logic-review-evidence.json` | [history-20261107-implementation.zip](history-20261107-implementation.zip) | 0 |
| <a id="record-062"></a>`reports/implementation/life-mm-logic-review.md` | [history-20261107-implementation.zip](history-20261107-implementation.zip) | 0 |
| <a id="record-063"></a>`reports/implementation/life-mm-maintainability.json` | [history-20261107-implementation.zip](history-20261107-implementation.zip) | 0 |
| <a id="record-064"></a>`reports/implementation/mm-quote-activity-evidence.json` | [history-20261107-implementation.zip](history-20261107-implementation.zip) | 0 |
| <a id="record-065"></a>`reports/implementation/mm-quote-activity.md` | [history-20261107-implementation.zip](history-20261107-implementation.zip) | 0 |
| <a id="record-066"></a>`reports/implementation/mm-until-stopped-evidence.json` | [history-20261107-implementation.zip](history-20261107-implementation.zip) | 0 |
| <a id="record-067"></a>`reports/implementation/mm-until-stopped.md` | [history-20261107-implementation.zip](history-20261107-implementation.zip) | 0 |
| <a id="record-068"></a>`reports/implementation/mm-volatility-defense-evidence.json` | [history-20261107-implementation.zip](history-20261107-implementation.zip) | 0 |
| <a id="record-069"></a>`reports/implementation/mm-volatility-defense.md` | [history-20261107-implementation.zip](history-20261107-implementation.zip) | 0 |
| <a id="record-070"></a>`reports/implementation/okx-ws-position-startup-evidence.json` | [history-20261107-implementation.zip](history-20261107-implementation.zip) | 0 |
| <a id="record-071"></a>`reports/implementation/okx-ws-position-startup.md` | [history-20261107-implementation.zip](history-20261107-implementation.zip) | 0 |
| <a id="record-072"></a>`reports/investigations/README.md` | [history-20261107-investigations.zip](history-20261107-investigations.zip) | 0 |
| <a id="record-073"></a>`reports/investigations/life-mm-short-repro-evidence.json` | [history-20261107-investigations.zip](history-20261107-investigations.zip) | 0 |
| <a id="record-074"></a>`reports/investigations/life-mm-short-repro-review.md` | [history-20261107-investigations.zip](history-20261107-investigations.zip) | 0 |
| <a id="record-075"></a>`reports/investigations/life-mm-trace-load-evidence.json` | [history-20261107-investigations.zip](history-20261107-investigations.zip) | 0 |
| <a id="record-076"></a>`reports/investigations/life-mm-trace-load-review.md` | [history-20261107-investigations.zip](history-20261107-investigations.zip) | 0 |
| <a id="record-077"></a>`reports/investigations/mm-burst-followup-evidence.json` | [history-20261107-investigations.zip](history-20261107-investigations.zip) | 0 |
| <a id="record-078"></a>`reports/investigations/mm-burst-followup.md` | [history-20261107-investigations.zip](history-20261107-investigations.zip) | 0 |
| <a id="record-079"></a>`reports/investigations/mm-checkpoint-probe-correlation-evidence.json` | [history-20261107-investigations.zip](history-20261107-investigations.zip) | 0 |
| <a id="record-080"></a>`reports/investigations/mm-checkpoint-probe-correlation.md` | [history-20261107-investigations.zip](history-20261107-investigations.zip) | 0 |
| <a id="record-081"></a>`reports/investigations/mm-cleanup-state-probe-code-audit-evidence.json` | [history-20261107-investigations.zip](history-20261107-investigations.zip) | 0 |
| <a id="record-082"></a>`reports/investigations/mm-cleanup-state-probe-code-audit.md` | [history-20261107-investigations.zip](history-20261107-investigations.zip) | 0 |
| <a id="record-083"></a>`reports/investigations/mm-current-probe-correlation-evidence.json` | [history-20261107-investigations.zip](history-20261107-investigations.zip) | 0 |
| <a id="record-084"></a>`reports/investigations/mm-current-probe-correlation.md` | [history-20261107-investigations.zip](history-20261107-investigations.zip) | 0 |
| <a id="record-085"></a>`reports/investigations/mm-engine-liveness-investigation-evidence.json` | [history-20261107-investigations.zip](history-20261107-investigations.zip) | 0 |
| <a id="record-086"></a>`reports/investigations/mm-engine-liveness-investigation.md` | [history-20261107-investigations.zip](history-20261107-investigations.zip) | 0 |
| <a id="record-087"></a>`reports/investigations/mm-engine-probe-controlled-evidence.json` | [history-20261107-investigations.zip](history-20261107-investigations.zip) | 0 |
| <a id="record-088"></a>`reports/investigations/mm-engine-probe-controlled.md` | [history-20261107-investigations.zip](history-20261107-investigations.zip) | 0 |
| <a id="record-089"></a>`reports/investigations/mm-engine-probe-latency-evidence.json` | [history-20261107-investigations.zip](history-20261107-investigations.zip) | 0 |
| <a id="record-090"></a>`reports/investigations/mm-engine-probe-latency.md` | [history-20261107-investigations.zip](history-20261107-investigations.zip) | 0 |
| <a id="record-091"></a>`reports/investigations/mm-engine-residual-probes-evidence.json` | [history-20261107-investigations.zip](history-20261107-investigations.zip) | 0 |
| <a id="record-092"></a>`reports/investigations/mm-engine-residual-probes.md` | [history-20261107-investigations.zip](history-20261107-investigations.zip) | 0 |
| <a id="record-093"></a>`reports/investigations/mm-engine-status-publication-evidence.json` | [history-20261107-investigations.zip](history-20261107-investigations.zip) | 0 |
| <a id="record-094"></a>`reports/investigations/mm-engine-status-publication.md` | [history-20261107-investigations.zip](history-20261107-investigations.zip) | 0 |
| <a id="record-095"></a>`reports/investigations/mm-guard-recovery-coverage-evidence.json` | [history-20261107-investigations.zip](history-20261107-investigations.zip) | 0 |
| <a id="record-096"></a>`reports/investigations/mm-guard-recovery-coverage.md` | [history-20261107-investigations.zip](history-20261107-investigations.zip) | 0 |
| <a id="record-097"></a>`reports/investigations/mm-historical-cause-evidence.json` | [history-20261107-investigations.zip](history-20261107-investigations.zip) | 0 |
| <a id="record-098"></a>`reports/investigations/mm-historical-cause-review.md` | [history-20261107-investigations.zip](history-20261107-investigations.zip) | 0 |
| <a id="record-099"></a>`reports/investigations/mm-incident-investigation-evidence.json` | [history-20261107-investigations.zip](history-20261107-investigations.zip) | 0 |
| <a id="record-100"></a>`reports/investigations/mm-incident-investigation.md` | [history-20261107-investigations.zip](history-20261107-investigations.zip) | 0 |
| <a id="record-101"></a>`reports/investigations/mm-measurement-remediation-evidence.json` | [history-20261107-investigations.zip](history-20261107-investigations.zip) | 0 |
| <a id="record-102"></a>`reports/investigations/mm-measurement-remediation.md` | [history-20261107-investigations.zip](history-20261107-investigations.zip) | 0 |
| <a id="record-103"></a>`reports/investigations/mm-offline-history-preparation-evidence.json` | [history-20261107-investigations.zip](history-20261107-investigations.zip) | 0 |
| <a id="record-104"></a>`reports/investigations/mm-owner-publication-timeout-evidence.json` | [history-20261107-investigations.zip](history-20261107-investigations.zip) | 0 |
| <a id="record-105"></a>`reports/investigations/mm-owner-publication-timeout.md` | [history-20261107-investigations.zip](history-20261107-investigations.zip) | 0 |
| <a id="record-106"></a>`reports/investigations/mm-owner-publication-wal-followup-evidence.json` | [history-20261107-investigations.zip](history-20261107-investigations.zip) | 0 |
| <a id="record-107"></a>`reports/investigations/mm-p99-investigation-evidence.json` | [history-20261107-investigations.zip](history-20261107-investigations.zip) | 0 |
| <a id="record-108"></a>`reports/investigations/mm-p99-investigation.md` | [history-20261107-investigations.zip](history-20261107-investigations.zip) | 0 |
| <a id="record-109"></a>`reports/investigations/mm-process-isolation-evidence.json` | [history-20261107-investigations.zip](history-20261107-investigations.zip) | 0 |
| <a id="record-110"></a>`reports/investigations/mm-process-isolation-experiment.md` | [history-20261107-investigations.zip](history-20261107-investigations.zip) | 0 |
| <a id="record-111"></a>`reports/investigations/mm-quote-recovery-evidence.json` | [history-20261107-investigations.zip](history-20261107-investigations.zip) | 0 |
| <a id="record-112"></a>`reports/investigations/mm-quote-recovery.md` | [history-20261107-investigations.zip](history-20261107-investigations.zip) | 0 |
| <a id="record-113"></a>`reports/investigations/mm-quote-wait-diagnostics-evidence.json` | [history-20261107-investigations.zip](history-20261107-investigations.zip) | 0 |
| <a id="record-114"></a>`reports/investigations/mm-quote-wait-diagnostics.md` | [history-20261107-investigations.zip](history-20261107-investigations.zip) | 0 |
| <a id="record-115"></a>`reports/investigations/mm-random-handover-evidence.json` | [history-20261107-investigations.zip](history-20261107-investigations.zip) | 0 |
| <a id="record-116"></a>`reports/investigations/mm-random-handover.md` | [history-20261107-investigations.zip](history-20261107-investigations.zip) | 0 |
| <a id="record-117"></a>`reports/investigations/mm-reporting-fee-candidates.sql` | [history-20261107-investigations.zip](history-20261107-investigations.zip) | 0 |
| <a id="record-118"></a>`reports/investigations/mm-reporting-latency-evidence.json` | [history-20261107-investigations.zip](history-20261107-investigations.zip) | 0 |
| <a id="record-119"></a>`reports/investigations/mm-reporting-latency.md` | [history-20261107-investigations.zip](history-20261107-investigations.zip) | 0 |
| <a id="record-120"></a>`reports/investigations/mm-runtime-followup-code-review-evidence.json` | [history-20261107-investigations.zip](history-20261107-investigations.zip) | 0 |
| <a id="record-121"></a>`reports/investigations/mm-runtime-followup-code-review.md` | [history-20261107-investigations.zip](history-20261107-investigations.zip) | 0 |
| <a id="record-122"></a>`reports/investigations/mm-runtime-logic-audit-evidence.json` | [history-20261107-investigations.zip](history-20261107-investigations.zip) | 0 |
| <a id="record-123"></a>`reports/investigations/mm-runtime-logic-audit.md` | [history-20261107-investigations.zip](history-20261107-investigations.zip) | 0 |
| <a id="record-124"></a>`reports/investigations/mm-trading-continuity-evidence.json` | [history-20261107-investigations.zip](history-20261107-investigations.zip) | 0 |
| <a id="record-125"></a>`reports/investigations/mm-trading-continuity.md` | [history-20261107-investigations.zip](history-20261107-investigations.zip) | 0 |
| <a id="record-126"></a>`reports/performance/README.md` | [history-20261107-performance.zip](history-20261107-performance.zip) | 0 |
| <a id="record-127"></a>`reports/performance/admin-cleanup-polling-evidence.json` | [history-20261107-performance.zip](history-20261107-performance.zip) | 0 |
| <a id="record-128"></a>`reports/performance/admin-cleanup-polling.md` | [history-20261107-performance.zip](history-20261107-performance.zip) | 0 |
| <a id="record-129"></a>`reports/performance/admin-cleanup-state-evidence.json` | [history-20261107-performance.zip](history-20261107-performance.zip) | 0 |
| <a id="record-130"></a>`reports/performance/admin-cleanup-state-review.md` | [history-20261107-performance.zip](history-20261107-performance.zip) | 0 |
| <a id="record-131"></a>`reports/performance/life-mm-optimization-review.md` | [history-20261107-performance.zip](history-20261107-performance.zip) | 0 |
| <a id="record-132"></a>`reports/performance/life-mm-runtime-optimization.json` | [history-20261107-performance.zip](history-20261107-performance.zip) | 0 |
| <a id="record-133"></a>`reports/performance/life-mm-runtime-optimization.md` | [history-20261107-performance.zip](history-20261107-performance.zip) | 0 |
| <a id="record-134"></a>`reports/performance/life-mm-soak-retest-evidence.json` | [history-20261107-performance.zip](history-20261107-performance.zip) | 0 |
| <a id="record-135"></a>`reports/performance/life-mm-soak-retest.md` | [history-20261107-performance.zip](history-20261107-performance.zip) | 0 |
| <a id="record-136"></a>`reports/performance/mm-checkpoint-poll-evidence.json` | [history-20261107-performance.zip](history-20261107-performance.zip) | 0 |
| <a id="record-137"></a>`reports/performance/mm-checkpoint-poll-optimization.md` | [history-20261107-performance.zip](history-20261107-performance.zip) | 0 |
| <a id="record-138"></a>`reports/performance/mm-controller-persistence-evidence.json` | [history-20261107-performance.zip](history-20261107-performance.zip) | 0 |
| <a id="record-139"></a>`reports/performance/mm-controller-persistence-optimization.md` | [history-20261107-performance.zip](history-20261107-performance.zip) | 0 |
| <a id="record-140"></a>`reports/performance/mm-daily-conversion-followup-evidence.json` | [history-20261107-performance.zip](history-20261107-performance.zip) | 0 |
| <a id="record-141"></a>`reports/performance/mm-daily-conversion-followup.md` | [history-20261107-performance.zip](history-20261107-performance.zip) | 0 |
| <a id="record-142"></a>`reports/performance/mm-offline-history-soak-evidence.json` | [history-20261107-performance.zip](history-20261107-performance.zip) | 0 |
| <a id="record-143"></a>`reports/performance/mm-partial-quote-size-evidence.json` | [history-20261107-performance.zip](history-20261107-performance.zip) | 0 |
| <a id="record-144"></a>`reports/performance/mm-partial-quote-size.md` | [history-20261107-performance.zip](history-20261107-performance.zip) | 0 |
| <a id="record-145"></a>`reports/performance/mm-reporting-startup-evidence.json` | [history-20261107-performance.zip](history-20261107-performance.zip) | 0 |
| <a id="record-146"></a>`reports/performance/mm-reporting-startup-optimization.md` | [history-20261107-performance.zip](history-20261107-performance.zip) | 0 |
| <a id="record-147"></a>`reports/performance/runtime-flow-remediation-20261101.json` | [history-20261107-performance.zip](history-20261107-performance.zip) | 0 |
| <a id="record-148"></a>`reports/performance/runtime-flow-review-20261101.json` | [history-20261107-performance.zip](history-20261107-performance.zip) | 0 |
| <a id="record-149"></a>`reports/platform/README.md` | [history-20261107-platform.zip](history-20261107-platform.zip) | 0 |
| <a id="record-150"></a>`reports/platform/cloudrun-image-optimization.md` | [history-20261107-platform.zip](history-20261107-platform.zip) | 0 |
| <a id="record-151"></a>`reports/platform/cloudrun-logging-verification.json` | [history-20261107-platform.zip](history-20261107-platform.zip) | 0 |
| <a id="record-152"></a>`reports/platform/cloudrun-logging-verification.md` | [history-20261107-platform.zip](history-20261107-platform.zip) | 0 |
| <a id="record-153"></a>`reports/platform/postgres-decimal-precision.json` | [history-20261107-platform.zip](history-20261107-platform.zip) | 0 |
| <a id="record-154"></a>`reports/platform/postgres-performance-review.md` | [history-20261107-platform.zip](history-20261107-platform.zip) | 0 |
| <a id="record-155"></a>`reports/platform/postgres-verification.md` | [history-20261107-platform.zip](history-20261107-platform.zip) | 0 |
| <a id="record-156"></a>`reports/platform/submission-block-logging.md` | [history-20261107-platform.zip](history-20261107-platform.zip) | 0 |
| <a id="record-157"></a>`reports/release/README.md` | [history-20261107-release.zip](history-20261107-release.zip) | 0 |
| <a id="record-158"></a>`reports/release/admin-access-credentials-acceptance.md` | [history-20261107-release.zip](history-20261107-release.zip) | 0 |
| <a id="record-159"></a>`reports/release/admin-access-credentials-evidence.json` | [history-20261107-release.zip](history-20261107-release.zip) | 0 |
| <a id="record-160"></a>`reports/release/admin-cleanup-fault-e2e.md` | [history-20261107-release.zip](history-20261107-release.zip) | 0 |
| <a id="record-161"></a>`reports/release/admin-cleanup-fault-evidence.json` | [history-20261107-release.zip](history-20261107-release.zip) | 0 |
| <a id="record-162"></a>`reports/release/admin-cleanup-partial-crash-e2e.md` | [history-20261107-release.zip](history-20261107-release.zip) | 0 |
| <a id="record-163"></a>`reports/release/admin-cleanup-partial-crash-evidence.json` | [history-20261107-release.zip](history-20261107-release.zip) | 0 |
| <a id="record-164"></a>`reports/release/admin-managed-environment-e2e.md` | [history-20261107-release.zip](history-20261107-release.zip) | 0 |
| <a id="record-165"></a>`reports/release/admin-managed-environment-evidence.json` | [history-20261107-release.zip](history-20261107-release.zip) | 0 |
| <a id="record-166"></a>`reports/release/admin-managed-lifecycle-e2e.md` | [history-20261107-release.zip](history-20261107-release.zip) | 0 |
| <a id="record-167"></a>`reports/release/admin-managed-lifecycle-evidence.json` | [history-20261107-release.zip](history-20261107-release.zip) | 0 |
| <a id="record-168"></a>`reports/release/e526-release-evidence.json` | [history-20261107-release.zip](history-20261107-release.zip) | 0 |
| <a id="record-169"></a>`reports/release/e526-release.md` | [history-20261107-release.zip](history-20261107-release.zip) | 0 |
| <a id="record-170"></a>`reports/release/mm-checkpoint-final-release-evidence.json` | [history-20261107-release.zip](history-20261107-release.zip) | 0 |
| <a id="record-171"></a>`reports/release/mm-checkpoint-final-release.md` | [history-20261107-release.zip](history-20261107-release.zip) | 0 |
| <a id="record-172"></a>`reports/release/mm-checkpoint-poll-load-evidence.json` | [history-20261107-release.zip](history-20261107-release.zip) | 0 |
| <a id="record-173"></a>`reports/release/mm-checkpoint-poll-load.md` | [history-20261107-release.zip](history-20261107-release.zip) | 0 |
| <a id="record-174"></a>`reports/release/mm-controller-persistence-release-evidence.json` | [history-20261107-release.zip](history-20261107-release.zip) | 0 |
| <a id="record-175"></a>`reports/release/mm-controller-persistence-release.md` | [history-20261107-release.zip](history-20261107-release.zip) | 0 |
| <a id="record-176"></a>`reports/release/mm-latent-fixes-load-evidence.json` | [history-20261107-release.zip](history-20261107-release.zip) | 0 |
| <a id="record-177"></a>`reports/release/mm-okx-expiry-load-evidence.json` | [history-20261107-release.zip](history-20261107-release.zip) | 0 |
| <a id="record-178"></a>`reports/release/mm-okx-expiry-load.md` | [history-20261107-release.zip](history-20261107-release.zip) | 0 |
| <a id="record-179"></a>`reports/release/mm-partial-quote-final-soak-evidence.json` | [history-20261107-release.zip](history-20261107-release.zip) | 0 |
| <a id="record-180"></a>`reports/release/mm-partial-quote-final-soak.md` | [history-20261107-release.zip](history-20261107-release.zip) | 0 |
| <a id="record-181"></a>`reports/release/mm-quote-recovery-release-evidence.json` | [history-20261107-release.zip](history-20261107-release.zip) | 0 |
| <a id="record-182"></a>`reports/release/mm-quote-recovery-release.md` | [history-20261107-release.zip](history-20261107-release.zip) | 0 |
| <a id="record-183"></a>`reports/release/mm-release-closeout-evidence.json` | [history-20261107-release.zip](history-20261107-release.zip) | 0 |
| <a id="record-184"></a>`reports/release/mm-release-closeout.md` | [history-20261107-release.zip](history-20261107-release.zip) | 0 |
| <a id="record-185"></a>`reports/release/mm-runtime-logic-release-evidence.json` | [history-20261107-release.zip](history-20261107-release.zip) | 0 |
| <a id="record-186"></a>`reports/release/mm-runtime-logic-release.md` | [history-20261107-release.zip](history-20261107-release.zip) | 0 |
| <a id="record-187"></a>`reports/release/mm-service-supervision-evidence.json` | [history-20261107-release.zip](history-20261107-release.zip) | 0 |
| <a id="record-188"></a>`reports/release/mm-service-supervision.md` | [history-20261107-release.zip](history-20261107-release.zip) | 0 |
| <a id="record-189"></a>`reports/release/mm-submission-log-release-evidence.json` | [history-20261107-release.zip](history-20261107-release.zip) | 0 |
| <a id="record-190"></a>`reports/release/mm-submission-log-release.md` | [history-20261107-release.zip](history-20261107-release.zip) | 0 |
| <a id="record-191"></a>`reports/release/probe-release-20261030-evidence.json` | [history-20261107-release.zip](history-20261107-release.zip) | 0 |
| <a id="record-192"></a>`reports/release/probe-release-20261030.md` | [history-20261107-release.zip](history-20261107-release.zip) | 0 |
