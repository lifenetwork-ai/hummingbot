# LIFE O.7.7 — Full offline acceptance

Date: 2026-10-10. Verdict: **Full Offline Complete for the declared MVP scope**. Production permission remains **false**. Demo, real-account qualification and economic viability are not certified.

## Included scope and exclusions

The included offline contracts are LIFE-USDT spot, one optional external benchmark, linear LIFE-USDT-SWAP, one shared USDT capital pool, bounded hedge execution, dynamic carry monitoring, joint recovery, and bounded inventory execution with MM return. Supported derivative scope is account mode 2, cross margin, ONEWAY/HEDGE. Installation and action proposal are explicit APIs.

The [full manifest](evidence/life_o7_acceptance_manifest.json) retains the O.6 test scopes, pinned coverage bases and existing evidence selectors, and adds mandatory extension suites. It maps all 39 acceptance cases and all 118 unchecked detailed P requirements, including anonymous children. Every declared offline remainder is discharged; remaining requirements are assigned to D/R/C with owners and reasons.

- **37 A-cases:** applicable normalized offline portions verified. This does not pass their actual exchange, calibration or release portions.
- **A13:** actual exchange cancellation timer remains D/R. A fake timer cannot establish exchange behavior.
- **A17:** benchmark baskets are outside the explicitly single-source MVP. Configuration rejection is verified; basket reweight/loss behavior is not implemented or certified. A separate feature/research decision is required.
- Cross-asset hedges, portfolio margin and multi-host sending are also excluded. No new production scheduler, CLI or authenticated source assembly is claimed.

Detailed unchecked P roots combine offline and later-stage obligations. Historical O.7 wording remains fingerprinted in the plan and older O.6 map for traceability; the full manifest supersedes that offline remainder. Earlier checkpoint reports remain historical.

## Combined replay and financial identity

`o7_scenarios.py` installs the actual coordinating controller, V2 authorization filter, `OrderExecutor`, protected spot/SWAP senders, shared capital, hedge, carry, inventory and joint recovery. The fixture replaces the production release switch locally, injects upstream policy authorization/risk-epoch callbacks, and supplies synthetic normalized observations. Mandatory shared-capital, carry, hedge, runtime, inventory, session, quote, runner and final-send checks use their actual implementations; spot fill/terminal/account callbacks use the actual reconcilers. Deployed policy/identity adapter assembly remains D/R.

Two seeded runs (7, 19) reproduce the [semantic replay bundle](evidence/life_o7_combined_replay.json). Random intent/wire identities are omitted from this comparison; executed product/direction/quantity/price/fee/PnL, modes, signed funding and final joint metrics must match. Trade timestamps advance between children so weighted entry replay has an explicit chronology. Funding input ordering varies by seed.

1. An MM BUY of 1 LIFE partially fills 0.5 at 0.99, with a 0.001 USDT fee.
2. A 0.5 LIFE SWAP CLOSE hedge at 1.001 realizes 0.0005 USDT, with a 0.001 fee. Missing hedge history blocks inventory entry and retains the capital claim. The old final-send callback is revoked. Complete history and explicit operator rearm are required.
3. Funding settles a 0.01 debit and a 0.004 receipt. The gross funding debit budget remains 0.01; net funding is −0.006.
4. A bounded inventory SELL of 0.5 at 0.99 incurs a 0.001 fee. Complete account/fill proof permits return to MM; the fixed execution benchmark is retained.
5. A 0.5 LIFE SWAP OPEN rebalance at 0.999 incurs a 0.001 fee. Remaining UPL at mark 1 is 0.0005. All capital claims settle only after joint proof.

Final physical spot holdings are 10 LIFE and 9.9905 USDT; SWAP long is 1 LIFE. Joint NAV is **19.9910 USDT**, combined fees **0.004**, settled funding **−0.006**, realized SWAP PnL **0.0005**, and remaining UPL **0.0005**. The 100 USDT synthetic collateral observation is reported separately and is never added again to NAV. Opening NAV is 20; net result is −0.0090. Drawdown uses the retained intermediate high-water of 20.004: `(20.004 − 19.9910) / 20.004 × 10000`, approximately 6.499 bps. Repeating reconciliation changes neither capital nor gross budget/cost commitments.

Eight additional combined cases revoke spot and SWAP final callbacks under funding-scope failure, basis shock, missing history and collector disconnection. They retain pending holds and issue no additional sends. Restored facts allow settlement; failed joint proof additionally requires operator rearm. Five provenance cases reject tampered SWAP config, foreign wire identity, absent joint authority, unknown intent and a fake executor during spot slot scanning.

## TDD defects and reporting

A failing WAL regression reproduced unequal in-memory/reopened record tuples for nonalphabetical intent insertion. Canonical intent ordering now makes durable comparison stable; mismatch and uncertain-storage checks remain active. The combined replay also failed when the spot planner rejected verified SWAP children as foreign spot orders. It now excludes only protected, account-bound SWAP children with durable sender/WAL/capital provenance from spot occupancy; unknown or tampered children still block proposals.

Telemetry and the full acceptance entry point began with missing-module Red checks. The extension collector reports qualified joint NAV, drawdown, signed fees/funding, SWAP positions and realized/unrealized PnL, maintenance requirement and separate collateral. Hedge cost is explicitly a **gross campaign cost hold**, not realized PnL. Fill-time edge retains its spot-only label. Joint executable liquidation value stays null without qualified joint exit evidence. Stale/missing/mismatched authority, attribution or storage returns null joint metrics, never a fallback spot NAV. The bounded recorder roundtrip is exercised by the combined replay. Historical spot payload shape is preserved.

## Reproduction and regression gate

From the repository root, in the built Hummingbot conda environment:

```sh
conda run -n hummingbot --no-capture-output python -m \
  test.hummingbot.strategy_v2.life_liquidity.o7_acceptance \
  --output-dir /tmp/life-o77-final
```

The audit validates the full manifest, runs the declared LIFE, V2 strategy-base, OKX spot/perpetual, executor-orchestrator and MarketsRecorder scope with coverage, and refuses missing, skipped, failed or incomplete mapped variants. Mandatory extension suites cannot be removed; existing spot selectors cannot be dropped. Both pinned feature/recent changed-line coverage gates require at least 80%, and every changed production Python file must be measured. The summary records cases, extension node counts/hashes, environment versions, plan/manifest/audit/entry-point hashes, coverage and source HEAD/worktree status.

The [regression archive](evidence/life_o7_full_offline_regression.json) contains the measured results, focused node outcomes and source hashes. Repository pre-commit hooks and `git diff --check` are part of the final checkpoint. Historical O.6 certifies only its original spot scope; this entry point certifies the declared full offline scope.

## Remaining work: D, R and C

Authenticated collectors still need actual UID, endpoint, mode, terms, contract, fill/fee/bill, account cut and pagination qualification. Source labels and completeness flags in offline fixtures are injected contracts, not authentication. Deployed runner/database ownership, logging/flush, actual scheduling/latency and exchange close/post-only enforcement require demo and real-account proof.

All numeric prices, volume, depth, fees, funding, collateral, latency, deadlines and risk thresholds here are synthetic. Scheduled fills are inputs, not evidence of external demand or profitable execution. Existing O.5 queue/latency cases remain in the full gate. Extension fresh-object checkpoint tests do not establish full process-kill or power-loss durability; the host-local account authority and dedicated account remain deployment assumptions. No runtime halt automatically liquidates or hands off a position.

Next is **D — Demo Proven**: assemble qualified adapters and runner explicitly, select isolated demo endpoints/credentials, then retain real session/pause/expiry/restart/disconnect/cancel/fill/account-reconciliation evidence. If LIFE is unavailable in demo, use a supported connector instrument plus this frozen LIFE replay and document both limits. Real capital, budgets, KPI calibration, economic evaluation and release eligibility remain R/C/P9. Unresolved live inputs cannot become implicit defaults.
