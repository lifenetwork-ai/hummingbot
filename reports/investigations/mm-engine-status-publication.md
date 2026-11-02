# Engine status-publication responsiveness

Date: 2026-11-02 (Asia/Saigon). Source verification in progress; the new runtime
has not yet received image/load acceptance. Baseline image `39198499` retains
its completed release scope. [Plan](../../docs/plans/MM_ENGINE_STATUS_PUBLICATION_TDD_PLAN.md).

## Confirmed path

The deeper review finds frequent retained stacks under observe_once →
publish_status → BotStateStore.publish/AdminStore.record_observation → bounded
PostgreSQL wait. In the existing healthy envelope, the retained publication
frames occur repeatedly; these are bounded contextual samples, not unique-event
counts or proof that every historical probe has this cause.

The legacy publication is synchronous on the trading event loop. A deterministic
native Red shows a slow status write finishing before the loop can service its
scheduled health task. A stronger legacy-source PostgreSQL replay injects 400ms
only into the status-publication SELECT: an already-started external HTTP probe
with the shipping 300ms deadline returns TimeoutError. This establishes a
sufficient cause for a probe miss without changing trading/ownership logic.
The first HTTP replay accidentally scheduled its client only after the blocking
call; that false-negative test is retained separately. An explicit client-ready
barrier fixes the test and produces the retained Red.

## Runtime change

Publication captures and normalizes a detached JSON snapshot, identity, version
and daily-checkpoint decision on the event loop. Only the unchanged store's SQL
transaction runs through asyncio.to_thread. The observer awaits the committed
result; errors still propagate and fenced rejection still does not advance the
checkpoint cadence. Connector/strategy state and availability updates never run
on the DB worker.

One async admission lock plus one retained physical task/context bound the work.
Cancellation shields the physical write; close stops admission and drains it
before final status and owner release. A failed physical write prevents clean
release. The existing executor, pool, SQL version/generation fences, receipt
transaction, query/IO/probe deadlines and restart grace are reused unchanged.
Fill/intent acknowledgment and exchange mutations remain on their original
paths. There is one asynchronous publication method, not a parallel legacy mode.

Three initial Reds cover loop blocking and async lifecycle/cadence. A separate
candidate concurrency Red detects overlap between queued drainers; serialized
admission fixes that before packaging. It is not labelled a pre-existing legacy
concurrency bug. A second prototype race allowed cancellation on the same
loop turn as a failed physical write to clear its retained result; its own Red
now verifies that shutdown still sees the failure and retains ownership.
Eight focused publication cases and the existing service tests
pass after the change; targeted PostgreSQL 40 and real HTTP/CAS 2 cases pass.
The final native regression passes 2,279 tests plus 11 subtests. The final-source PostgreSQL regression passes all 284 cases, including the
HTTP/CAS additions (the driver also receives explicit file arguments, but its
normal directory collection still runs the complete suite). Swagger passes. Four failures
in the first broader CLI attempt were synchronous test doubles for the newly
async method; converting those doubles to AsyncMock preserves their original
HALT/send-gate assertions. The failed run remains retained.

The prior accepted local image was externally removed. Packaging will use the
current full Dockerfile and existing dependency cache; it cannot be described as
a derivative of the missing image. Verify packaged source hashes and imports,
then targeted image cases, matched 600s ordinary diagnostic and unchanged full
R1/R2 before making any new artifact-acceptance claim.

## Scope limits

Other owner/recorder/commit calls can still block the loop within existing DB
limits. This change does not establish the unique cause of every historical
probe, guarantee uninterrupted quotes or create a new production acceptance.
Raw original/counterfactual/Green logs: `build/engine-publication-20261102/`.
Demo configuration, volume and account are not modified in this follow-up.
