# O.7.5 — Joint Spot and SWAP Recovery

Date: 2026-10-10 (America/Los_Angeles). **O.7.5 is complete offline for the normalized contract below. O.7 remains open. Production trading remains disabled.**

## Included scope

`JointRecovery` is an explicitly installed coordinator for the existing protected spot/SWAP senders, shared capital authority, reservation ledger, session manager and optional hedge/carry/economic ledgers. Scope is LIFE-USDT spot plus linear LIFE-USDT-SWAP, account mode 2, cross margin, explicit USDT collateral, and ONEWAY/HEDGE. Recovery supports a nonzero opening position with explicit weighted entry prices; it does not infer an entry price from the mark.

The injected collector must authenticate the UID and return complete, consistently cut regular/algo orders, trades, funding bills, account balances/positions and runner scope. Source strings identify a contract; they do not authenticate it. Tests supply synthetic observations and fake transports through actual controller, V2 filter and executor paths. No credentials or real orders are used.

## Reconciliation and financial invariants

- A durable opening anchor and immutable policy bind account UID, journals, initial holdings/cash/entries and numerical freshness/poll/cancel limits. Complete history is retained from that anchor, including settled claim tombstones. Omitted or changed history, foreign orders, unsupported algo scope, stale sequences, future observations and clock rollback deny permission.
- Every order matches its product, intent/wire/exchange IDs, session/epoch, configuration, quantity, direction and WAL. SWAP pre-send action provenance is checked even when the cold recorder has no row. Runner fill/cancel hints require matching complete history; a collector completeness flag cannot hide foreign runner children.
- Lost ACK is repaired only from bound order evidence. Partial fills update actual exposure and wallet amounts while retaining the complete capital claim. ACK, cancel ACK, absent history and session expiry never prove terminal state. A proven allocation-before-WAL/action gap may become unsent; a sent/unknown order cannot.
- Complete spot fills and SWAP signed fees, realized PnL and funding apply in chronological order to **one atomic wallet checkpoint**. Verified earlier sale proceeds can finance a later funding debit. Replayed immutable IDs cannot apply twice. Gross adverse execution costs consume the durable loss budget; gains, fee rebates and funding receipts do not refund gross spending budgets.
- Both product positions and physical LIFE/USDT balances must agree with the qualified complete account snapshot. OPEN updates weighted entry; CLOSE cannot exceed its actual leg or reverse exposure. Remaining positions remain open after a partial close or order cancellation.
- NAV is wallet USDT plus physical LIFE at the qualified index plus remaining SWAP unrealized PnL at the qualified mark. Eligible collateral is a risk input and is not added again. Approved external cash flows adjust performance separately. Installed carry history must match the complete funding bill set; factual settlement can proceed while exhausted carry budgets continue to deny new risk.

## Durable ordering and permissions

The coordinator commits an **APPLYING** receipt containing the qualified history and digest before wallet, economic attribution, WAL, hedge and capital writes. Only this bound authority can settle a terminal claim into an immutable tombstone. **COMMITTED** is written last. A failed or ambiguous write fences creation; fresh objects replay a compatible full history idempotently. There is no network exactly-once claim and no retry of an old uncertain send.

Both protected creation routes and shared allocation require a fresh completed joint receipt. Session successor/reference transitions additionally require that both old-epoch WALs and capital scopes have reconciled. A spot-only receipt cannot bypass unresolved SWAP scope. Successor timing preserves the absolute parent deadline.

Restore is read-only until fresh joint proof and explicit operator rearm. Rearm preserves durable hedge targets, versions, attempt/cost limits and residual exposure. It cannot clear a cost breach or runtime HALT, revive an expired session, or enable production trading. The readiness-independent safety tick polls on an explicit interval after quote expiry. Cancel attempts persist before the bounded transport request; retries retain unresolved claims. On stop, account ownership remains held until both order-safety and joint-reconciliation tasks finish cancellation cleanup.

## TDD and replay evidence

Initial Red: importing the not-yet-created recovery module failed collection. Subsequent regression cases exposed duplicate status request identities and wallet ordering around sale proceeds/funding. The shutdown race was reproduced with **two failing cases**: joint polling alone, and order-safety cleanup finishing before joint cleanup. The corrected shutdown keeps account ownership through both tasks; all three completion cases pass.

The focused suite has **55 passing cases**:

- Lost ACK, partial close, immutable terminal history, bounded cancel and disconnect recovery.
- UID, completeness, pending/algo, balance, position, fee, exchange ID and duplicate-history failures.
- Fresh-object restore at six cuts: before apply, after wallet, after WAL terminal, after capital settlement, before final receipt and clean restore.
- Both executor products into one wallet/economic ledger; funding and collateral deduplication; realized versus remaining unrealized PnL.
- ONEWAY/HEDGE OPEN/CLOSE directions, SWAP runner hints and cold recorder provenance.
- Hedge preparation gaps, residual retries, retained cost breaches, successor/reference guards, post-expiry polling, cancellation interruption and account ownership during stop.

The interruption tests inject exceptions/cancellation around real file-backed checkpoints and reopen fresh objects. They are not process-kill or power-loss tests, and the fake runner/connector scope does not prove actual OKX/database history completeness.

Reproduce:

```sh
conda run -n hummingbot --no-capture-output python -m pytest -q \
  test/hummingbot/strategy_v2/life_liquidity/test_joint_recovery.py

conda run -n hummingbot --no-capture-output python -m \
  test.hummingbot.strategy_v2.life_liquidity.o6_acceptance \
  --output-dir /tmp/life-o75-acceptance
```

The [regression artifact](evidence/life_o7_joint_recovery_regression.json) retains exact broad test results, changed-line coverage, focused node outcomes and source hashes. Its nested O.6 audit continues to certify **Spot Offline Complete**; O.7.7 owns the full extension acceptance map. The O.6 manifest's existing deferred cases and unchecked detailed P text remain unchanged; the new checked P children record only this completed slice.

## Acceptance mapping and remaining work

| Requirement / case | Verified O.7.5 slice |
| --- | --- |
| P6.2 / A03 | Fixed contract/base quantities, actual account legs, close PnL and residual exposure |
| P6.3 / A14 | Mode/action-aware partial CLOSE, uncertain ACK and disconnect settlement |
| P6.15 / A16 / A18 | Durable joint scope, cancellation and cross-product successor guards |
| P8 / A35 / A39 | Fresh-object replay, single accounting application and explicit rearm |

These are extension slices; they do not close all requirements or acceptance cases represented by those IDs.

Authenticated endpoint/WS collection, pagination, regular/algo/trade/bill completeness, currency/sign/precision normalization and consistently cut snapshot assembly remain D/R. Opening anchors must distinguish already included payments from subsequent history; ambiguous anchor-boundary amounts fail balance reconciliation. History is retained without pruning. One canonical host-local recovery directory and account UID ownership lock are required when configured; synthetic isolated fixtures omit that deployment configuration. Cross-host coordination is outside the MVP.

Actual exchange enforcement, deployment/database/process-loss evidence, numeric calibration and operational position handoff remain D/R. Monitoring requires a running controller; stopping does not close a position. O.7.6 protected inventory execution and O.7.7 combined extension replay remain open. **Full Offline Complete and live eligibility are not claimed.**
