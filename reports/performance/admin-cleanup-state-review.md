# Cleanup ownership, shutdown failure and snapshot projection

## Ownership loss: confirmed classification defect

The initial snapshot can legitimately be absent when an operation already ended.
Later snapshots are required before consuming positions or producing completion
proof. Previously, losing owner/control/operation authority at one of those
boundaries returned `None`, then raised a `TypeError` classified as `cleanup_failed`.

Commit `f9a44a2af` adds an explicit required-snapshot check and reports
`owner_unavailable`. No stale progress/removal is written, and no further position
is closed by that coordinator. Initial absence and error-path readback keep their
existing optional behavior. Four real-PG Red cases cover ownership changes after
cancel, a close, verification cancel and consumer detachment; Green includes
replacement-owner recovery without repeating either hedge close. A direct DB
generation change is the test stimulus; this does not itself emulate advisory-lock
or TCP loss. Related coordinator scope passes 17 tests.

A later audit also confirms the native `OwnershipLost` exception has no `.code`;
it was independently classified as `cleanup_failed`. Four additional Red cases
raise that actual exception at the same boundaries. Explicit classification maps
it to `owner_unavailable`; all eight owner-transition cases pass. This changes
reason reporting, not the send/ownership or intentional shutdown-restart fences.

## Shutdown failure: verify the existing restart fence

Commit `9e3a71f66` adds tests without changing shutdown behavior. Three PG cases
exercise persistent stop failure before removal, failure in `finally` after the
root committed, and stop failure while handling an exchange dependency error.

The original durable reason is preserved. Keys remain until verified removal;
an already committed success is not rewritten. With healthy shutdown and a new
owner, versioned Retry/re-entry finishes without duplicate closes or root audit.
Two additional ServiceHost cases prove that a failed cleanup task reaches the
intentional engine-restart fence, both with a pending operation and after root
completion, without starting another concurrent cleanup task. The host file passes
seven tests, of which two are new. PG shutdown cases use a fake exchange runtime;
they do not inject an actual connector shutdown failure.

## Measure before optimizing snapshots

The fixture has one pending order and one market-state record containing 256
synthetic tracking entries, approximately 1 MiB of JSON. Before any snapshot change,
the full reader executes five queries and returns 1,056,240 tracking bytes. Seven
local samples measured p50 21.12 ms / p95 26.37 ms and Python peak 2.155 MiB.

Coordinator progress/proof/error reads never consume `tracking_states`. Add the
private `include_tracking_states` option and opt those reads out. Full tracking
remains the default, so runtime preparation and restored-order reconciliation keep
their existing data. Row locks, owner/control/operation checks, live orders,
positions, scope limits and proof freshness are unchanged. No caching is added.

Paired seven-sample run on the same controlled fixture:

| Metric | Full/default | Coordinator projection |
| --- | --- | --- |
| SQL queries | 5 | 4 |
| Tracking JSON returned | 1,056,240 bytes | 0 |
| p50 | 22.74 ms | 10.34 ms |
| p95 | 24.31 ms | 11.89 ms |
| Python allocation peak | 2.154 MiB | 0.056 MiB |

This is a synthetic large-checkpoint measurement, not normal-load latency, process
RSS, MM tick latency or an explanation of earlier probe outliers. Timing is
reported, never used as a pass/fail threshold. The first new projection test fails
before implementation; after implementation, projection scope passes 12 tests.
An additional coordinator query-capture test verifies that progress/proof reads
actually omit the tracking query. Final combined targeted scope passes 33 tests.

Authority tests cover both full and projected snapshots after owner, control,
desired-state and active-operation changes. A concurrent `FOR UPDATE NOWAIT` reader
gets SQLSTATE `55P03` while either snapshot is paused inside its ledger read, then
succeeds after completion. This verifies the control lock is retained; the change
does not remove the ownership fence to improve timings. Projected/full position,
order, receipt and state values match except the separately sampled DB clock.

## Intermediate regression; final-source/image verification pending

Intermediate source `7a4089a61` passes full native regression: **2,721 tests plus 11 subtests**.
Related PostgreSQL scope passes **124 tests**. Twenty-two cases are added across
those phases (four owner-transition PG, three shutdown PG, thirteen snapshot PG and
two native host-fence cases). The earlier targeted/benchmark runs overlap these
counts and are not additive. Rebuilt-image cleanup recovery remains pending. No
new image, actual OKX, external Google/GCP, existing-Demo, full release or hour-soak
acceptance is claimed by these source results. Four further native-exception PG
cases extend the new coverage to 26 cases; final-source broad/image checks remain
pending after that classification fix.
