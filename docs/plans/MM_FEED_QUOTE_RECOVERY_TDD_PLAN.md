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

## Cloud revision83 follow-up: maker bound / native cancel

- [x] Read actual revision/image, status, quotes and independent OKX order truth.
  Preserve the failed quote window:15/18 ready reads, no HALT, sparse Spot SELL,
  no Perpetual quotes under the existing cap. Four native zero-fill cancellations
  have cancelSource31. Some ready503 reads have fresh books but inconsistent own
  depth; do not relabel them stale feed or declare all guards fixed.
- [x] Red9 native cases reproduce the configured-price maker-bound gap and
  zero-cancel retry. The affected methods predate7372; source comparison retained.
- [x] Apply native BUY min(model,best bid)/SELL max(model,best ask) before identity
  creation, preserving original and current quote/base ceilings and bootstrap.
  Retire a confirmed zero-fill managed maker cancel via normal hold/shutdown,
  including native cancellation before created/ACK; retain partial/unknown paths,
  fill history and explicit Stop priority.
- [x] Identical configured fee-floor fixture/harness: image1d3c fails to restore
  both levels/sides in20s; image2d4d passes with two true cancelSource31 events.
  Preliminary harness assertions and a no-fee fixture pass are not runtime Red.
- [x] Complete affected native/PG, bootstrap/spread image regressions and120s
  load with actual post-only enforcement enabled; record scope, commit and keep
  the deployed service unchanged until a separately verified rollout.

Outcome:670 native +2 subtests, two separate depth controls, PG9 and three
packaged E2E groups pass. Post-only-enforced thin-L1 load remains failed71/120;
independent L2 healthy input passes120/120 with unchanged rates/guard budgets.
No unchanged-input or full-release pass is claimed. Revision83 retains its historical
failed readback.

- [x] Verify the user-deployed revision84/source860314 read-only against actual
  OKX Demo: all18 health samples pass, Spot SELLs work, 20 native fills match
  quantity/price/original fees, current perpetual quantities match. No HALT or
  scheduled engine restart observed. The existing perpetual cap remains binding.
  See connected readback in the report/evidence; this short window does not close
  full release/soak, thin-book availability, two-sided qualification or probe cause.


- [x] Investigate and reproduce revision84's later18:32 local incident: Spot HTTP429/OKX50013,
  shutdown51603, unresolved intent and HALT on restart. Preserve the earlier
  passing short window as historical; reproduce the full lifecycle before a fix
  or recovery. Current native GET shows no pending Spot order, but is not taken
  alone as permission/evidence to clear durable state.


- [x] Retain pending native tracker state after an uncertain managed send; do not
  emit false OrderFailure or automatically retry. Preserve unknown intent/HALT;
  late OPEN, partial/full fills and duplicate observations reconcile normally.
- [x] Cold HALT keeps owner/observer/startup/live/control/recovery available, ready
  remains503, and no strategy is constructed. Removed old YAML/credentials cannot
  block recovery bootstrap. Existing warm reconciliation/drain rules remain.
- [x] Check reports pending intent before replay/KMS/venue proof; a51603 response
  alone never clears intent or HALT. Test real API Stop with a current MM session.
- [x] Final validation:1,720 native +9 subtests, PG126, two packaged E2E groups;
  five current runtime source hashes match image696c… / manifest6138… .
- [ ] Roll out this remediation and reconcile the old Cloud sent intent using
  reviewed complete evidence. Current Cloud HALT is not reset by local tests.
  Full release/load/long soak and prior thin-book/probe qualifications remain open.
