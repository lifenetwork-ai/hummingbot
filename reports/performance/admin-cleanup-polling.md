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

The runtime file passes **30 cases**, including nine new cases. Two additional
PostgreSQL coordinator cases check waiting-for-dependency status, retained keys,
scheduled retry and eventual same-operation completion for cancel/close readback.
The adapter and coordinator portions are tested separately: the latter injects
the mapped error, rather than claiming an actual exchange timeout.

Final source `f1a7429f4` passes full native regression (**2,719 tests plus 11
subtests**) and related PostgreSQL (**104 tests**). The initial runtime version's
2,717 native/104 PG passes are retained separately; final counts are not added to
them. **Eleven new cases** are added (nine native unit cases and two PG coordinator
cases). Runtime source SHA-256 is
`16530ab4489bceabd24afb619d00e634f822a77eb52f6b510a4f08f1cb2e383d`;
the rebuilt image contains exactly that file.

## Shipping-image cleanup acceptance

Canonical Dockerfile target `cloudrun`, `linux/amd64`, commit `f1a7429f4`:

- Tag: `hummingbot-cloudrun:cleanup-polling`.
- Config/image ID: `sha256:8cdadb21e6ff302d58de5983b6b5154ee37b70fcbeeb645f12e1c79618b00821`.
- Registry manifest: `sha256:711ec041a98388030afa5d8872471b53e6c9cb8bf4e2d8e4dd50f8d8da6002d9`.
- Size: 1,374,439,437 bytes, within the unchanged 1.5 GiB budget.
- Result: `build/e2e-offline/20261106-165113-cfd613/result.json`.

Runtime smoke (ABI, JIT, TLS, SDK discovery and size), followed by **six of six**
native-image cleanup cases, passes:

| Scenario | Venue / recorder fills | Physical close attempts |
| --- | --- | --- |
| Partial live + WS loss | 6 / 6 | 2 |
| Partial cancelled + WS loss | 6 / 6 | 3 |
| Partial live + SIGKILL/restart | 6 / 6 | 3 |
| Partial cancelled + SIGKILL/restart | 6 / 6 | 3 |
| Real PG fill-write failure / replacement / Retry | 5 / 5 | 2 |
| Real PG removal-audit failure / dependency retry | 5 / 5 | 2 |

Each case has its own isolated database and simulator; seed fills are included in
the counts. Fees match exactly, managed perpetual positions finish flat, spot is
preserved, selected ciphertext is removed only after verification, companion
credentials remain and another stopped restart preserves history. Counts include
every physical POST, including rejected attempts, rather than only accepted IDs.
The fill-write failure uses ordinary process replacement plus the existing Retry
path; no ledger repair, health-flag reset or risk-HALT reset is performed.

The request-count improvement is measured by the controlled unit case, not by an
end-to-end latency benchmark. The image matrix verifies bookkeeping/recovery after
the optimization; it does not inject a native REST timeout. Unit adapter tests and
PG coordinator tests cover the timeout classification/retry boundary separately.
Fault windows deliberately produce errors and may retain recovery HALTs; no
zero-error-log claim is made. Existing Demo and real Google/GCP/OKX are untouched.
The gate remains targeted (`release_scope_complete=false`); no new full-image,
upgrade, retention or hour-soak acceptance is claimed. Earlier qualified probe
timing evidence is not reclassified or inherited as new-image evidence.

[Portable evidence](admin-cleanup-polling-evidence.json) contains the raw image
result, source hashes, command summaries, Red/Green failures and final results.
Disposable test/registry containers are removed. Candidate and rollback images,
existing service containers and volumes are retained; no broad prune is performed.
