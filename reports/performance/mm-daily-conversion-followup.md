# Daily fee aggregation and remaining availability investigation

2026-11-04 (Asia/Saigon). **Source regressions pass; new-image load and
connected Demo acceptance are pending.** Earlier failed results remain in the
[controller/persistence report](../release/mm-controller-persistence-release.md).
[Evidence](mm-daily-conversion-followup-evidence.json),
[checklist](../../docs/plans/OKX_DEMO_CURRENT_IMAGE_ACCEPTANCE.md#reportingprobeguard-follow-up--requested-2026-11-04).

## Confirmed redundant work and source change

The original fill aggregate inlines its conversion predicate into three aggregate
filters. A real PostgreSQL Red regression performs 600 original-fee array parses
for 200 zero-conversion fills. The candidate computes nonzero-converted and
zero-fee-converted counts once in the existing daily aggregate, then derives the
three result fields from those small daily totals. The Green query executes 200
parses. There is no additional full-history materialization or aggregate pass.

The USDT sum excludes other quote assets. Invalid zero conversions cannot alter
its numeric total; an explicit validity count retains NULL when no conversion is
valid. Regression coverage includes mixed valid/missing/zero/foreign conversions,
rebates, exact original-currency amounts, late fills, date boundaries and a single
repeatable-read database snapshot. Response fields and trading authority stay
unchanged. No cache, index, migration, dependency or runtime timeout is added.

## Paired disposable-database measurement

PostgreSQL 14 is limited to 2 CPU / 512 MiB. Five randomized-order paired samples
after warmup are retained for the first three cases; the last uses three. Every
returned value is exactly equal between the original and candidate SQL. These
are diagnostics on one host, not production latency promises.

| Fills / fees | Main fill query before → after, median ms | Complete daily read before → after, median ms |
| --- | ---: | ---: |
| 100k / repeated, nonzero conversion | 323.85 → 301.26 | 775.82 → 773.78 |
| 100k / diverse, zero conversion | 2,250.71 → 1,026.90 | 3,041.33 → 1,899.29 |
| 1M / repeated, nonzero conversion | 2,764.67 → 2,733.73 | 8,448.79 → 7,642.99 |
| 1M / diverse, zero conversion | 26,371.55 → 12,246.17 | 36,764.15 → 23,625.69 |

Diagnostic query deadlines are 90s; bulk fixture setup alone allows 300s after
initial setup-only deadline failures. Shipping statements retain their 2s limit
and 3s I/O deadline. The million-row cases remain beyond that runtime budget.
Two exploratory shapes were not retained: adding classification groups and a
per-row projection boundary did not establish a safe normal-workload benefit.

The old soak timeout used **nonzero** fee conversions, so eliminating repeated
zero-conversion decoding does not establish that historical timeout's root cause.
Plans also show temporary-file sorting in some cases. No global memory/timeout
setting or guard threshold is changed to make the gate pass.

## Independent reference freshness observation

A separate read-only connection subscribes to the same Demo `books` channel for
BTC/ETH/AVAX for 180s, without account access or mutations. It receives 88 AVAX,
401 ETH and 563 BTC messages. AVAX has a 10.96s maximum receive gap and six of
359 point samples exceed the current freshness policy; maximum measured server
book age at spot UID precision is 11.71s. ETH has no stale samples and its maximum
receive gap is 1.55s; BTC also has no stale samples. The public clock response
bounds server-minus-client offset between -0.007s and +0.228s.

This reproduces a real input condition for `stale_reference:okx:AVAX-USDT` even
outside the service. It does not prove the cause of every historical wait, a
continuous outage duration, or the source of `inconsistent_own_depth`. A bounded
private/public observation witness is prepared for the next approved Demo trial;
qualification thresholds and all order guards remain intact.

## Acceptance still pending

Native regression passes 2,442 tests plus eleven subtests. The first full
PostgreSQL attempt is stopped after the unchanged original-currency fee query
hits its 2s deadline in the 100k measurement test; 49 other tests passed. Image
C++ compilation overlapped that attempt. This is not a demonstrated quantity or
fee mismatch, and overlap alone does not establish causation. The complete suite
subsequently passes 345 tests without concurrent compilation; the failed log
is retained and a unique historical timeout cause is not established.

A newly built/verified image, packaged E2E, a traced
MM/probe diagnostic, bounded spot-first/both natural-fill trial with restoration,
and the unchanged 60-minute zero-degraded-response gate remain pending. The
prior probe spike and uninterrupted quote coverage are not declared fixed.

Raw diagnostics/helpers: `build/reporting-followup-20261104/`. Discarded or
interrupted experiments are preserved there and are not passing gates.
