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

## B — Sampled activity and operator API

The existing liquidity checkpoint now contains per-market activity: working
orders on both sides, one side, no orders, idle or unavailable. BUY/SELL details
reuse the actual proposal diagnostics. Five-second-expired proposal reasons are
not repeated as current. Working-order fill observations explicitly cover only
current acknowledged limit orders, not historical session fills.

Continuous complete order absence is measured only between valid samples at
most five seconds apart. Owner/generation changes, restart, gaps, unavailable
data and stop break the interval. Any working side resets total-absence timing.
`MM_NO_QUOTE_WARNING_SECONDS` defaults to 120; 0 disables warnings. WARNING and
INFO events reuse the existing logging pipeline, with per-market throttling and
session identity. Clearing a warning on unknown data is not labelled as recovery
of actual orders. API reads do not advance duration or execute corrective actions.

Seventeen activity/config cases failed before implementation, then the initial
40-test selection passed. Two HTTP cases first failed, then the 46-test HTTP/core/
configuration selection passed. Final focused capture/controller/API/availability
selection passed 42 tests, covering actual controller-to-observer wiring with no
new source I/O. These selections overlap the broader regression.

Review caught an important wording issue: a spending-preview denial can mean
missing coverage, fees or pending terminal proof, as well as a ceiling breach.
A failing regression now requires `spot_outflow_blocked` instead of claiming
`spot_outflow_limit`; operators inspect the existing spending-policy details for
the specific cause. Another Red/Green HTTP case replaces jargon in the operator
label with “No working orders observed.” Machine field names remain stable.
