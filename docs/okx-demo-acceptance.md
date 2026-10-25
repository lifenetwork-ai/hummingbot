# OKX Demo acceptance — P6

Status: **in progress**. Setup and authenticated preflight have completed. The
first real workflow found a reference-subscription defect before any order was
created. Its regression tests pass; the corrected image must be tested before
resuming the workflow matrix and the 30-minute observation.

## Configuration and preflight

- Environment: dedicated Global OKX Demo account; both connector credential
  models validate and authenticated account reads return code `0`.
- API permissions reported by OKX: `read_only,trade`.
- Account mode: Futures (`acctLv=2`), enabled by the account owner on OKX.
- Position mode: Hedge (`long_short_mode`), configured and re-read through the
  existing perpetual connector after confirming no positions or pending orders.
- BTC-USDT-SWAP: Cross 1x leverage acknowledged and independently re-read.
- BTC-USDT, ETH-USDT and AVAX-USDT have active Demo spot and USDT SWAP metadata.
  BTC-USDT is the explicitly selected test instrument; this is not LIFE listing
  acceptance.
- Trading: BTC-USDT spot/perpetual, 100 USDT per market, existing 1%/2% maker
  spreads and 60-second refresh. The weighted benchmark remains 60% BTC and
  40% ETH; `follow_token` follows AVAX-USDT with weight 1.
- Initial Demo wallet observations include 1 BTC, 1 ETH and 5,000 USDT. The spot
  cap is 1.01 BTC: initial BTC plus 0.01 BTC of headroom. A 0.01 BTC absolute cap
  would reject the initial wallet before quoting. Perpetual long/short caps remain
  0.01 BTC each, gross 0.02 BTC; no position is automatically flattened.
- Sample instrument minimum notionals were approximately 1.61 USDT for BTC spot
  and 8.06 USDT for BTC SWAP. Each configured level has 25 USDT before live
  quantization. Runtime trading rules remain authoritative as prices change.

The private env, generated admin password and profile catalogue are outside the
checkout under `~/.config/hummingbot/`. The root `.env` and the shipped LIFE
catalogue were not replaced. Credentials and database URLs are not included here.

The dedicated local PostgreSQL 14 container is
`hummingbot-okx-demo-postgres`, database `okx_demo`, on its own Docker network and
persistent volume. Its host port is bound to loopback. `DATABASE_URL` has been
filled in the private Demo env; the container env uses the database's Docker
hostname. PostgreSQL and runtime connections report UTC. All 13 packaged SQL
scripts replayed successfully twice against the new database, which has 16
public tables. `ENABLE_AUTO_MIGRATION=true` remains enabled for service startup.

Local preflight artifacts:

- `build/okx-demo/preflight.json`: initial authenticated reads and public metadata;
  its account mode is the observation **before** the owner's switch.
- `build/okx-demo/account-preparation.json`: subsequent Futures/Hedge/1x
  confirmation, with no existing orders or positions and no orders submitted.

## First deployment-image attempt and regression

The reconstructed standard image
`sha256:79df8bfee6af9e67d7de111c4256d2da1a875ab92d973146620e9d4cbf403086`
matched the eight previously verified optimization runtime source hashes and the
checkout. Its local `life-mm` E2E passed all nine profile/market combinations,
restart anchors, weighted references, stale-data joint stopping, fills, random
expiry, reconciliation and DB-fault HALT. Evidence:
`build/e2e-offline/20261025-230310-f0812a/result.json`.
This selected-scenario run has `release_scope_complete=false`; it does not
recertify the previous full load/soak on a different image.

The first real Demo case, spot/follow_market, did **not** pass. BTC-USDT is both
the trading target and a basket member. The provider attempted to add BTC to a
tracker that already owned that book; the upstream duplicate-add operation
returned false. The controller repeatedly failed reference startup and the
acceptance driver timed out. The run was stopped, with zero recorded orders and
zero remaining test-owned quotes. Evidence:
`build/okx-demo/acceptance-20261025T160917Z.json`.

Two new parametrized regression cases exercise the real provider initialization
path with a pre-existing BTC book, a new ETH subscription and two consumers, for
both trading and already-started public-only connectors. They failed before the
fix (`BTC-USDT: false`) and pass afterward. The provider now reuses a tracked book
after the public-network startup check. Existing reference ownership keeps BTC
subscribed and removes only the new ETH book after its last consumer releases it.
Controller price freshness and inventory checks are unchanged.

The six affected test files pass **140 tests**. The reference subscription tests
are also included in `make test-cloudrun-regression`. Red/Green evidence:
`build/okx-demo/reference-reuse-red.log` and
`build/okx-demo/reference-reuse-green.log`.

## Remaining acceptance

- [ ] Rebuild and verify the corrected standard image with local E2E.
- [ ] Complete the nine real Demo profile/market cases, command retries,
  switches, exchange-confirmed post-only quotes and cancellation.
- [ ] Observe random/both for 30 real minutes, including restart with persisted
  anchors/deadline, random steps and automatic expiry/cancellation.
- [ ] Compare recorded history, cash/equity labels, separate market statistics
  and bounded observational reconciliation with Demo responses.
- [ ] Record naturally observed fills/funding, or mark them unverified if absent.
- [ ] Record the final image/config hashes and held positions, update the plan
  and commit the final evidence. No Cloud Run staging or OKX stress test is used.
