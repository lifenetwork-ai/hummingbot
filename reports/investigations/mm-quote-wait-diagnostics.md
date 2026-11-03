# Quote-wait diagnostics for operators

Date: 2026-11-03 (Asia/Saigon).
[Evidence](mm-quote-wait-diagnostics-evidence.json),
[plan](../../docs/plans/MM_QUOTE_WAIT_DIAGNOSTICS_TDD_PLAN.md),
[FE contract](../../docs/fe-integration.md#working-orders-fills-and-missing-order-warnings).

Missing-side activity now includes an optional typed `diagnosis` in both existing
status and liquidity responses: stage, evidence source, continuous sampled stage
duration, English guidance and an optional fresh proposal exception class.
Local pending create/cancel states and the executor-report event distinguish
pipeline waits from a completely unavailable explanation. Existing reason codes,
order counts, trading admission and risk checks remain authoritative.

For example, a missing BUY can report `stage=submission_pending`,
`source=order_tracker`, `observed_seconds=6` and guidance to wait for confirmation.
That local state does not prove transport or exchange acceptance. A persistent
wait reaching the existing warning threshold directs the operator to support;
there is no automatic HALT, restart or risk-limit reset from this diagnosis.
Known balance/limit blockers retain their separate guidance. When evidence is
insufficient, `stage=unknown` remains explicit rather than inventing a root cause.

Stage duration includes a missing side while the other side works. It counts
consecutive valid samples in the same stage/reason and resets on gaps, changed
owner/generation, restart, invalid observations, stage/reason changes and recovery.
It is not individual order age, exchange latency or measured downtime. Stop and
stale API projections clear diagnosis without rewriting the stored checkpoint.
Repeated API reads do not advance time. Legacy snapshots without the field remain
readable with null diagnosis; FE must also tolerate older images omitting it.

Proposal failures retain only `type(error).__name__`; no exception text is saved
in that diagnostic. Error classes expire with proposal freshness, and malformed
or unbounded class values are omitted. Invalid cached ages cannot create fictitious
wait duration. Existing WARNING `mm_no_quotes_warning` carries per-side stages in
the message and JSON `buy_wait_stage`/`sell_wait_stage` fields, retaining the same
once-per-minute limit and INFO clear event.

## Reuse and bounded work

Pending states are counted during the existing bounded tracker scan. The only
additional controller observation is the existing asyncio event's current value;
trading evaluation is never repeated. No service, endpoint, SQL query, migration,
exchange request, cache, environment setting, retained order history or background
task is added. The existing two-market snapshot stores only current diagnosis.

The Makefile now includes the new wait tests and the two preceding inventory/
withdrawal lifecycle modules in its canonical regression command. Those lifecycle
tests had passed manual broad checks but were missing from the explicit file list.
This is a test-target coverage correction, not a newly identified trading defect.

## TDD and verification

- Before implementation, 15 new controller/core cases fail while six existing
  cases pass; the two new HTTP cases also fail, and the structured-log assertion
  has its own Red. Seven additional validation edge cases cover cached ages and
  unsafe class values. Prototype Boolean-age handling is corrected by its Red.
  One test serializer initially omitted Decimal conversion; that fixture issue
  is corrected and is not counted as a production defect.
- Canonical `make test-cloudrun-regression`: **2,429 passed plus 11 subtests**.
  After final plain-English guidance edits, all **44 focused tests** pass again.
- Relevant real-PG activity/event/operations tests: **19 passed**, including
  typed JSONB round-trip, stale-owner invalidation, unchanged stored status and
  exactly one query per status/liquidity request.
- Swagger generation/check and formatting/lint/secret hooks pass. Existing
  dependency/deprecation warnings remain in the raw logs.

Raw evidence: `build/mm-quote-wait-20261103/`. This is source/API verification,
with HTTP exchange I/O mocked in tests. No current-image rebuild, packaged E2E,
fresh load/soak, connected Demo or deployment is claimed. The accepted51d4d879
image remains the prior artifact; rebuild before using these new runtime fields.
Historical probe root causes and continuous quote-availability limits are unchanged.
