# Cleanup polling: fewer repeated reads and recoverable dependency timeouts

## Confirmed issues and changes

`CleanupExchangeRuntime.cancel_orders()` reread every tracked order on every poll,
including orders already reconciled as terminal. Keep every identity for the final
remote-order check, but only poll unfinished orders after their first REST/fill
read. Initially terminal WS/tracker entries still receive fresh REST verification.
Orders that finish before their peers also get one final read after all pending
orders finish, preserving the old loop's last-pass check for delayed fills.

A raw `asyncio.TimeoutError` from status/fill readback reached the coordinator as
`cleanup_failed`, requiring operator action. The bounded refresh helper now maps it
to `cleanup_orders_pending` (503). The existing dependency retry keeps the original
operation, tracked orders and encrypted credentials; it does not permit duplicate
submission or report completion without final proof.

Each read uses `min(10, remaining_poll_budget)` instead of an independent ten-second
timeout per order. An exhausted budget starts no new read. Close readback waits the
existing 100 ms between unfinished reads and returns immediately on terminal
readback, removing the initial back-to-back query and unnecessary post-terminal
sleep. Cancellation continues to propagate, with the order identity retained.

Only Disconnect readback changes. Strategy ticks, market-making quote logic,
submission authority, unknown-order handling, final remote/DB checks, persistence
health and removal/audit transactions keep their existing behavior. No cache or
new scheduler is added. Credential status already batches receipt lookup using
`IN`; no SQL N+1 was found in that reviewed path.

## Before/after evidence

| Controlled case | Before | After |
| --- | --- | --- |
| Three tracked orders over three polls: one terminal initially, one finishes immediately, one finishes on poll three | 9 status/fill refresh calls | 7 calls; early terminal orders get first/final reads, pending order read three times |
| Two reads, twelve seconds already elapsed in a fifteen-second poll budget | Both await budgets independently 10 seconds | First 10 seconds, second remaining 3 seconds |
| REST readback timeout during cancel or close | Raw timeout, classified as unexplained failure | Safe transient `cleanup_orders_pending`; tracking retained |
| Unfinished close, then terminal readback | Two immediate reads, then a 100 ms sleep even after terminal | Read → 100 ms wait → terminal read, return |

The nine-to-seven reduction is **22.2% fewer refresh calls in this controlled case**,
not a claim of a 22.2% overall runtime or trading speed improvement. Network await
budgets do not bound the duration of the whole Disconnect, event-loop scheduling
or a synchronous persistence callback.

## TDD and verification

Four initial behavioral tests fail against the original runtime: repeated terminal
reads, raw cancel timeout, raw close timeout and missing remaining-budget cap. A
separate cadence test also fails before the close loop is changed. All pass after
the runtime changes. The first broader attempt has 24 passes/one failure because
an existing fake clock lacks the new pre-read time check; advancing that clock
through an actual first read restores its original cancel-ACK-without-terminal
assertion. No guard assertion is removed. A later review adds a failing delayed-fill
test against the first five-read optimization; the final seven-read version keeps
the last-pass reconciliation. Its timeout is also tested as a dependency wait,
never a completed cleanup.

The runtime file has **30 cases**, including nine new cases. Two additional
PostgreSQL coordinator cases check waiting-for-dependency status, retained keys,
scheduled retry and eventual same-operation completion for cancel/close readback.
The adapter and coordinator portions are tested separately: the latter injects
the mapped error, rather than claiming an actual exchange timeout.

The initial runtime version passes full native regression (2,717 tests plus 11
subtests) and related PostgreSQL (104 tests). Final-source regression and a
rebuilt-image cleanup matrix remain pending. No shipping-image, actual OKX, full
load or hour-soak acceptance is claimed until its corresponding evidence is recorded.
