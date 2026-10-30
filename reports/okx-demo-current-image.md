# Current-image OKX Demo acceptance

Date: 2026-10-30 (Asia/Saigon). Status: **the scoped current-image Demo protocol
passes**, including read-only preflight, all nine profile/mode cases, Random
rollover, active restart and exact terminal order/position reconciliation.
**No natural new fill occurred**, so positive new-fill/original-fee processing
was not observed during this protocol. [Sanitized evidence](okx-demo-current-image-evidence.json).

This document preserves that zero-fill run. The subsequent
[natural-fill follow-up](okx-demo-natural-fill.md) observed six real maker fills,
found a filled-restart defect on715a, then verified the repaired exposure on
fixed imagee526 after operator-reviewed maintenance. The canonical local service
now uses e526 with trading stopped; the results below retain their original scope.

Selected immutable runtime `715a11f3…` has completed its own local
image/E2E/load/hour/control gates. These new Demo observations apply to that
image; earlier P6/P7 Demo results keep their original scope.

The operator explicitly requested current-image Demo verification: spot profiles,
Random step/restart, then perpetual/both and recorder/account reconciliation.
Checklist: [current-image Demo plan](../docs/plans/OKX_DEMO_CURRENT_IMAGE_ACCEPTANCE.md).

## Read-only preflight and stopped upgrade

- Both dedicated credential trios authenticate to the same Demo account with
  read/trade permissions. Account mode is Futures, position mode Hedge, leverage
  1x/Cross. No account-mode/leverage/order mutation was sent by preflight.
- Public REST metadata and real public WS books pass for BTC/ETH/AVAX spot and
  BTC swap; private WS login and order subscriptions pass for both credential sets.
- Independent reads find zero pending orders. Durable intent is stopped and
  acknowledged, with no HALT, pending mutation or unfinished admin command.
- Existing BTC perpetual positions are long 0.0081 / short 0.0022 BTC. Exchange
  contracts converted using the returned contract value match the two DB rows.
- The existing private catalogue validates unchanged: spot 200 / perpetual 400 USDT,
  combined 600; strict guards, three weighted levels and volatility defense remain.
  Exposure/inventory caps and the configured 600s Random step are preserved.
- Verify ten runtime fingerprints; take a private PostgreSQL backup, retain the
  old container stopped and start the new supervised runtime stopped, using the
  same DB/BOT_ID/controller IDs/private files. No trading Start command is part
  of this phase. All 12 existing order rows, zero fill rows and two position rows
  retain their exact content hashes across the upgrade.
- New local service uses two CPUs/2GiB, total pool 5 and daily TTL 15s. Only localhost
  exposes port 8080. The old stopped container is retained for reviewed rollback.

Raw helpers/results are ignored under `build/okx-demo-current/`. Private files
and the DB backup stay outside the repository. The stopped-upgrade phase above
contains no trading Start; the following controlled protocol is separately scoped.

## Fixed spot phase

All three profiles (`follow_market`, `sideways`, `follow_token`) independently
confirm exchange post-only orders with recorder identity, valid tick/lot/minimum
and notional below the unchanged YAML allocation. Each is observed for 65s, then
Stop leaves zero pending exchange orders and unchanged perpetual quantities.
The initial snapshots contain 6/6/3 orders respectively; the third is BUY-only
at that instant. These are snapshots during normal creation/refresh, not proof
that every instant has a complete two-sided six-level book. The independent
terminal review below confirms that both directions were actually placed in
each case; it does not infer uninterrupted two-sided uptime from those snapshots.

Spot Random has also recovered quotes after a real container restart with the
same session, profile, generation, anchors, start time and expiry. Its configured
600s step boundary advances from step0 `follow_market` to step1 `follow_token`.
The global and step deadlines remain pinned across restart, quotes recover after
the real rollover, and Stop leaves zero pending orders. The API does not override
the YAML timer. `session.generation` is preserved; engine ownership legitimately
changes on restart. All twenty Start/Stop receipts complete successfully.

## Nine-case matrix and independent terminal readback

Each fixed case observes at least 65s before Stop. Independent OKX order-detail
reads confirm the original price and base quantity exactly, using the returned
swap contract value for contract-to-BTC conversion. All confirmed orders are
post-only and canceled. BUY and SELL both occur for every selected market in
every case, while the original exposure/inventory/volatility gates remain active.

| Market mode | Profile | Confirmed terminal orders |
| --- | --- | ---: |
| Spot | follow_market | 18 |
| Spot | sideways | 6 |
| Spot | follow_token | 18 |
| Perpetual | follow_market | 23 |
| Perpetual | sideways | 6 |
| Perpetual | follow_token | 15 |
| Both | follow_market | 42 |
| Both | sideways | 18 |
| Both | follow_token | 32 |

The Random session accounts for another 226 confirmed orders: **404 canceled
post-only orders total**. PostgreSQL adds 405 order rows, including one local
failed intent with no exchange order. These are order counts, not trades/volume.
The real exchange price/quantity checks also verify that recorded BTC order
precision survives the current PostgreSQL path.

## Fills, fees, guards and final state

- Independent exchange fills and new recorder fills both equal zero. Original-fee
  comparisons therefore equal zero; no positive fill/fee claim is made. Existing
  fill/fee acceptance on older images is not reused as current-image evidence.
- Exchange quantities still match DB long 0.0081 / short 0.0022 BTC exactly. No
  initial position is flattened or relabelled, no HALT is reset and no fill is forced.
- Logs contain 1,567 INFO, 48 WARNING and 8 ERROR records, all structured JSON;
  no CRITICAL or unclassified ERROR remains. Six create attempts are blocked by
  `spread_policy_quote_rejected`; two by joint market/data/exposure readiness.
  The executor logs these eight attempts as open-order retry errors. All eight
  client IDs are independently absent on OKX for both target instruments: seven
  are blocked before an order row is written, one after a local intent exists.
  These are enforced safety checks, not eight accepted orders or credential failures.
- Final DB counts: 417 orders, zero fills, two positions, zero funding rows and
  zero unfinished commands; timezone is UTC. Control/acknowledged versions both 22.
- The new service stays up on localhost:8080 with trading stopped, owner healthy,
  no HALT, pending mutation or pending exchange order. `/startup` and `/live`
  return 200; `/ready` correctly returns 503 while trading is stopped. DB, private
  files, backup and stopped predecessor container are preserved.

This is BTC-USDT Demo integration, not LIFE listing/live or Cloud Run acceptance.
No market order, tighter-spread override, new risk budget or API/YAML override is
introduced to obtain fills. A future natural fill would need its own explicit
positive amount/fee comparison; the zero-fill limitation is part of this result.
