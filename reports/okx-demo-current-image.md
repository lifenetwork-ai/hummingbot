# Current-image OKX Demo acceptance

Date: 2026-10-30 (Asia/Saigon). Status: read-only preflight, stopped upgrade and
three fixed spot profiles and spot Random/restart complete; perpetual/both remain
in progress.
Selected immutable runtime
`715a11f3…` has completed its own local image/E2E/load/hour/control gates.

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
and the DB backup stay outside the repository. Controlled session results will
be recorded separately; this stopped phase does not claim new orders or fills.

## Fixed spot phase

All three profiles (`follow_market`, `sideways`, `follow_token`) independently
confirm exchange post-only orders with recorder identity, valid tick/lot/minimum
and notional below the unchanged YAML allocation. Each is observed for 65s, then
Stop leaves zero pending exchange orders and unchanged perpetual quantities.
The initial snapshots contain 6/6/3 orders respectively; the third is BUY-only
at that instant. These are snapshots during normal creation/refresh, not proof
that every instant has a complete two-sided six-level book. Full terminal order,
fill/fee and position reconciliation remains part of the final phase.

Spot Random has also recovered quotes after a real container restart with the
same session, profile, generation, anchors, start time and expiry. Its configured
600s step boundary advances from step0 `follow_market` to step1 `follow_token`.
The global and step deadlines remain pinned across restart, quotes recover after
the real rollover, and Stop leaves zero pending orders. The API does not override
the YAML timer. Perpetual/both and final reconciliation remain pending.
