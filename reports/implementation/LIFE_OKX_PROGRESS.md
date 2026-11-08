# LIFE / OKX delivery status

Updated 2026-11-09. This concise status replaces the chronological journal; the
[full journal](../README.md#historical-records) (Git: `reports/implementation/LIFE_OKX_PROGRESS.md`) retains every original phase, attempt and result.
Feature/configuration references are in [MM controls](../../docs/life-mm-controls.md),
[FE integration](../../docs/fe-integration.md), [PostgreSQL](../../docs/postgres-runtime.md)
and [credential operations](../../docs/exchange-credentials.md).

## Delivered

- Hummingbot reuse: native connectors, V2 controllers/executors and recorder.
- Three MM scenarios, weighted market basket/token references, YAML-owned profiles,
  fixed/Random sessions including until-stopped, spot/perpetual/both.
- Authenticated operator API, English status/diagnostics, immutable configuration
  ownership, history/statistics/audit and development-only Swagger.
- PostgreSQL persistence, UTC/Decimal precision, idempotent startup migrations,
  durable-before-send, ownership/reconciliation/recovery and background funding.
- Risk/availability, quote guards, drawdown/outflow, listing bootstrap, inventory
  skew, fee/NATR/volatility controls and bounded resource handling.
- Optional Basic bootstrap, Google identities/roles, local/GCP encryption provider
  adapters and recoverable cancel/close/remove credentials operations.

## Current verification

Image **`96368970…`**, build `8544488ef`, now packages the recorder/quote/
anchor hot-path changes. **1,010 native passes + 9 subtests** and paired component
measurements retain exact-value and failure checks; **1,225 packaged hashes**
match the build tree, and **1,254 measured source/fixture hashes** remain frozen.

The new full load gate **fails burst main/auxiliary p99** at **1,084.01 /
1,151.07 ms** against 1,000 ms. Warmup, mixed, sixty-minute Random soak and all
five fault assertions pass. Soak p99 **547.30 ms**, 4,884 simulated fills/fees/
positions reconcile, retained growth **26.09 MiB / 64 MiB**. Healthy reads:
97,800 main / 24,450 auxiliary HTTP200, zero unexpected errors. Twelve healthy
probes recover in **1.031–1.248 s** with no healthy child replacement. These
observations do not prove continuous quotes or an exclusive latency cause.

First E2E: six groups pass before an older seed fixture omits dummy live perpetual
credentials. TDD proves live fail/demo pass; fixture `556d30bb7` supplies the same
fields as other both-market fixtures, with 14 related native tests passing.
Corrected `all` fails shutdown after fourteen groups; thirteen remaining/targeted
groups subsequently pass on the unchanged image. Upgrade remains unrun
without an actual prior image. The fixture fix does not weaken credential gates.

Power changes from battery to AC during soak; no power settings are changed.
Eight ERROR records belong to injected DB faults only. Existing unrelated images
and volume are retained; no connected Demo/Google/GCP or deployment occurs.

The previous accepted `a6fe88c0…` report/evidence remains in Git `8544488ef` at the
current release paths, including source, quantile fixture and historical limits.
The previous daemon's rollback images/containers no longer exist here; historical
verification is retained in Git, not silently relabeled as current-image evidence.

- [Current image report](../release/mm-runtime-followup-load.md)
- [Latest source remediation](MM_RUNTIME_FOLLOWUP.md)
- [Remaining acceptance/policy/evidence](../../docs/plans/README.md)

No actual-Demo cutover, real Google/GCP or production deployment is inherited from
these local checks. Historical pass/fail scopes remain in Git history.

Shutdown follow-up: corrected `all` E2E passes fourteen groups before a nonzero
inventory-skew shutdown; all thirteen remaining/targeted groups pass, covering twenty-seven unique
groups across attempts. The failed `all` remains failed. A real-signal
TDD reproducer confirms an Event-lock hazard, fixed in source `1916e048e` (75 related
passes). This is a possible mechanism, not proof of the old exit's unique cause.
`96368970…` predates the fix; new-image qualification remains open. See the
[runtime checklist](../../docs/plans/MM_RUNTIME_FOLLOWUP_REMEDIATION_TDD_PLAN.md).
