# Submission-block logging clarification

Date: 2026-11-01. Status: source change and targeted regression complete.

The connected Demo protocol logged six executor ERROR records for locally
blocked orders. Independent OKX reads found no corresponding exchange orders.
The connector also emitted a generic API-key/network warning. That wording
obscured expected admission decisions during Stop, session changes and market
readiness waits.

## Behavior

| Failure | Level | Structured reason |
| --- | --- | --- |
| Stopped/shutting down, superseded control, expired/changed session authority | INFO | `session_submission_closed` |
| Required market/data/exposure readiness is false | WARNING | `market_not_ready` |
| Other guard/recovery failures or missing authority/readiness wiring | ERROR | `submission_safety_blocked` |
| Lost ownership | ERROR | `ownership_lost` |

Two specific subclasses of `SubmissionBlocked` distinguish expected session
closure and readiness waits. Existing guard catches still catch them. Both the
connector and position executor use the failure type already carried through
`OrderUpdate` / `MarketOrderFailureEvent`; no event schema or API field is added.
Logs include `event=order_submission_blocked`, scope, reason code, exception type,
order ID and the English reason. Blocked messages no longer blame API keys or
network connectivity or say “Retrying” as though another send is guaranteed.

Unsafe state takes precedence over routine Stop: HALT, pending mutations and
configuration mismatch retain ERROR. Missing readiness wiring is not classified
as a temporary market wait. Unknown failures stay errors; there is no message-
substring classification. Ordinary transport/exchange failures keep their
existing network diagnostics and executor ERROR messages.

Order states remain FAILED, failure events still propagate, failed-order lists
and retry counters are unchanged (including the existing take-profit behavior).
No admission condition, transport permission, accounting, retry schedule,
cleanup or recovery rule is relaxed. A safety-block message does not prove that
an earlier transport had no effect; uncertain effects still require recovery.

## Verification

- Initial Red: 19 failures / 10 passes reproduce missing severity/reason
  classification and expected session blocks being treated as ordinary errors.
- Additional Red: 1 failure / 36 passes catches missing readiness wiring being
  incorrectly classified as a temporary WARNING. The final implementation keeps
  that case at ERROR.
- Green: 239 native tests plus 4 subtests pass across new logging tests, shutdown
  authority, structured service logging, position executor, order tracker, OKX
  spot and OKX perpetual. The new module contains 37 regression cases.
- Disposable PostgreSQL: 50 passed / 229 deselected for ownership, pre-intent,
  submission and shutdown cases. The persistent Demo database is not used.
- Flake8 and diff checks pass. The new regression module is included in
  `make test-cloudrun-regression` so future release runs retain this coverage.

Raw logs: `build/submission-logging-20261101/{red,authority-red,native,postgres}.log`.
The existing Pydantic deprecation warnings remain unchanged.

This is source-level verification, not a new image build, connected Demo run or
hour-load gate. The canonical Demo service remains on image `3f5cbd0f`, trading
stopped, with its existing acceptance scope. A future candidate must package
these changes before its runtime logs have the new behavior. No public exchange
orders, configuration changes or Cloud Run deployment are part of this change.
