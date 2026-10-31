# Pre-listing MM economic stress — 2026-10-27

The economic replay is implemented and verified. **It is a synthetic stress
experiment, not calibrated LIFE backtesting, an expected-return forecast or
production approval.** Production strategy code and deployed settings are unchanged.

Run `make test-mm-economics` for the declared matrix, or
`make test-mm-economics-probe` for a diagnostic subset. YAML assumptions are in
[`economics.yaml`](../../test/e2e/offline/economics.yaml). See the
[guide](../../docs/life-mm-economic-stress.md) and
[portable evidence](life-mm-economic-stress-evidence.json).

## What was measured

Each platform completed all **648 combinations**: six scenarios, two seeds,
three profiles, spot/perpetual/both, three policy variants and two execution
assumption bundles. The virtual session lasts 300 seconds plus a common finite
cancellation tail. These combinations reuse **twelve scenario/seed groups and
eleven distinct tapes** (the empty/no-flow seeds have identical data);
they are correlated counterfactuals, not 648 independent market observations.

Scenarios cover empty/no-flow, sideways, sustained taker buying, sustained taker
selling, LIFE moving against its references and price gaps with slower cancels.
Every policy, profile, mode and sensitivity for a scenario/seed receives the
same prices, references and independent aggressor flow.

The adapter reuses the real LIFE controller, deployed catalogue, maker-entry
convention, fee/NATR/inventory/guard/bootstrap helpers, rolling outflow projection,
upstream position holds and independent simulator accounting. Added test code
allocates aggressor volume through displayed liquidity and conservative queue
assumptions, handles arrival/cancel deadlines and records metrics. It does not
replace the production trading engine or run its full executor/owner lifecycle.

## Findings under these assumptions

No configuration establishes a globally cheapest, effective or profitable policy.
Lower total fees can accompany fewer fills and less qualifying liquidity. The
following is the **both-market / assumed-base** slice: each row contains the same
36 counterfactual sessions, mixing six regimes, three profiles and two seeds.
Fees are totals across those independent session simulations, not one account's
cost or a production fee estimate.

| Variant | Fills | Assumed fees, USDT | Median excess vs hold, USDT | Median simultaneous qualified time | Negative initial-margin headroom cases |
|---|---:|---:|---:|---:|---:|
| Fixed | 4802 | 11.487383 | -13.226219 | 4.17% | 18/36 |
| Fee floor + volatility | 3339 | 7.605365 | -1.984986 | 3.50% | 2/36 |
| Fee floor + volatility + spot inventory skew | 3083 | 5.783601 | -1.921656 | 1.67% | 0/36 |

Qualified time requires both market sides around the independent mark, a maximum
150 bps spread and at least 5 assumed USDT per side inside the half-band. The
denominator includes all 300 session seconds, including waits, depleted quotes
and poor/stale model placement. These deliberately stressful settings do not
meet sustained liquidity objectives; the table must not be used to nominate a
production winner. Full spot/perpetual/both and stressed-bundle groups remain in
the evidence JSON and each run's generated `review.md`.

Across the entire matrix, **148 cases** have positive hold-relative marked
excess after assumed costs, and **108 cases** have no fills. These are descriptive
counts under artificial paths, not a profitability probability. Empty/no-flow
cases retain zero trading PnL and fees, finite bootstrap availability and a
negative assumed operating-cost contribution. In perpetual-only mode, the two
fee/volatility variants agree because inventory skew is spot-only.

Two risk findings remain explicit:

- **7 cases** observe rolling net outflow plus reservations above the cap after
  earlier SELL credits expire. Every admitted BUY still satisfies the existing
  cap inequality. This is an admission policy, not a perpetual hard bound on the
  observed rolling measurement or account loss.
- **58 cases** develop negative fixed initial-margin headroom. The model has no
  maintenance-margin/liquidation process; these are capacity warnings. Continued
  marked PnL beyond that point is not a prediction of executable real positions.

Account PnL includes initial inventory repricing. Hold-relative excess removes
that passive initial-wallet return and includes actual simulated fees/funding
once. Remaining spot inventory and HEDGE positions are marked at scenario prices;
there is no assumed liquidation, exit slippage or terminal exit fee. Fees are
USDT-denominated assumptions, funding uses a synthetic accelerated schedule,
and operating cost is illustrative rather than a Cloud Run price estimate.

## Verification and retained failures

- Native and packaged Linux each complete **648/648** cases. Compared metrics,
  liquidity, per-market contributions, markouts, reasons, tape hashes and effective
  configuration hashes agree exactly for all cases despite recorded library
  version differences. This is reproducibility evidence, not market calibration.
- **17 economic tests**, **1613 native regression tests plus 11 subtests**,
  Swagger and applicable hooks pass. A real PostgreSQL V2-perpetual connector/
  executor/hold/restart E2E also passes on the experiment image.
- Image `54e2a1408e1e` is Linux amd64, 1366591963 bytes. Its ABI/JIT/TLS/logging
  smoke passes; 61 production/runtime paths match unchanged source. Production
  modules came from the image; the test adapter and current entry driver were
  mounted explicitly. Source hashes and dependency versions are retained.
- The original simulator's weighted-entry and short-PnL-sign defects were fixed
  with Red/Green coverage before using it for variable-price economic accounting.
- The first native matrix remains **failed after 432 cases**. Its continuous
  rolling-ceiling assertion was stronger than the implemented admission contract.
  A targeted reproduction shows virtual second 66 moving net outflow from
  57.532131935910499 to 60.317639639910499 solely as prior SELL credit expires.
  The corrected experiment checks every BUY admission and reports overshoots;
  production policy, fees, price paths and budgets were not changed to pass.
- An initial packaged setup could not resolve the local tag; the confirmed
  immutable image ID was used. A subsequent complete Linux replay lost exportable
  output when its stopped container's tmpfs disappeared. The unchanged full
  matrix was repeated with a persisted host evidence mount; all 648 case files
  are retained. These setup/export failures are not reported as passing harness runs.

Raw native results: `build/mm-economics/20261027-192056-985800/`.
Persisted image results: `build/mm-economics-verification/runtime-output/results/`.
The failed native run is under `build/mm-economics/20261027-190751-97ae3d/`;
logs, dependency versions and source proofs are under
`build/mm-economics-verification/`. Test containers are removed after verification.

No live OKX account, Cloud Run deployment, new load/soak, full release image matrix
or upgrade/rollback gate is certified here. Real queue behavior, impact, account
fees, liquidation and LIFE order flow require actual instrument data. Conservatism
in one execution assumption does not make the entire profit model a worst-case bound.
