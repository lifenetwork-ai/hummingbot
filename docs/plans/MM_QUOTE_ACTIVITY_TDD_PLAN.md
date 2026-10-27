# Quote activity and missing-quote diagnostics — TDD

Expose actionable English operator status without changing quote policy, account
limits, session timing or recovery authority. Reuse controller proposals, the
existing order tracker, liquidity checkpoint and service incident logger.

- [x] A — Red/Green per-side proposal diagnostics: balances, exposure, outflow,
  allocation, price/size constraints, active executors and cooldown. Diagnostic
  counts mean proposed actions, not exchange acceptance.
- [x] B — Red/Green observed working-order/fill activity and bounded continuous
  no-quote warnings. Unknown data, gaps, restart, step changes and stop must not
  accrue absence time or show a stale alert as current. Use a deployment setting
  `MM_NO_QUOTE_WARNING_SECONDS` (default 120; 0 disables warnings).
- [x] C — Publish through existing status/liquidity APIs and Swagger, reuse
  warning/recovery logs, update FE guidance and ownership docs. Run regression
  and disposable PostgreSQL HTTP checks, record scoped evidence and commit.

Fill observations describe current acknowledged working orders only, not the
session's historical fills. Existing `/fills` remains recorder history. A lack
of fills does not trigger restart, risk-limit changes or trading actions.

Verification complete: 1,661 native tests plus 11 subtests, a final 42-test
capture/controller/API selection, and 18 PostgreSQL integration tests pass.
Status/liquidity each retain one state query and do not mutate control state.
Swagger and changed-file checks pass. See [report](../../reports/mm-quote-activity.md)
and [portable evidence](../../reports/mm-quote-activity-evidence.json). This scope
does not include a new image build, load/soak, exchange, browser or Cloud Run run.
