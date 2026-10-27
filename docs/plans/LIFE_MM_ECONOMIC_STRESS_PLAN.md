# Pre-listing MM economic stress replay

Purpose: compare the existing LIFE controller's configurations under explicit,
synthetic market assumptions before LIFE has a live order book. Results are
conditional stress outcomes, not calibrated backtests or profit forecasts.

- [x] E0: Red/Green coverage for conservative fills, latency/cancel races,
  shared flow consumption, exact cash/fee/funding accounting and hold baseline.
- [x] E1: Replay the real LIFE controller, spread/inventory/quote policies and
  upstream position accounting with local providers and no exchange/DB access.
- [x] E2: Paired deterministic scenarios for empty/no-flow, sideways, sustained
  buys, sustained sells, independent-reference divergence and gaps/slow cancels.
- [x] E3: Compare fixed, fee/volatility and fee/volatility/inventory variants on
  spot, perpetual and both with the same seed, wallet and scenario within a pair.
- [x] E4: Provide YAML assumptions, CLI/Make targets, bounded/censored markouts,
  liquidity denominators, equity/drawdown, costs, hold-relative PnL and raw ledgers.
- [x] E5: Run native and packaged-runtime verification, retain provenance and
  publish a report with limitations, failures and conditional results.

Reuse `LifeMMController`, `MMProfilesConfig`, `SpreadPolicy`, `QuoteGuard`,
`ListingBootstrap`, `SpotOutflowProjection`, `BacktestPositionHold` and the existing
independent recorder-ledger validation. The upstream candle-only executor
simulator and load driver's scripted fills do not model queue position or
independent aggressor flow; the new test-only adapter supplies those assumptions.
Do not change production trading, introduce another service or call OKX.

The replay is a controller-level experiment. It does not replace real connector,
executor, ownership, migration or recorder E2E tests. Those retain their existing
image evidence. Random profile optimization, fitted LIFE correlations, real
queue calibration, market impact estimation and exact live account accounting
are outside this first experiment. No default production risk setting changes.

Completed 2026-10-27: all 648 declared cases pass replay/accounting integrity on
native and packaged Linux. Compared outcome fields agree for every case. This
does not certify profitability or production acceptance. 17 economic tests,
1613 native regressions/11 subtests, runtime smoke, Swagger/hooks and targeted
real-PG V2 perpetual E2E pass. Initial failed matrix/setup/export attempts remain
documented; rolling outflow overshoots and margin concerns remain visible.
See [results](../../reports/life-mm-economic-stress.md) and
[evidence](../../reports/life-mm-economic-stress-evidence.json).
