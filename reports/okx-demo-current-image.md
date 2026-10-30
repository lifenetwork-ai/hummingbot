# Current-image OKX Demo acceptance

Date: 2026-10-30 (Asia/Saigon). Status: read-only preflight and stopped upgrade
complete; controlled trading acceptance is pending. Selected immutable runtime
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
- Existing BTC perpetual positions are long0.0081/short0.0022 BTC. Exchange
  contracts converted using the returned contract value match the two DB rows.
- The existing private catalogue validates unchanged: spot200/perpetual400 USDT,
  combined600; strict guards, three weighted levels and volatility defense remain.
  Exposure/inventory caps and the configured600s Random step are preserved.
- Verify ten runtime fingerprints; take a private PostgreSQL backup, retain the
  old container stopped and start the new supervised runtime stopped, using the
  same DB/BOT_ID/controller IDs/private files. No trading Start command is part
  of this phase. All12 existing order rows, zero fill rows and two position rows
  retain their exact content hashes across the upgrade.
- New local service uses two CPUs/2GiB, total pool5 and daily TTL15. Only localhost
  exposes port8080. The old stopped container is retained for reviewed rollback.

Raw helpers/results are ignored under `build/okx-demo-current/`. Private files
and the DB backup stay outside the repository. Controlled session results will
be recorded separately; this stopped phase does not claim new orders or fills.
