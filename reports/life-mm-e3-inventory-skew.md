# E3: reuse existing PMM spot inventory skew

Date: 2026-10-27 (Asia/Ho_Chi_Minh). Baseline: `3b39d7db7`.
Scope: E3 of the [enhancements plan](../docs/plans/LIFE_OKX_MM_ENHANCEMENTS_TDD_PLAN.md).
Status: E3 complete and locally verified.

## Reuse and implementation scope

| Concern | Existing implementation reused |
|---|---|
| Inventory interpolation | Original compiled `calculate_bid_ask_ratios_from_base_asset_ratio`; no new interpolation formula |
| Quote prices/allocations | LIFE/PMM Simple level conversion and reference anchors |
| Order execution | Existing PositionExecutor, post-only orders, normal refresh and position-preserving stop |
| Cash and freshness | Existing OKX `account_balance_observations`, original cash fields and normal balance polling |
| Market valuation | Existing bounded known-order-filtered `observe_market` |
| Risk/funding | Existing account exposure, available balances, spot outflow preview/final gates and owner/session authority |
| UI | Existing authenticated market readiness snapshot and generated handler schemas |

The addition is a small validated adapter and optional sizing integration in the
existing controller. It adds no service, client, background task, database schema,
ledger, cache or environment variable. It does not import the generic PMM V1
controller/OrderExecutor or copy its single-base-order range assumption.
Original legacy/generic calculation files are unchanged.

The spot-only YAML policy is disabled by default. Base and USDT cash from the
spot connector determine account inventory at an outside market mid. Perpetual
equity/shared collateral and model/own-quote anchors cannot inflate that ratio.
Actual unskewed base level quantities define the range. Only the existing legacy
ratio interpolation uses doubles; final allocations, normalization and cap checks
remain Decimal. Inputs that cannot be represented safely are rejected by the
optional adapter rather than silently creating a ratio.

All configured level notionals are scaled down together when necessary. Existing
connector rounding/minimums and funding/exposure/outflow checks apply afterward.
Active/shutting-down executors retain full original proposal allocation, including
partial fills; rejected allocation is not redistributed. Prices, reference/session
anchors, order type and perpetual sizing remain on their existing paths.

Missing optional cash fields, outside valuation or failed interpolation selects
neutral `(1,1)` and `inventory_skew_unavailable`, recovering on the next update.
Known stale/invalid account timestamps and corrupt mandatory engine balances
block readiness and recover automatically with fresh data. Freshness follows two
normal long-poll periods (currently 240 seconds), avoiding an availability gap on
quiet accounts normally polled every 120 seconds. No additional poll is scheduled.
See the [operational guide](../docs/life-mm-controls.md#inventory-skew).

## TDD and limits

Initial contracts failed before the adapter existed. Additional Red/Green cases
caught two relevant integration errors: known stale account balances must not be
misclassified as optional interpolation failure, and an own-only raw mid must not
be treated as independent inventory valuation. Their fixes reuse existing
observation/readiness paths. Final review also reproduced malformed optional cash
snapshot shapes incorrectly faulting controller readiness. Three Red/Green cases
now prove malformed/empty optional snapshots select neutral sizing and recover
on the same controller, while known stale account timestamps remain mandatory. A test fixture initially omitted active-executor
metadata and was corrected. No test or latency threshold was relaxed.

Tests compare the adapter directly with original upstream interpolation across
neutral, underweight, overweight and capped-range cases, and run the original
legacy calculator suite. Controller cases cover shared-equity differences, missing
cash/zero portfolio/helper errors, stale/future account data, same-instance recovery,
weighted allocation normalization, quantization/minimums, partial active allocation,
outflow rejection without redistribution and both-market readiness. Disabled and
perpetual paths retain their existing behavior. FE status serializes UTC times and
Decimal strings through the existing schema.

This remains a proposal-sizing signal, not an isolated campaign wallet or a
promise about actual execution proceeds/account equity. Public/private observations
are not atomic. Existing maker-price behavior remains authoritative; no extra
market orders or wash/self-match behavior is added. All-feature final image/
performance gates remain E6; this phase does not certify profitability.

## Verification

Final native regression passed **1530 tests and eleven subtests** (158/11/979/382),
including the original legacy calculator suite. Focused verification passed 58
tests with RuntimeWarning as errors. The final full PostgreSQL suite passed all
171 cases. Scoped hooks and generated Swagger checks pass.

Final Linux amd64 image `sha256:1dd6485eee231effacce0d406d880bd03f0269c1518ffbf59fce8d14362d8312` is 1364727376 bytes.
All thirty recorded runtime/config/contract paths match packaged bytes. Original
PMM `.pyx`/`.pxd` and generic PMM V1 sources match baseline byte-for-byte. The full
standard Cloud Run Dockerfile built the final artifact. Runtime ABI/JIT/TLS/SDK/
SQL/size smoke and enabled inventory E2E passed on this image:
`build/e2e-offline/20261027-001442-561cf1/result.json`. Startup/restart were 8.260s/8.831s; first shutdown was
0.950s.

On a 100-USDT proposal budget, initial underweight inventory produced BUY/SELL
notionals of about **75.74/24.26 USDT**. Two normal simulated BUY fills moved
inventory overweight, and existing refresh produced **37.31/62.69 USDT**. All
orders remained post-only, total proposed allocation stayed within 100 USDT,
spot/both modes and restart passed, and perpetual sizing stayed on its normal
path. Exactly two exchange/recorder fills were retained despite duplicate events;
no extra market orders/rebalance or live exchange operation was introduced.

The final sixty-second healthy diagnostic combined bootstrap/guards, drawdown,
spot outflow and spot skew on 1 CPU/2 GiB with 100000 historical orders/fills,
about 100 market updates/s, one fill/s and 20 UI reads/s. Unchanged budgets passed:
**p95 49.36ms / p99 225.97ms**, 1200 HTTP 200 reads, zero unexpected errors,
sixty unique exchange/recorder fills and matching positions. Peak RSS was
326864896 bytes. Artifact: `build/mm-load/20261027-001719-d1daa8/result.json`. The healthy fixture
uses explicit target `.01` / range `10` to retain both funded sides; the dedicated
E2E separately demonstrates stronger skew with target `.015` / range `1`. These
are test settings, not production recommendations. No gate was relaxed or guard
disabled to obtain this result.

The initial healthy image/probe also passed before the final optional snapshot
shape check. The final image repeats both enabled gates, and its native/PG suites
cover the final source. These local emulated amd64 measurements do not establish
Cloud Run latency guarantees or worst-case all-feature throughput.

No additional cache/index is justified by this change. Interpolation runs once
per controller reference update, and repeated proposal reads reuse that update's
small factor snapshot. The addition uses existing books/account/executor reports,
not history queries or per-level exchange calls. Normal owner/recorder SQL remains
required. No new table, ledger, service or poll was added.

See [sanitized JSON evidence](life-mm-e3-inventory-skew.json). Raw output remains
under ignored `build/e3/`. E4–E6, the final all-feature image matrix and long load/
soak gates remain open. No external OKX account or Cloud Run deployment was used.
