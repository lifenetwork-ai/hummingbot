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

New managed-Demo candidate `4451974e…` passes native2,868 +16 subtests, PG557,
targeted packaged E2E and [connected recovery/Random/restart/cleanup](../demo/managed-local-connected-followup.md).
All original history is preserved; final bot stopped, no HALT/pending orders and
perpetual flat. A nonfatal cleanup reporting warning and the new candidate's
full release/soak remain separate follow-ups. The full-load numbers below retain
their preceding-image scope.

Image **`b090430c…`**, build `d0791e563`, packages the earlier recorder/quote/
anchor allocation changes and signal-lock fix `1916e048e`. Canonical native
**2,861 passes + 16 subtests**, paired Linux signal contracts and **27 available
E2E groups in one all execution** pass. All 1,225 packaged hashes match; all
1,254 measured source/fixture hashes remain unchanged.

The unchanged full gate **passes** all four windows, Random soak3600 and all five
fault assertions. Main p99 warmup/mixed/burst/soak **569.69/273.48/297.81/313.00 ms**;
burst auxiliary 133.70 ms. All 97,800 main/24,450 auxiliary healthy reads return
HTTP200 without unexpected stale responses. **4,885 simulated fills/fees/positions**
reconcile; retained growth **30.33 MiB/64 MiB**.

Three healthy engine probes recover in 1.116/1.083/1.057 s without replacement.
All eight ERROR records belong to injected DB faults. No baseline activity
overlay: continuous quoting and exclusive historical cause are not established.
The whole observed run is AC; warmup p99 and retained growth are higher than the
failed predecessor, so no uniform speedup or exclusive power effect is claimed.

The pre-existing Event-lock hazard is demonstrated by real-signal Red/Green and
old/new image behavior. The predecessor's observed shutdown cause remains
unproven. Failed `96368970…` reports remain in Git `d0791e563`; earlier accepted
`a6fe88c0…` records remain in `8544488ef`, at current release paths. Unrelated
container IDs/states, image IDs and volume are retained. No connected Demo,
Google/GCP or deployment occurs.

- [Current image report](../release/mm-runtime-followup-load.md)
- [Latest source remediation](MM_RUNTIME_FOLLOWUP.md)
- [Remaining acceptance/policy/evidence](../../docs/plans/README.md)

No actual-Demo cutover, real Google/GCP or production deployment is inherited from
these local checks. Historical pass/fail scopes remain in Git history.
