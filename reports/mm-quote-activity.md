# Quote activity implementation evidence

2026-10-27. Implements [the TDD plan](../docs/plans/MM_QUOTE_ACTIVITY_TDD_PLAN.md).

## A — Proposal diagnostics

Five new cases failed before implementation. Per-side diagnostics now record the
existing proposal filters: quote/base balance, inventory, outflow preview,
allocation and order price/size/spread rules. Counts are proposals, not exchange
acceptance. Original PMM level selection remains authoritative; active executors
and cooldown are reported from that result. Outflow preview is still called once
per proposed BUY. No extra exchange or database reads were added.

The first green attempt exposed a perpetual test fixture using seconds instead
of that connector's millisecond book IDs; fixing the fixture allowed the original
freshness guard to pass. Final selection: 125 controller/spread/inventory tests
passed, including fresh reasons after balance recovery and unchanged upstream
active-level/cooldown behavior. Logs remain in `build/admin-operations/`.
