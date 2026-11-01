# Latent-fix image connected OKX Demo verification

Date: 2026-11-01. Status: **scoped connected Demo protocol passes**.
Candidate: image `3f5cbd0f` / manifest `6e54a348`, application source `3da3598bf`.
This is a connected Demo follow-up to the completed local load/recovery gates;
it does not change their results or close the unexplained first burst failure.

## Completed preparation

Both dedicated Demo credential sets pass authenticated REST, private WebSocket
login and read-only order subscriptions. Public books arrive for BTC/ETH/AVAX spot
and BTC perpetual. Futures/Hedge account mode is confirmed without changing it.
There are no pending exchange orders, unfinished commands, pending mutations or
HALT. Exchange contract quantities convert to exactly the stored 0.0084 BTC long
and 0.0027 BTC short. Six historical fills remain in the recorder.

The service was stopped before taking a private PostgreSQL custom-format backup.
The original `e526118b` container remains stopped for reviewed rollback. The new
canonical container uses the same private environment/catalogue, network, BOT_ID,
controller IDs, two CPUs, 2 GiB and localhost-only port 8080. Startup remains
trading-stopped and all four order/fill/executor/position hashes are unchanged.

Private configuration is unchanged: BTC-USDT spot 200 USDT / perpetual 400 USDT,
three half-spread levels 0.004/0.009/0.018, existing risk/fee/volatility controls,
BTC/ETH follow-market weights 0.6/0.4 and AVAX follow-token. Random retains its
three-profile pool and 600-second step. No tighter fill-seeking preset is used.

## Completion requirements

The protocol runs three fixed profiles across spot, perpetual and both; confirms
post-only order identity, tick/lot/minimum and budget checks independently; and
checks cancellation after Stop. Random must advance at its real YAML deadline
and preserve its session/anchor/deadlines over active restart. A separate both-
market restart must recover quotes with durable positions.

Final independent reads must reconcile terminal orders, quantities, original
fees, perpetual positions and fee-net BTC cash. Three repeated recorder reads
must retain unique fills and unchanged historical fill content. New positive
fills are reported only if they actually occur; none are forced. The final
service remains up with trading stopped, no pending orders, no HALT and no
unresolved mutations. All these completion checks pass; the positive new-fill
scope is stated separately below.

## Completed connected results

All nine fixed cases pass, each with at least 65 seconds of observation after
independently recorded quotes are found, followed by Stop and zero pending orders.
Terminal order reads verify post-only type, exact original base quantity/price
and a terminal state; local failed intents are separately classified.

| Market mode | Profile | Confirmed terminal orders |
| --- | --- | ---: |
| spot | follow_market | 27 |
| spot | sideways | 6 |
| spot | follow_token | 21 |
| perpetual | follow_market | 10 |
| perpetual | sideways | 6 |
| perpetual | follow_token | 21 |
| both | follow_market | 40 |
| both | sideways | 12 |
| both | follow_token | 82 |

Total confirmed terminal orders, including Random: **381**.
These are orders, not fills or trading volume. Snapshot quote counts are not a
claim of continuous two-sided liquidity.

The old local report helper initially grouped orders by timestamp windows and
mislabelled some first quotes: recorder timestamps have second resolution while
session starts contain fractional seconds (for example, `07:17:40.000` versus
`07:17:40.104610`). Two Red checks reproduce the boundary/missing-identity mistakes;
both pass after grouping by session identity and rejecting unknown associations.
All 381 order IDs map uniquely to structured creation-log session IDs; the 51
associations still present in durable executor metadata agree. All nine cases
have both BUY and SELL terminal orders for their expected markets. The original
time-window result remains in ignored evidence with its hash recorded in JSON.
This corrects report attribution only; image, orders and terminal venue reads
are unchanged. No trading-runtime fix or new load rerun is needed for this helper.

Random restarts with the same session, generation, anchors and global deadline,
then advances at its unchanged 600-second step from `follow_market`
to `sideways`. Repeated profile draws are allowed. A separate
active both-market restart also recovers quotes with the pinned session and
durable exposure; neither path clears HALT or reapplies old YAML seed positions.

No new natural fill occurs in this protocol. Positive new-fill/original-fee
processing is therefore **not newly verified** on this image. The six earlier
fills remain unchanged; their earlier maker-fill evidence keeps its original
image scope.

Fee-net BTC cash reconciles exactly to the baseline and observed spot fill delta.
Final perpetual holdings reconcile exactly: long **0.0084 BTC**,
short **0.0027 BTC**. Three repeated reads retain unique
new fills and unchanged historical fill content. With no new fills, the additional
economic snapshot check compares amount, cost basis, accumulated fees, traded
volume and realized PnL across the restarts; its result is recorded in the JSON.

Structured log severity counts: `{"ERROR": 6, "INFO": 1522, "WARNING": 56}`.
All 6 classified executor error records have a matching
`SubmissionBlocked` update and independent OKX `51603` (not found) for both target
instruments. There are no unclassified ERROR/CRITICAL records. The generic upstream
“check API key/network” warning is not evidence of a credential failure in these
cases; the explicit local rejection and independent order lookup are authoritative.

Final service: candidate `3f5cbd0f`, trading stopped, no pending exchange orders,
no HALT or unresolved mutations, original private files unchanged, predecessor
stopped. `/startup` and `/live` return 200; `/ready` returns 503 while stopped.
[Sanitized evidence](okx-demo-latent-fixes-evidence.json) includes full scope,
individual comparisons, readback counts and raw evidence hashes.

The local upgrade helper initially copied the predecessor's inherited
`COMMIT_SHA` environment value, so protocol logs carried the old revision label
despite the independently verified new image ID. After trading stopped, the
deployment-only label was aligned with the candidate image's own `COMMIT_SHA`.
Only that environment key changed. A fresh stopped startup passes with unchanged
four-ledger fingerprints and no ERROR/CRITICAL records; private source config
files and all trading settings remain unchanged. The task-specific launch env is
also aligned, and the original e526 rollback container remains stopped. This is
deployment metadata correction, not a new trading runtime or a rerun of trades.

The unexplained earlier load burst p99 remains open. This connected Demo run is
not an investigation of that incident or a claim of leak-free unlimited uptime.

## Local operation and rollback

Use the explicit `hummingbot-cloudrun:latent-fixes` candidate, identified by the
immutable image above. The old `hummingbot-cloudrun:local` tag still names `e526`;
it is not silently changed by this verification. Private environment/catalogue
and backup paths are recorded only in ignored local evidence, outside Git.

For rollback, first Stop through the admin API and independently confirm no
pending exchange orders or unresolved mutations. Stop the candidate process
before starting the preserved predecessor: never run two engine containers for
the same BOT_ID/account. Check schema/code compatibility and the current durable
position state before switching back. Keep the current database and history;
do not restore a pre-test backup after exchange activity, reuse stale position
seeds, or clear a HALT automatically. An unsafe/uncertain recovery requires the
existing [reviewed maintenance procedure](../../docs/postgres-runtime.md).

Raw local helpers and evidence: `build/okx-demo-latent-20261101/`. No live account,
forced/taker fill, DB repair, guard override or Cloud Run deployment is part of
this run. Earlier natural-fill/restart evidence remains on its original image.
