# O.7.2 — Shared Capital Authority

Date: 2026-10-10. **O.7.2 is complete offline for the scope below. O.7 remains open. Production trading remains disabled.**

## Scope and ownership

`SharedCapitalAuthority` supplies one durable, UID-bound allocation journal to both protected spot and SWAP senders. All eligible MM/hedge children consume this same pool; hedge generation and target-inventory coordination remain O.7.3. The controller installs only one authority and refuses installation over existing orders, spot reservations or capital claims. Adopting those requires O.7.5 recovery proof.

Supported policy: account mode **2**, **cross** margin, **USDT-valued eligible collateral**, LIFE-USDT spot and linear LIFE-USDT-SWAP, ONEWAY or HEDGE. Portfolio/multi-currency margin and implicit USD-to-USDT conversion are rejected. A provider must supply a conservative USDT collateral value; the `totalEq`/`adjEq` USD fields from the read-only account adapter cannot simply be relabeled as USDT.

The source contract is an explicitly supplied, fresh, account-wide snapshot with an increasing sequence, UID/modes, physical LIFE/USDT balances, distinct long/short exposure, eligible collateral, account initial/maintenance requirements, funding liability and distinct qualified mark/index prices. SWAP observations must match the same snapshot sequence and actual contract-derived positions. Spot observations must match the actual reservation ledger's physical balances. Exchange feed assembly/qualification remains O.7.4/O.7.5 and D/R.

Allocation uses `PolicyState` file locking, atomic replace/fsync and stale/uncertain-writer fencing. The contract assumes one configured journal for the pool and the existing dedicated-account host ownership rules. Different journals do not coordinate with each other; deployment must retain the existing account UID lock and canonical recovery directory. Cross-host risk allocation is outside this MVP.

## Financial contract

Every accepted claim binds intent ID, wire ID, account UID, session/epoch, config/risk versions, product, side, OPEN/CLOSE, LIFE quantity, USDT price and leverage. Repeating the exact claim is idempotent; changing identity, direction or quantity is denied. Authorization never allocates a missing claim.

- **Pending endpoints:** independently evaluate spot-buy/spot-sell, long-open/long-close and short-open/short-close outcomes. Gross exposure includes both HEDGE sides. Opposite pending orders never receive financing or guaranteed netting credit.
- **Physical holds:** spot purchases reserve principal; sales and closes cannot exceed actual holdings after other claims. Sale proceeds and a close's anticipated margin release receive no credit before settlement.
- **Pending identity:** a fully identical local/external intent and wire identity counts once. Duplicate external rows, changed quantities, conflicting IDs and duplicate wire IDs fail closed. Partial fills are not inferred from a smaller pending row.
- **Fees:** a nonnegative explicit conservative fee rate reserves fees for all pending claims; maker rebates do not finance orders.
- **Margin tiers:** apply the selected tier's rate to the entire gross perpetual notional, using the larger of tier initial rate and `1 / leverage`. Rates and caps must be finite, positive and ordered; uncovered notional is blocked. This is a conservative envelope, not an imitation of OKX portfolio-margin offsets.
- **Existing account requirements:** preserve the greater of observed account margin and modeled current LIFE margin, then add the nonnegative modeled increase to the stressed pending state. Existing account requirements are not added twice. Other observed account requirements cannot disappear when the LIFE model is smaller.
- **Stress:** stress notional upward, reserve actual mark/index divergence and additional adverse basis, evaluate directional loss at the worst net endpoint, and reserve nonnegative funding liability. A separately configured combined stress-loss cap applies even with abundant collateral.
- **One collateral pool:** initial and maintenance buffers each subtract spot purchase principal, fees, basis/directional stress, funding and the respective margin requirement from the same eligible account collateral. Two connector balance observations cannot be summed through this interface.

All limits, fee rates, stress amounts, tiers and freshness values in tests are explicitly synthetic. Live numerical calibration and source qualification remain R; no test value becomes a live default.

## Persistence and send ordering

SWAP allocation commits before its action/WAL is published. Spot allocation commits after its WAL/local reservation claims and before the protected gateway can enqueue a request. Financial refusal aborts the proven-unsent spot WAL/local reservation; an uncertain shared checkpoint retains its possible claim.

Both senders revalidate the same claim at final network authorization. Actual V2 filters and OrderExecutors reach actual OKX connector schedulers, `_create_order`, REST assistants and throttlers. A collateral change during throttling sends zero requests. Successful ACK remains unresolved in the capital journal.

There is deliberately **no automatic capital-release API** in this stage. ACK, timeout, missing feed, cancellation request, stale state and session expiry cannot release holds. Restoring the authority retains claims; controller installation with existing claims requires O.7.5 recovery. This conservative allocation stage does not yet recycle capital after fills or prove joint settlement.

## TDD and evidence

Red: the initial allocation suite failed at collection because `shared_capital` did not exist. Green tests cover competing spot/SWAP allocations, one-sided pending fills, HEDGE gross/tiers at zero delta, account-margin deduplication, basis/combined stress, close/sale bounds, external pending identities, policy/unit restrictions, stale or corrupt snapshots, restart and idempotency.

Thread and separate spawned-process contenders prove that two individually affordable claims cannot overdraw the same journal or overwrite each other. An injected directory-fsync failure retains a possibly committed claim and fences the writer. Restore rejects corrupted claim units, identity, sequence/snapshot pairing and policy.

Runner tests exercise allocations in both dispatch orders, account snapshot disagreement, collateral/basis/funding/freshness/storage revocation, and both real REST throttlers using fake HTTP sessions. Unrelated spot market/quote gates are isolated in these tests; their complete acceptance remains the O.6 baseline. No credentials or real network orders are used.

Reproduce the focused suite:

```sh
conda run -n hummingbot --no-capture-output python -m pytest -q \
  test/hummingbot/strategy_v2/life_liquidity/test_shared_capital.py \
  test/hummingbot/strategy_v2/life_liquidity/test_shared_capital_runner.py
```

Focused result: **66 passed**. The [regression artifact](evidence/life_o7_shared_capital_regression.json) records the broader regression, coverage, test nodes and source hashes. Reproduce that audit with:

```sh
conda run -n hummingbot --no-capture-output python -m \
  test.hummingbot.strategy_v2.life_liquidity.o6_acceptance \
  --output-dir /tmp/life-o72-final
```

The audit still certifies the spot baseline while executing the extension tests. It does not close the full O.7 gate or replace the O.7.7 extension acceptance map.

Final regression: **1,676 passed, zero failures, zero skipped**; all 61 changed production Python files were measured. Changed-line coverage passed the 80% gates against both the feature and recent baselines; exact denominators and percentages are archived in the regression artifact. Style and repository pre-commit checks passed.

## Next stages

- **O.7.3:** hedge generation/coordinator, exactly-once inventory target updates, partial hedge/retry/urgent exposure policies and executable cost binding.
- **O.7.4:** qualified mark/index/funding/tier/fee/collateral observations and monitoring after quote-session expiry.
- **O.7.5:** durable fill/cancel/account reconciliation, capital settlement/release, cold restart and joint session transitions. A breached allocation cap currently blocks new claims, including closes; the separately bounded urgent exit policy belongs to O.7.3.
- **O.7.6–O.7.7:** protected P7 inventory execution and complete combined replay/evidence acceptance.
