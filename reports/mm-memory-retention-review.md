# Long-running MM memory retention review

2026-10-28. Source review, Red/Green fixes and a completed ten-minute image diagnostic. The earlier one-hour image soak had
28.59 MiB first/last median RSS growth and passed its 64 MiB budget. That was a
historical image and did not prove the current code free of retained objects.

## Confirmed issues and fixes

1. `EventLogger.wait_for()` left notifiers, and sometimes delivered events,
   referenced after timeout/cancellation. `finally` now removes both entries,
   including cancellation between notification and resumption. Weak-reference
   tests first failed for all three abandonment paths, then passed.
2. Connector event log fill history was unbounded. The headless service now
   requests a 1,000-fill recent diagnostics log; generic events retain their
   existing 50-event bound. PostgreSQL/API history is complete and unchanged.
   Interactive/file mode keeps its legacy full-history default. The connector
   refuses truncation if snapshot-based balance adjustment or an asset budget
   needs full fill history; the service logs `connector_history_unbounded`
   at WARNING and preserves that calculation rather than silently changing it.
3. PostgreSQL OKX recorders continuously populated legacy reconstruction sets
   of fill/order IDs that neither OKX connector reads. This path now skips both
   startup warming and incremental cache additions. In-flight tracking and
   PostgreSQL deduplication/commit guards remain authoritative. SQLite and other
   connectors keep their original reconstruction path.
4. Position holds accumulated retired client-order IDs indefinitely. IDs now
   cover resident executors only; successfully persisted retirement releases
   their identities while keeping aggregate amount/fees/PnL. An independent
   dirty flag preserves checkpoint safety after IDs leave memory. Processed
   executor IDs now cover all resident holds, replacing the 50-entry recent
   cache that could forget still-resident executors.
5. Individual executor persistence failure previously still removed the executor
   from memory. It now retains execution/deduplication state for retry. A new
   regression reproduces that loss before the fix.

Native fixtures initially used an int event tag where `trigger_event` expects
an enum, and a mocked executor had invalid financial fields. Those fixtures
were corrected; no production validation was weakened. The final complete
regression passes **1,688 tests + 11 subtests**; the complete disposable PostgreSQL
suite passes **215 tests**. Red results remain in ignored `build/memory-review/`.
No schema migration, persistent-record pruning, production GC loop or process
restart workaround was added.

## Accelerated component allocation experiment

The same compiled implementation was exercised with unbounded legacy retention
and the explicit service diagnostic limit, using tracemalloc plus collection
between checkpoints. Each stream injected 100,000 distinct fill events.

| Stream | Retained fills at 100k | Traced Python bytes at 10k | At 100k |
|---|---:|---:|---:|
| Legacy unbounded control | 100,000 | 6,268,574 | 62,811,494 |
| Bounded diagnostics | 1,000 | 629,844 | 631,396 |

The bounded component grew only 1,552 bytes after warmup in this experiment.
This is Python-heap retention of this component, not total bot RSS or proof of
unlimited uptime. The legacy full-history mode intentionally retains its records.

## Remaining behavior to watch

Outstanding/lost orders and genuinely open exposure cannot be discarded just
to make a memory graph flat. The order tracker's terminal cache is already
limited to 1,000 / 30 seconds, daily reporting to 32 TTL entries, candle windows
to configured records, and controller/provider tasks have awaited cleanup.
Allocator high-water RSS may remain elevated after objects are collectible.
The special full-history balance modes above remain an explicit growth risk.

## Additional lifecycle issue exposed by the first image probe

The first image `15c48fe0b649` did not pass the memory run. Its explicit Stop
arrived during a new random step's readiness/reconciliation reads; cancellation
then incorrectly created a durable recovery HALT, preventing the next Start.
The run was interrupted and retained as failed evidence under
`build/memory-review/20261028-022433/`. Samples after HALT are not accepted as
healthy memory stability evidence.

Two further Red tests reproduced cancellation during readiness and an open-order
GET. Reconciliation now distinguishes those read-only cancellations from an
interruption after a cancel request may have reached the exchange. The former
keeps send authority closed and leaves prior HALT/intents untouched; the latter
still requires recovery. Timeouts, read failures and economic mismatches keep
their existing failure behavior. New real-PG cases cover both branches and an
already-halted state. This is a lifecycle correction discovered by the memory
workload, not a memory-budget waiver or automatic HALT reset.

After this additional correction, the complete native regression passes
**1,691 tests + 11 subtests**, and the full disposable PG suite passes
**219 tests**. The four new PG cases preserve existing HALT, distinguish
read-only cancellation from a potentially dispatched cancel, and keep send
authority closed. The failed image run remains excluded from acceptance; the final run below uses the correction.

Optional external notifier queues are not bounded by this patch; attaching a
notifier whose consumer falls behind needs its own backlog/delivery policy.
This workload attaches no external notifier. Rolling outflow projections retain
fills within their configured time window, so their capacity scales with fill
rate and window length even when expiry is correct. Those are capacity/configuration
considerations, not evidence that all possible plugin/config combinations are
leak-free.

## Final image diagnostic — passed

The rebuilt amd64 image `d8e084adc307` passed a 600-second run on the arm64 host
with 1 CPU / 2 GiB for the bot and 100k historical orders plus 100k fills. Five
changed Python runtime sources byte-match the image; two compiled extension
fingerprints are recorded. Native and image allocation experiments agree.
The test entrypoint adds bounded telemetry; this is diagnostic evidence, not a
new unchanged full release soak or an uninstrumented production RSS measurement.

| Measurement | Result |
|---|---:|
| Original UI reads | 12,000 / 12,000 |
| Auxiliary UI reads | 3,000 / 3,000 |
| Unexpected HTTP errors | 0 |
| Original p95 / p99 | 191.2 / 479.1 ms |
| Auxiliary p95 / p99 | 170.6 / 469.3 ms |
| Peak bot RSS | 329.8 MiB |
| First/last two-sample median RSS growth | 10.7 MiB (budget 32 MiB) |
| Exact recorder / exchange fills | 599 / 599 |
| Position comparison | Matched |
| Strategy / provider instances after Stop + diagnostic GC | 0 / 0 |
| Async tasks after Stop | 2 |

Nine minute samples showed active task counts 58–67 (39 at the intentional Stop),
and only one or two live strategy/provider instances despite repeated replacement.
No forced GC occurred during the measured window. ID counts remained within
resident executor/order counts, and unused OKX reconstruction caches stayed zero.
The explicit Stop/Start and both Switch commands succeeded without HALT. A live
checkpoint before completion recorded 18 step starts and all three profile types.
Final collection was requested only after the measured window and Stop; it tests
collectability and is not a production memory-management workaround.

RSS rose gradually rather than staying perfectly flat. The bounded instrumentation
also warms rolling sample deques, and Python/native allocators can keep freed
pages. These samples do not prove that every byte of RSS growth is explained or
exclude a slower leak over days. Deterministic retention tests and object cleanup
support the narrower conclusion that the confirmed unbounded references above
are fixed in the exercised service path.

Four ERROR logs remain from private-stream position snapshots arriving before
contract-size initialization. Later REST reconciliation and exact position checks
passed; no HALT occurred. This startup ordering/logging issue is recorded as a
separate follow-up, not suppressed or counted as zero runtime errors. “Zero
errors” in the table specifically means unexpected HTTP/load-client errors.

See [portable evidence](mm-memory-retention-evidence.json) and the
[completed checklist](../docs/plans/MM_MEMORY_RETENTION_REVIEW.md). Raw artifacts
remain in `build/memory-review/20261028-024013/`. No real exchange, cloud deploy,
new sixty-minute release gate or multi-day uptime verification was performed.
