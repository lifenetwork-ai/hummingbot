# MM feed and quote recovery

Scope: offline TDD for per-book public snapshot recovery, fresh preparation before
the first durable entry intent, and bounded operational diagnostics. No Cloud
deployment, real exchange writes, inventory/cap/fee-floor/freshness changes.

- [x] Red: a quiet ETH book recovers while BTC WS continues; fresh BTC causes no
  REST fetch. Use native controller/provider lifecycle with simulated books.
- [x] Lease recovery to active LIFE controllers; at most two snapshot reads,
  one per book, bounded timeout/backoff and one pending snapshot per book.
  Publish through the existing tracker snapshot stream, using venue timestamps.
  Reject stale/future/regressive REST and handle WS recovery both during the
  request and after enqueue, at the final tracker consumer.
- [x] Prove consumer sharing, release/reacquire, cancellation and provider drain;
  Stop cannot publish a late snapshot or retain old controllers/tasks.
- [x] Red/Green: prepare a queued managed entry from fresh policy before its
  first order identity/intent. Preserve executor/session/level and sizing bounded
  by the original base/notional budget; stale/revoked conditions
  retire without submission. Existing retry/intent prices remain immutable.
- [x] Keep final transport checks; late price changes still reject safely.
- [x] Type expected drawdown valuation unavailability; keep status unready,
  emit bounded warning summaries and recovery, retain unexpected ERROR traces.
- [x] One connector owns quote-refresh warnings; preserve executor bookkeeping
  and bounded diagnostics without two WARN lines per rejected order.
- [x] Native/PostgreSQL checks and packaged offline E2E for quiet WS/fresh REST,
  stale REST/failure, WS return, fresh quote preparation and Stop; targeted load.
- [x] Record failed/passing attempts separately. Actual OKX/Cloud effectiveness
  and a new full release gate remain distinct from simulated offline evidence.

Result: 903 native tests + 4 subtests, targeted PG9, packaged E2E4 and 120-second
diagnostic load pass on image `1d3c3a6703b9…`. Nine runtime overlay hashes match.
One measured probe timeout recovers; its cause remains open. Connected Cloud
validation and a new full release/long soak are outside this offline completion.
See [report and evidence](../../reports/performance/mm-feed-quote-recovery.md).
