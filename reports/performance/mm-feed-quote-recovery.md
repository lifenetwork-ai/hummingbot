# MM feed / unsent quote recovery — 2026-11-10

Latest status (2026-11-10 18:40 local): **revision84 is now HALTed** after an
18:32 OKX50013/HTTP429 submission failure; the earlier short passing window is
historical. See the incident below.

The offline change addresses three separate paths observed in the Cloud review:
quiet reference books, queued entry prices overtaken by a fresh reference/fee
check, and repeated diagnostics for expected operational conditions. It does not
establish one root cause for every historical probe timeout or no-quote interval.

| Path | Resulting behavior |
|---|---|
| Required LIFE book approaches its freshness limit | Shared per-book native public snapshot read; healthy books cause no extra request. Two requests maximum, three-second timeout, capped backoff, one queued snapshot/book. |
| REST is stale, unavailable, regressive or overtaken by WS | Keep the actual book/guard state. Reject older/equal recovery snapshots again at the final tracker consumer; ordinary upstream snapshot/replay behavior stays intact. |
| Managed entry waits before its first order identity | Re-evaluate reference, fees, sizing and admission. Preserve executor/session/level and original base/notional ceilings. A failed check retires without submitting. Existing order/retry parameters and final transport checks remain authoritative. |
| Expected drawdown valuation is unavailable | Keep reports unready; first WARN plus thirty-second summaries and INFO recovery. Unexpected errors retain ERROR traces. |
| Final spread gate rejects a quote | Connector owns bounded WARN summaries; executor retains per-order DEBUG and all terminal bookkeeping. Rejection still retires the entry; no silent transport retry or guard relaxation. |

Stop releases controller leases, cancels requests and drains tracked tasks.
Native tests cover overlapping consumers, stricter leases, WS races, native
spot/perpetual timestamp units, timeout/backoff and repeated acquire/release.
Logging summaries retain at most 64 keys per instance.

Candidate: Linux amd64 image `1d3c3a6703b9…`, manifest
`sha256:88494b868c32b72cd16c4a0836f1ea15dfb730adce7fb894d6ca8254f4b3a402`.
It inherits the retained startup-reconciliation candidate `d5c7667254ab…` and
copies nine runtime Python modules; all nine packaged hashes match this workspace.
Raw attempts and the overlay manifest are retained under
`build/mm-feed-quote-offline-20261110/`.

Validation on that candidate: **903 native tests + 4 subtests, nine targeted
PostgreSQL tests and four packaged E2E scenarios pass** (`feed-recovery`,
`availability`, `drawdown`, `spread-policy`). PostgreSQL verifies the unchanged
actual quote callback at intent and final transport, including rejection after
the quote changes. The full PostgreSQL/release suite was not rerun.
Portable paths, hashes and metrics are in the [evidence](mm-feed-quote-recovery-evidence.json).

Parent-code Red runs reproduce
missing quiet-book recovery, stale first-entry pricing, duplicate diagnostics and
the queued WS/snapshot race. Fixture setup mistakes are retained separately and
are not counted as demonstrated runtime bugs. An additional test caught and fixed
a preparation-exception retry hazard in the uncommitted implementation.

The packaged quiet-feed scenario passes: 13 seconds of quiet ETH WS, two ETH REST
reads, zero extra BTC snapshots, sampled working quotes maintained; old REST then
withdraws quotes, a real503 read stays blocked, and fresh WS restores quoting in
the same process/session/deadline. Stop passes. All venue activity is simulated.

The 120-second diagnostic load passes at 20 main reads/s, about 100 market updates/s
and one simulated fill/s against 100,000 historical orders and 100,000 fills.
All 120 new fills, original fees and positions reconcile. API p95/p99 are
231.82/540.70 ms; zero unexpected API errors or declared budget violations.
This instrumented ARM-host/amd64 run, with security/features enabled, is not a
paired latency comparison with the older full release. The application limit is
2 CPU / 2 GiB; peak summed RSS is 776,462,336 bytes. It does not qualify a long soak or
establish memory retention on the deployed 1 GiB service.

One engine probe times out in the measured window (397.96ms), then recovers;
the supervisor transition is 1.068 s. There is no observed engine replacement or
HALT. Correlated strategy ticks have a maximum 1.002 s start gap, controller cycles
1.303 s; venue samples retain at least 12 working orders with no empty sample.
Discrete samples do not prove uninterrupted quoting. A 251 ms NATR calculation
precedes the failure and a 206 ms observation overlaps it; this is correlation,
not a unique root cause. A second probe failure/recovery is after the measured
window and remains in the raw logs. Residual probe latency is still open.

Inventory skew, perpetual exposure caps, freshness budgets, drawdown policy and
trading cadence are unchanged. This work makes no Cloud deployment, financial
maintenance or real OKX writes. Cloud effectiveness, a new thirty-minute quote
qualification and the full release/long soak remain separate acceptance scopes.

## Revision83: configured maker price / native cancel follow-up

Cloud revision `hummingbot-demo-00083-wfb` runs source `7372ecc21`, digest
`sha256:be5e1d440a310481712c84afa56f5ad3be9d6cd37f9032fd3389a4e993780f76`.
Read-only sampling 10:22:01–10:24:51 UTC preserves a failed quote window:
15/18 readiness reads return 200, three return 503, no HALT; Spot has no working order in 17 samples
and one SELL in one, while Perpetual remains cap-blocked. Some failures have
fresh book timestamps but `inconsistent_own_depth`, not stale reference data.
Four repeated Spot cancellations independently read from OKX have zero fills,
post_only type and cancelSource31: they would take liquidity. No Cloud writes
or financial maintenance were performed.

The configured-price branch bypasses the native executor's passive price bound;
the cancel callback then reuses the same price on a new identity. These underlying
methods already exist unchanged before `7372` (AST comparison retained). Previous
fixture behavior acknowledged crossing post-only orders as live, so the old soak
did not test this venue lifecycle. This is a code/coverage gap, not an OKX
connection failure.

The follow-up freezes BUY min(model,best bid)/SELL max(model,best ask) before
identity creation, preserving both original and current base/notional ceilings.
Existing quote/fee/cap/freshness gates run afterward. A native-confirmed zero-fill
managed maker cancellation retires through normal hold/shutdown, fetching the
native cached order when cancel precedes created/ACK. Partial/unknown paths,
explicit Stop priority and empty-book bootstrap retain their existing behavior.
No existing order identity is repriced.

Nine native Red cases reproduce the gap. Under identical corrected fee-floor
fixture/harness hashes, source `7372` image `1d3c…` generates 44 zero-fill cancellations
and times out with 6/8 required quotes; candidate `2d4d…` has two real cancelSource31
events and restores 8/8 before Stop. Its final ledger has no duplicate physical
submissions or fills. Preliminary wrong-bound assertions and a no-fee fixture
pass are retained, but are not counted as valid runtime Red. A current-allocation
counterexample was also caught and corrected before deployment.

Candidate `2d4d7561e080…`, manifest
`sha256:195b7b3e6dcd5e7f20d8d137a69258866e45cb5f8c067716d428cd1bd38897a2`,
copies two verified runtime modules over `1d3c…`. Its 670 targeted native tests plus
two separate book-depth controls, nine PG guards and three packaged scenarios
(`maker-entry`, `listing-bootstrap`, `spread-policy`) pass.

The first post-only-enforced 120s load **fails fill_rate: 71/120**, despite no
unexpected API errors and p99 298.35 ms. Recent own orders at L1 are conservatively
subtracted at full quantity for 30s; the original one-level SWAP fixture can then
fall below the unchanged 50 USDT outside-depth requirement. Native controls prove
this protective restriction without changing guard code. A separate healthy
two-level input retains the exact original L1 prices/quantities and adds independent
next-tick L2. That 120s load passes 120 fills, original fees/positions, 20 main
reads/s and about 100 market updates/s: API p95/p99 85.11/415.91 ms, zero unexpected
API errors or budget violations. Limits remain 2 CPU / 2 GiB. This is a distinct
healthy input, not an unchanged performance comparison. Neither the thin-book failure nor
the actual Cloud quote window is converted into a pass.

The final L2 diagnostic also has one engine probe recovery transition of 1.041s;
its unique cause remains unproved.

Revision83 above still has the old source. The subsequent revision84 verification
is recorded below; residual own-depth/probe behavior and full release qualification
remain open.


## Revision84: connected Demo deployment verification

On 2026-11-10, revision `hummingbot-demo-00084-9bv` runs source `860314b62`,
Cloud image `sha256:9f619d5ac20046420d4d23e4a134d9785701bc608de8c32b4344ebece1356646`,
2 CPU / 1 GiB, 100% traffic. This is the actual CI-built image, distinct from the
local overlay image above. Verification was read-only; no Stop/Start, risk changes,
credential changes or financial maintenance were performed.

At 11:16:26–11:19:16 UTC (18:16–18:19 local), all 18 samples of each startup/live/ready
endpoint return 200. Engine is running without HALT throughout the samples, with
one session and control/ack55. Spot has two working SELL orders in 17 samples,
one in the remaining sample, and no BUY: inventory skew has BUY multiplier zero.
Perpetual has no working orders in all 18 samples, with `inventory_limit` on both
sides. These are discrete short observations, not continuous or two-sided qualification.

Independent OKX Demo REST reads at 11:17:36 and 11:23:04 UTC find respectively two
and one live Spot SELL orders, all matched by client ID to the bot API. The latest
eight order details in each read include filled orders and client cancellations
(cancelSource1, including a roughly 61-second refresh); none has cancelSource31.
This sampled readback no longer exhibits the old repeated post-only rejection loop.
The closing API read at 11:23:35 UTC again has two working Spot SELLs, all health
endpoints200 and no HALT.

At 11:20:42 UTC, **20 actual new Spot Demo fills** after revision readiness match
native REST trade IDs, quantities, prices and original USDT fees exactly, with no
duplicate API fill identities. Native perpetual contract quantities converted to
BTC match the API: LONG0.0030 / SHORT0.0048. This checks the selected recent fills
and current perpetual quantities, not the entire historical account ledger.

Retrieved revision logs through 11:22:46 UTC contain no ERROR or scheduled child
restart. Before sampling, one engine probe timeout301.097ms and one controller
progress-overdue warning5.878s remain recorded. Later warnings are perpetual no-quote
reports consistent with its exposure cap; absence of an INFO recovery line does
not establish an exact recovery duration. This does not close the residual probe
root cause, thin-book71/120 failure, thirty-minute quote target, or full release/soak.


## Revision84 later incident: venue busy / unresolved submission (open)

At 11:32:01 UTC (18:32 local), Spot POST `/api/v5/trade/order` returns HTTP429,
OKX50013, "System is busy, please try again later." This is the specific initial
failure; the generic log advising an API-key/network check is less informative.
Shutdown then reports order-not-found51603, exits the engine, and subsequent
startup attempts stop at ownership `_check_intents` with unresolved submission.
At 11:39:33 UTC the API reports HALT, `owner_unavailable`, `StrategyStartFailed`,
startup/ready503 and live200. The short successful window above does not qualify
this later operating period.

Independent read-only OKX Demo checks at 11:40:21 UTC return51603 for the failed
client ID, zero pending BTC-USDT Spot orders, and no matching client ID in the
latest100 Spot fills. This limited evidence is not used to erase an intent or
reset HALT. The API recovery operation shown is an older failed check from07:34,
not a new verification of this incident. No recovery command was sent.

A separate perpetual private-WS disconnect1006 at11:30 has an automatic retry log;
its causal connection to the Spot50013 is unproved. There is no evidence here for
an OOM or deploy-triggered restart. Next investigation must reproduce the
50013 response -> durable intent/tracker transition ->51603 lookup -> restart
sequence, then determine safe reconciliation behavior without blindly resubmitting.


## Busy-send remediation: local implementation and qualification

The runtime issue is not the venue's busy response itself. The managed guard
retains a sent intent and HALT, but the generic connector catch previously emitted
OrderFailure/FAILED anyway. Native late acceptance/fill readback then lost the
normal create transition; executor failure callbacks could request another order.
Four real-PG/native-OKX tests reproduce that discrepancy on the parent runtime.
A separate native Red reproduces the cold host starting a strategy despite HALT.
The process test also catches early env-mode validation of a missing old YAML,
which prevented the recovery API from reaching startup at all.

The fix keeps the native tracker pending when the managed send gate reports
uncertainty, emits `order_submission_uncertain` with the original exception, and
retains the durable fence. Positive native status/fills still run through the
existing tracker/recorder. Not-found51603 remains an error, not a terminal event
or permission to retry. Proven-unsent/correlated-expiry failures and unmanaged
connector behavior retain their normal failure path.

A cold HALTed host now keeps its owner, observer, health and operation APIs alive
without constructing another trading strategy. Startup/live200 and ready503 mean
"service available, trading blocked." It skips old trading YAML/credential
validation while HALTed; a later reset and separate Start validate normally, with
fresh owner/control checks across awaits. An already-running reconciliation still
drains its known restored orders. Incomplete in-flight cleanup still forces the
original bounded process exit; the replacement is stable rather than looping.
Check reports `recovery_pending_intents` before position replay/KMS or exchange
proof when a pending intent exists. No automatic reset/financial repair is added.

**1,720 native tests + nine subtests and 126 PostgreSQL tests pass.** The PG cases
exercise the actual REST send gate and native OKX tracker/recorder with HTTP429,
not-found51603, late OPEN/partial/full execution, duplicate observation and exact
fees. A real shipping supervisor/API/engine accepts Stop while HALTed, reports the
pending-intent blocker, preserves the intent and owner identity, and does not
restart. This includes existing commit-outcome, ownership and recovery controls.

The broader native run initially found seven economic-replay fixture failures:
its LocalProvider lacked the already-existing book-recovery lease methods. The
controller and adapter source hashes matched the parent before that fixture fix.
The adapter now records/releases deterministic tape leases without introducing
network prices or weakening stale-data checks. Its 20 tests and the final full
native selection pass; those fixture errors are not counted as new trading bugs.

Tested Linux amd64 image `sha256:696c0d7d3d640e539ae59aae387da4639a507f0a64d759148b5b265126b70636`,
manifest `sha256:6138fba08427463068ce10df6df9d9e05d2cfd056511b230d3290d9f4ea6755b`,
overlays five verified runtime files on the prior maker candidate. Both packaged
`submission-uncertainty` and `maker-entry` E2E groups pass on that exact image.
The former injects four real HTTP429 responses without venue acceptance, exercises
51603 and engine replacement, then proves stable HALT, usable Stop, no additional
POST and preserved pending intents. All venue activity here is simulated.

This candidate is not yet deployed. The old Cloud intent/HALT is not repaired by
these local tests, and a recent empty exchange read is not sufficient to erase it.
Rollout and reviewed reconciliation of that incident remain open, together with
the prior thin-book failure, residual probe cause and full release/long soak.
