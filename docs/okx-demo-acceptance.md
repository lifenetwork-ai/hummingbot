# OKX Demo acceptance — P6

Status: **the bounded Demo protocol passed**, completed on 2026-10-26 in
Asia/Ho_Chi_Minh. Natural fill/fee, filled-position recovery and funding checks
remain explicitly unverified. Two real integration defects were fixed with
Red/Green regressions before the final run. Portable, credential-free evidence:
[okx-demo-acceptance.json](okx-demo-acceptance.json).

The currently retained runtime is
`sha256:17d530e26018da8c5d0e4f7c2f769fc29c28dd04f6a3c908f7f6f5568fa49023`,
code commit `32285f5c8`. It includes the cancellation fix discovered after the
observation and passes full native regression, selected local E2E, a real fenced
Demo terminal-response probe and idle startup/shutdown checks. The 30-minute
numbers below retain their original `8401c10c2c5c...` image identity.

| 30-minute observation check | Observed result |
| --- | --- |
| Three profiles × spot/perpetual/both | 9/9 cases pass; 48 confirmed post-only quotes |
| Admin control | 17 completed start/switch/stop receipts; matching retries return the same receipt |
| Random/both session | 1,800-second configured duration; 1,801.585 seconds observed through expiry/cancellation |
| Random steps | Steps 0/1/2; the three-profile pool happened to draw follow_market each time |
| Graceful restart at minute 15 | Session, anchors, start and deadline preserved; both markets ready again |
| Observational reconciliation | Both checks matched; each checked 12 spot and 12 perpetual orders |
| Final exchange read after service stop | Zero pending orders; zero retained perpetual positions |
| Demo cash | BTC 1, ETH 1, OKB 100, USDT 5,000; unchanged |
| Final database | 300 cancelled orders, zero fills, UTC, owner released, no HALT |

Of the 300 persisted orders, 12 belong to the earlier spot pilot and 288 to the
observation image: 144 spot and 144 perpetual (48 matrix quotes plus 240 observation
quotes). History was preserved across fixes and reviewed recovery. The stopped
service and PostgreSQL data are retained locally; the PostgreSQL container is
still running. The final image's selected local E2E passed; no new full offline
load/soak or Cloud Run deployment is claimed.

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

The metadata fix is committed as `c9b858e36`. The observation image is
`sha256:8401c10c2c5c50f4d426db6f2e8529506923fa77b02b0090f028e56c11c04fc2`;
all nine reviewed runtime source hashes match the checkout. Its selected LIFE
E2E matrix/recovery passes:
`build/e2e-offline/20261025-235024-307a7a/result.json`. It has
`release_scope_complete=false`; no new full load/soak is claimed for this image.

The failed perpetual session received an explicit stop through the existing
control store. Before a second narrowly reviewed local Demo recovery, all 12
recorded spot order IDs were re-read from OKX and confirmed cancelled with zero
fill; there were no pending orders, positions or balance changes. The native
corrected datasource also initialized a real BTC SWAP snapshot successfully.
Under the ownership lock, only the known startup HALT/reason and teardown stop
flag were reset. Order/receipt history was preserved. Evidence:
`build/okx-demo/reviewed-recovery-after-metadata.json`.

The observation image completed all nine real profile/market combinations, with four
exchange-confirmed post-only quotes in each single-market case and eight in each
both-market case. Both-profile switches and matching command retries passed;
test quotes cancelled on stop. The 30-minute random/both session completed, with
two observational reconciliations returning `matched` independently for spot
and perpetual, with no scope reasons or differences in either checked window.

The authenticated FE probes return HTTP 200 for Swagger, OpenAPI, profiles,
reference discovery, orders, fills, balances, positions, performance and daily
statistics. Balances expose one verified Demo account with unique BTC/ETH/OKB/USDT
currency observations and separate cash/equity fields, rather than duplicate
collateral per connector. Both position projections are empty at this observation.
Probe evidence: `build/okx-demo/fe-endpoints.json`.

The observation crossed both scheduled 600-second step boundaries (a repeated
follow_market draw is valid). At minute 15, a graceful container restart restored
the same session, both markets' anchors, original start and expiry; the current
health endpoint returned ready before acceptance continued. The driver refreshes
Docker's dynamically republished loopback port after restart. No session deadline
was extended, and no recovery HALT occurred during the healthy observation.
Automatic expiry cancelled the quotes. An independent account read after the
service stopped confirmed no pending orders or perpetual positions and unchanged
cash; PostgreSQL confirms the expired/stopped session, UTC and released owner.
The latest recorded order precedes the session expiry.

Final local run: `build/okx-demo/acceptance-20261025T165603Z.json`. The portable
artifact includes the final independent read, scoped reconciliation results,
configuration hash, runtime image identity and FE probes, with account IDs masked.

To reopen the same configured Demo service for FE work, start
`hummingbot-okx-demo-postgres` and `hummingbot-okx-demo-bot`, then discover the
current loopback port with `docker port hummingbot-okx-demo-bot 8080/tcp`.
The container retains the private env and BTC catalogue mount; root `.env` and
the shipped LIFE catalogue are separate. The expired session does not start new
quotes until an explicit authenticated start command is accepted.

## Post-observation cancellation fix

The 30-minute image log contains 40 ERROR entries for duplicate perpetual
cancellations returning `sCode=51400`. Each of those order IDs also has a
successful cancellation event; final REST/DB readback confirms it is cancelled.
There are no other ERROR entries, pending-task destruction, unawaited coroutine,
unclosed client-session or unclosed connector diagnostics. Both process exits
are clean. These counts belong to the observation image and are retained in the
portable evidence rather than relabelled as zero errors.

The perpetual adapter accepted `51603`/`51401` but omitted the `51400` response
already handled by the upstream spot adapter. Adding it exposed a second race in
the shared cancel path: a late HTTP acknowledgement could overwrite a terminal
WS/REST state with `PENDING_CANCEL`. The shared path now skips that update for an
already-done order. An asynchronous acknowledgement still leaves an open order
pending until an authoritative terminal update; cancellation never fabricates a
fill or terminal state. [OKX cancellation and order-state documentation](https://www.okx.com/docs-v5/en/#order-book-trading-trade-post-cancel-order).

New regressions fail before the fixes and exercise pending-before-readback,
repeated replies after cancellation, and concurrent CANCELED/FILLED observations.
The full Cloud Run regression target passes **1,259 tests and nine subtests**
(149 + 11 + 906 + 193). Evidence: `build/okx-demo/cancel-51400-red.log`,
`cancel-terminal-race-red.log`, `cancel-51400-green-focused.log` and
`cancel-race-regression.log` in the same directory.

Commit `32285f5c8` records the fix. The retained standard image
`sha256:17d530e26018da8c5d0e4f7c2f769fc29c28dd04f6a3c908f7f6f5568fa49023`
matches all 12 reviewed runtime source hashes and passes selected local LIFE E2E
and recovery: `build/e2e-offline/20261026-010051-890fb9/result.json`.
The direct Demo probe uses the real PostgreSQL ownership gate with new risk
disabled and an expired/stopped session. An already-cancelled order returns the
actual `51400`; its local state remains pending before readback, becomes CANCELED
after authoritative REST readback, and stays CANCELED after a repeated reply.
No new order is submitted. The same image starts an idle service with the
persisted expired session and stops cleanly with zero ERROR lines; DB history
remains 300 orders/zero fills and ownership is released.

Evidence: `build/okx-demo/terminal-cancel-demo-probe.json`,
`build/okx-demo/cancel-fixed-idle-smoke.json`, and the portable artifact's
`post_observation_cancel_fix`. The 30-minute observation remains evidence for
image `8401c10c2c5c...`; it is not relabelled as a new soak of this fix.

## Acceptance checklist

- [x] Rebuild and verify the corrected standard image with local E2E.
- [x] Complete the nine real Demo profile/market cases, command retries,
  switches, exchange-confirmed post-only quotes and cancellation.
- [x] Observe random/both for 30 real minutes, including restart with persisted
  anchors/deadline, random steps and automatic expiry/cancellation.
- [x] Compare recorded history, cash/equity labels, separate market statistics
  and bounded observational reconciliation with Demo responses.
- [x] Record naturally observed fills/funding, or mark them unverified if absent.
- [x] Record the final image/config hashes and held positions, update the plan
  and commit the final evidence. No Cloud Run staging or OKX stress test is used.
- [x] Validate the post-observation cancellation fix in its rebuilt image with
  local E2E and direct Demo terminal-response/readback checks.

## Unverified Demo checks

- [ ] Observe natural fills and confirm private-stream/recorder fee accounting
  and restart with filled exposure against Demo. This run had zero fills and
  zero perpetual positions; the existing local simulator covers these cases.
- [ ] Observe a real Demo funding payment and compare the retained original
  currency amount with the exchange. No payment was observed in this run.

The three-profile matrix covers all profiles, while the long random sample
happened to select only follow_market. BTC Demo integration does not verify LIFE
listing availability, live fill quality or profitability. Original scale-six
trade storage and legacy funding precision are unchanged.
