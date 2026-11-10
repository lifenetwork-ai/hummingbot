# LIFE / OKX delivery status

Updated 2026-11-10. This concise status replaces the chronological journal; the
[full journal](../README.md#historical-records) (Git: `reports/implementation/LIFE_OKX_PROGRESS.md`) retains every original phase, attempt and result.
Feature/configuration references are in [MM controls](../../docs/life-mm-controls.md),
[FE integration](../../docs/fe-integration.md), [PostgreSQL](../../docs/postgres-runtime.md)
and [credential operations](../../docs/exchange-credentials.md).

Follow-up: Cloud Build/audit attributes revision74→75 to the delayed automatic
deployment of older commit `f7da0d1e6`; no new runtime defect is reproduced.
Overlapping-owner E2E and91 native controls pass. Merged CI artifacts require
separate verification; they do not inherit image4198 release gates. See [closeout investigation](../release/demo-release-closeout.md#later-revision-transition-under-investigation).

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

Latest [Demo/release closeout](../release/demo-release-closeout.md): source
`f53885fda`, regular image `4198f2fa…`. Canonical native3,085 +16 subtests,
full disposable PostgreSQL605 and all30 packaged E2E groups pass, including the
technical distinct-image upgrade/takeover/rollback scenario. Full unchanged
load/Random soak3600/six faults/retention pass. Main p99
96.87/96.46/103.11/283.41ms;4,885 simulated fills and growth18.02MiB/64MiB.
Six healthy probes self-recover without healthy child replacement.
Historical rollout attribution is closed; latest merged CI artifact verification remains open.

Four TDD-confirmed defects are fixed: retained orphan PostgreSQL ownership,
flat perpetual hedge-leg identity overwrite, provable spot BTC-fee verification,
and fresh cleanup close51603 visibility. Native/database/Linux-image Red/Green
and actual corrected-image cleanup/reload evidence retain separate scopes.
The original infrastructure trigger of the first I/O delay is still unproved.

Actual revision73:1,780 samples across30 minutes, stable owner/no HALT/unknown;
conditional two-sided spot99.7700%, perpetual99.7133%, eligible/all97.70%/97.98%.
Four actual filled post-only orders prove BUY/SELL perpetual handoff and fresh
account observations. Temporary sizing/spreads are declared; risk caps/guards
are preserved. Stopped384 actual fills/original fees/positions reconcile.
Automatic cancel/close/remove/Set and cold reload preserve both flat hedge rows.

The regular catalogue is restored on Cloud revision74,2CPU/1GiB,min1/max1,
CPU always allocated. A separate audited Start53 leaves fixed follow_market/both
running Until stopped. These technical calls use actual Basic bootstrap identity;
reviewed incident maintenance records its own maintenance actor. No Google
identity is impersonated. Ordinary wallet inventory can legitimately limit BUY.

Real Google acceptance and earlier reviewed recovery remain qualified on their
original revisions; [Demo index](../demo/README.md) links those records.
The preceding full offline `b090430c…` result remains in
[its own image report](../release/mm-runtime-followup-load.md), without transferring
that pass to the latest source. The [remaining-work register](../../docs/plans/README.md)
keeps real GCP KMS/IAM, actual FE, live cutover and historical attribution open.
