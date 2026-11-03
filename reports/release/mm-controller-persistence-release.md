# Controller/persistence image verification

2026-11-04 (Asia/Saigon). Status: **60-minute soak failed its zero-degraded-response gate**. Image
`sha256:f1a7eead89fb43f7ae9b126d5258eb991e15577f8cf31f4a292ca29a2dfdfcdf`
was built from source `4a38bbf095c7be815515c60b5543f95ca814257e`; seven changed
runtime files match their source hashes. The earlier image's acceptance does not
transfer to this image. Raw evidence is under
`build/controller-persistence-release-20261104/` and the referenced
`build/mm-load/` directories.

## Offline probe and controlled comparison

The 600-second diagnostic **failed** the declared read p99 and auxiliary read
p99 budgets: p95 96.05 ms, p99 2,932.76 ms, auxiliary p99 2,818.15 ms. All
12,000 primary and 3,000 auxiliary reads returned 200; 600 synthetic fills
matched recorder and positions. The run included incident/MM instrumentation,
three extra operator reads/s and all-feature volatility-defense fixtures.

One engine probe-recovery interval was approximately 10.05 seconds, below the
15-second watchdog failure grace. During that observed interval the captured
maximum tick gap was 1.60 seconds and controller gap 2.00 seconds; discrete
venue samples retained at least nine orders. Another interval contained one
empty venue sample. A supervisor interval is not quote downtime. Sampling and
bounded/filtered traces cannot establish continuous quoting or a single root
cause. One submission exception occurred before the measured window and was
`SessionSubmissionBlocked` during a control-version change.

Two sequential 300-second runs used the same host, fixture/source hashes,
PostgreSQL image, resources and instrumentation. The prior image passed with
p99 138.60 ms; the current image passed with p99 123.55 ms. Each matched 300
synthetic fills and positions, with zero unexpected API errors. Their 197 Python
package name/version pairs match. These single repetitions do not explain the
first failed window or prove the new source cannot produce the same issue.

Both comparison runs contain discrete samples with no venue orders during
refresh: old 5/298 and new 7/298 samples, each with a longest consecutive empty
sample span of approximately 1.0 second. A 1 Hz sample does not prove continuous
absence between samples or exact downtime. The new 600-second run had 5/596
empty samples and a longest sampled span 2.01 seconds. No uninterrupted quote
coverage claim is made.

## Connected OKX Demo

Read-only packaged-image REST/public and private WS preflight passes, both keys
bind the same Demo account, no pending orders or unresolved intent/HALT, and
long/short units match PostgreSQL. A stopped upgrade/restart keeps all four
order/fill/position/executor hashes and private config hashes unchanged; the
previous container is preserved stopped. No manual ledger repair or HALT reset.

The fixed BTC-USDT matrix passes nine spot/perpetual/both/profile combinations,
Random advances from `follow_token` to `sideways`, and active spot Random and
both-market restarts preserve session/anchors/deadlines and recover quotes.
Independent OKX reads confirm all 496 recorded orders canceled, exact original
quantity/price, post-only; no new natural fills occur in that phase. Positions,
prior history and spot cash match; no ERROR/CRITICAL appears in structured logs.

The separately authorized 40-USDT-per-market one-level maker preset runs for
1,200 seconds and restores the original private catalogue. Independent reads
confirm 662 terminal orders (659 canceled, three filled). Three natural
perpetual maker fills match trade IDs, base quantities, prices and original
fees; no positive spot fill occurs, so current-image spot fill/fee and
filled-active restart acceptance remain open. The restored stopped service has
no pending orders or HALT and positions match OKX.

Availability observations are retained. In Random, sampled
`waiting_for_mm_readiness` included `stale_reference:okx:AVAX-USDT` and
`guard_recovering`, with subsequent recovery. The configured freshness default is
10 seconds; the cause of AVAX book age is not established. In the temporary
one-level preset, 12 of 77 samples at approximately 15-second intervals were
waiting; eight samples reported perpetual `inconsistent_own_depth`, followed by
recovery. Sampling counts do not establish exact outage duration. Do not disable
the guards or infer exchange/source causality from these observations.

## 60-minute offline soak and restoration

The standalone 3,600-second random soak **fails** the declared zero-error gate.
API read p95/p99 are **117.75/617.07 ms** and all 72,000 primary HTTP responses
are 200, but 13 daily-statistics responses are explicitly `stale` over 4.53 seconds.
The 7,200 auxiliary responses are 200, p99 552.75 ms.

PostgreSQL logged **one cancellation of the daily fill-aggregation statement at
22:13:18 UTC because `statement_timeout=2000 ms`**. The API caught the resulting
`OperationalError`, returned its bounded last-good observation and retried; the
13 degraded responses do not represent 13 distinct failed SQL queries. The
response was labeled stale, not silently presented as fresh. This establishes the
immediate cause of the degraded responses, not why that query took over two
seconds or the separate 600-second probe's p99 cluster.

The independent simulator and recorder agree on **3,594 unique fill IDs,
quantities, prices and fees**, with matched positions and funding-bill records.
Six fewer fills than 3,600 were produced; the Random fixture explicitly allows
step-admission gaps, and rate is reported as observed rather than a fixed-profile
rate gate. Five fills across spot and perpetual occurred during the 4.53-second
degraded-response interval. This shows some trading progress during that
interval; without tick/quote tracing in this soak it does not prove no impact on
engine latency or uninterrupted quoting.

All 58 runtime samples report running; engine/API/supervisor PIDs do not change,
and no child restart appears in the window log. There are **63 recovered engine
and three recovered API probe-timeout episodes**, longest observed recovery
intervals 2.612 and 1.107 seconds respectively. One 1.36-second engine probe
recovery overlaps the daily timeout; correlation does not establish cause.
Adjusted memory retention grows **29,605,888 bytes (28.23 MiB)**, within the
64 MiB gate; observed cgroup peak is approximately 701.55 MiB. These results do
not establish the absence of leaks beyond this window.

The soak samples Random `follow_token` and `sideways`; `follow_market` was
covered in the nine-case Demo matrix, not selected in this particular soak.
The standalone soak is not a complete multi-window release/fault gate.

Afterward, the stopped Demo service and PostgreSQL were restored. An independent
network-isolated PostgreSQL clone restored from the pre-soak private backup
matches post-restore hashes for orders, fills, positions and executors. A fresh
read-only packaged-image OKX preflight confirms no pending orders or HALT and
perpetual positions match PostgreSQL. Private configuration remains unchanged.

[Portable evidence](mm-controller-persistence-release-evidence.json) preserves
both failed and passing scoped results. Open items are positive current-image
spot fill/fee evidence; the first probe's p99 cause; repeated reference/guard
waits; and a clean 60-minute zero-degraded-response run if that unchanged gate
is required. Do not extend the global database timeout or weaken guards without
measuring the effect on the trading engine.
