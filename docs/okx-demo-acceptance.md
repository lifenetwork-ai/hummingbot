# OKX Demo acceptance — P6

Status: **in progress**. Setup and authenticated preflight have completed. The
first real workflow found a reference-subscription defect before any order was
created. Its regression tests and corrected-image local E2E pass. All three real
spot cases then passed, but perpetual startup exposed a second metadata parsing
defect. Its native regressions pass; final-image verification, the remaining
matrix and the 30-minute observation are still open.

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

The fix is committed as `e9ab23687`. The corrected standard image is
`sha256:372ffe4d35561e281e93f3b6c55d96917885998f91b58b8e3a5947630f0b9d92`.
Its eight reviewed runtime hashes match the checkout; only the provider changed
from the optimization image. The corrected image passes the same selected
offline E2E matrix and recovery checks:
`build/e2e-offline/20261025-232028-c05107/result.json`.
This remains a selected-scenario gate, not a new full load/soak certification.

The previous failed startup's shutdown had left a durable HALT and a stop
checkpoint. A first corrected-image restart correctly refused to trade; that
attempt is `build/okx-demo/acceptance-20261025T162431Z.json`. Before an explicit
local Demo recovery, exchange reads confirmed **zero pending orders, zero
nonzero positions, and unchanged initial BTC/USDT balances**. The stopped owner
was absent; all orders, fills, executor and position tables were empty, with no
pending mutations, incomplete shutdown or risk-stop reason. Under the ownership
advisory lock, the reviewed recovery removed the HALT and reset only the
teardown stop flag, preserving other state/history. The service and acceptance
driver do not automatically clear HALT. Review evidence:
`build/okx-demo/reviewed-recovery.json`.

## Real spot cases and perpetual metadata defect

The corrected image completed spot/follow_market, spot/sideways and
spot/follow_token (AVAX) against the real Demo account. Each case produced four
exchange-confirmed `post_only` orders with buy and sell sides, recorded in
PostgreSQL; all 12 orders were cancelled successfully. Same-key command retries
returned the original receipts. Observed command application was about 3 seconds
for start and 2 seconds for stop. No fill occurred. Evidence:
`build/okx-demo/acceptance-20261025T163226Z.json`.

That run then failed at perpetual startup before creating any SWAP order.
The Demo instruments response included 187 rows; `TESTING-USDT-SWAP` had
`state=preopen`, empty `ctType` and empty `ctVal`. The order-book datasource
converted every returned contract value to float, so this unrelated row prevented
initializing the valid BTC book. Broker reconciliation timed out and correctly
retained HALT. The database still has the 12 cancelled spot orders and zero
perpetual orders/fills. Observations and redacted service logs are retained in
`build/okx-demo/contract-metadata-observation.json` and
`build/okx-demo/perpetual-startup-service.log`.

The datasource now reuses the connector's existing active/linear/SWAP instrument
filter, then accepts only positive finite contract values. Valid contract-to-base
book conversion is preserved. Unusable metadata for the requested contract
cannot fabricate a snapshot or make its market ready. Two new regression methods
cover unrelated malformed/preopen rows and seven invalid selected-contract
values. They fail before the fix; the affected connector, Demo routing and MM
suites pass **201 tests and seven subtests** afterward. Evidence:
`build/okx-demo/contract-metadata-red.log` and
`build/okx-demo/contract-metadata-green.log`.

The failed perpetual session has been given an explicit stop receipt through
the existing control store. HALT remains pending a fresh exchange/DB review;
historical orders and receipts are preserved. The updated standard image must
pass local E2E before real Demo workflows resume.

## Remaining acceptance

- [x] Rebuild and verify the corrected standard image with local E2E.
- [ ] Complete the nine real Demo profile/market cases, command retries,
  switches, exchange-confirmed post-only quotes and cancellation.
- [ ] Observe random/both for 30 real minutes, including restart with persisted
  anchors/deadline, random steps and automatic expiry/cancellation.
- [ ] Compare recorded history, cash/equity labels, separate market statistics
  and bounded observational reconciliation with Demo responses.
- [ ] Record naturally observed fills/funding, or mark them unverified if absent.
- [ ] Record the final image/config hashes and held positions, update the plan
  and commit the final evidence. No Cloud Run staging or OKX stress test is used.
