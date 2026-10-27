# Pre-listing MM economic stress replay

Purpose: compare the existing LIFE controller's configurations under explicit,
synthetic market assumptions before LIFE has a live order book. Results are
conditional stress outcomes, not calibrated backtests or profit forecasts.

- [x] E0: Red/Green coverage for conservative fills, latency/cancel races,
  shared flow consumption, exact cash/fee/funding accounting and hold baseline.
- [ ] E1: Replay the real LIFE controller, spread/inventory/quote policies and
  upstream position accounting with local providers and no exchange/DB access.
- [ ] E2: Paired deterministic scenarios for empty/no-flow, sideways, sustained
  buys, sustained sells, independent-reference divergence and gaps/slow cancels.
- [ ] E3: Compare fixed, fee/volatility and fee/volatility/inventory variants on
  spot, perpetual and both with the same seed, wallet and scenario within a pair.
- [ ] E4: Provide YAML assumptions, CLI/Make targets, bounded/censored markouts,
  liquidity denominators, equity/drawdown, costs, hold-relative PnL and raw ledgers.
- [ ] E5: Run native and packaged-runtime verification, retain provenance and
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
