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
