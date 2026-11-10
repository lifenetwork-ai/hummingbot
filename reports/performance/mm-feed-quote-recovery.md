# MM feed / unsent quote recovery — 2026-11-10

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

The new maker fix is local, not deployed to revision83. Actual Cloud effectiveness,
residual own-depth/probe behavior and full release qualification remain open.
