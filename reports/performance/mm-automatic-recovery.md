# Automatic MM recovery — offline qualification

Status: r7 native, PostgreSQL, packaged E2E and full load/soak gates passed;
the separate 180-second MM activity diagnostic also passed.
No Cloud Run deployment, live database change, trading operation or push was made by
this work. [Portable evidence](mm-automatic-recovery-evidence.json) records artifact
identity and raw-result hashes. The [plan](../../docs/plans/MM_AUTOMATIC_RECOVERY_AVAILABILITY_TDD_PLAN.md)
keeps connected qualification and deferred behavior separate.

## Resulting behavior

| Boundary | Response |
| --- | --- |
| Managed maker POST or cancel has an unknown outcome | Persist the attempt/reservation, pause shared-account admission, obtain exact authenticated order and complete original fills. Resume only under fresh owner/control/session/credential/readiness/risk authority. No repeated POST. |
| Process dies between send, fill recording and native held transfer | Restore eligible outstanding work from a verified baseline. Fill/journal and native position/executor/application receipt commit atomically at their respective boundaries; publish held RAM state after commit. |
| COMMIT request or reply is lost | Preserve the fence. A fresh owner reads actual durable identity/effects; a client exception is not proof of rollback. |
| Venue repeatedly returns not-found, or a send deadline expires | Keep the reservation/pause, bounded retry and explicit status. Request operator investigation after 300 s. No timed absence proof. |
| Risk stop, newer Stop/Disconnect, owner loss, changed account or conflicting finance | Preserve the applicable protection. Reconciliation completion does not authorize restarting against newer control or clearing a risk latch. |

Scope: managed PostgreSQL OKX Spot/perpetual MM PositionExecutor maker entries
without exit barriers. Account-setting mutations and unsupported strategies retain
manual protection. The automatic resolver does not flatten positions. Explicit
Disconnect retains its existing authorized cancel/close workflow.

Outstanding work is bounded at 64 orders; fill reads use 100 rows/page and at most 10
pages/order. Terminal proofs coalesce per order and have concurrency 3. Complete
private zero-fill terminal evidence keeps its fast path. Work is based on outstanding
attempts/unapplied fills, not lifetime history. Managed held transfer requires a
persisted order and exact durable quantity, quote amount, original fee and fill IDs;
a healthy persistence latch or a nonzero RAM total alone does not certify finance.

## Current artifact and completed tests

- Image ID: `sha256:6d6dd5926e5664ff2cf631f750ceb2ca3bad5620be6977a3720e625af02303a3`.
- Manifest: `sha256:0e25f88890415ca24c2de2fe8e8782622c155e5c1d9346bb03a42a9f4326fa7a`.
- Cached base: `sha256:696c0d7d3d640e539ae59aae387da4639a507f0a64d759148b5b265126b70636`.
- 27 changed/new runtime files and migration are hashed in
  `build/mm-auto-recovery-20261110/source.json`. All 1,035 packaged application
  Python files and the production V2 script match source. Five older packaged
  harness utilities differ; tests execute their separately recorded host/mounted
  versions. This local overlay is a test artifact, not a claimed CI deployment.

| Gate on r7 | Result |
| --- | --- |
| Canonical native command | 3,154 passed + 16 subtests |
| Full PostgreSQL | 686 passed |
| Packaged offline E2E | 34/34 groups passed, including compatible takeover/rollback |
| Focused financial/crash/commit regressions | 45 passed; two missing-durable-evidence Red cases reproduced before the guard |
| OpenAPI/TypeScript contract and TypeScript typecheck | Passed using direct Node commands |
| Full unchanged load/soak | Passed: warmup 5 min, mixed 15 min, burst 90 s, Random soak 60 min, six fault windows |
| Independent MM cycle/order/quote observation | Passed 180 s diagnostic, 180 venue samples without observer/clock/sink errors; not the 30-minute live quote SLO |

Packaged fault: a physical order is accepted but loses HTTP ACK and its private
order event. Positive REST proof completes and both quote sides are observed in
1.125 s after fault observation, with the same owner, zero duplicate submission and
a clean Stop. This is resolver completion plus quote observation, not an order RTT
or a claim that no quote existed for the entire interval. The absent-order HTTP429
case remains reconciling, startup/live 200 and ready 503, preserving pending intent
through Stop. All venue fills in these tests are simulated, not actual OKX fills.

The same image passed cold DB recovery in the full MM matrix without manual edits,
all three profiles across Spot/perpetual/both, Random rollover, original fill/fee/
position reconciliation, cleanup crash/partial-close/DB-rollback cases, account/key
rotation, risk guard/HALT preservation and compatible rollback. Required schema is
installed writer-free before the compatible rolling-upgrade fixture.

## Final load and residual latency

One unchanged r7 image, 2 CPUs / 2 GiB container cap, 100,000 historical orders
and 100,000 historical fills, post-only enforcement and unchanged gate budgets.
Linux amd64 ran under emulation on the local Mac; these are not Cloud Run timings
or qualification of a 1 GiB container limit.

| Healthy window | Read p95 | Read p99 | Unexpected read errors |
| --- | --- | --- | --- |
| Warmup 5 min | 52.56 ms | 93.18 ms | 0 |
| Mixed 15 min | 56.51 ms | 107.53 ms | 0 |
| Burst 90 s | 57.55 ms | 105.13 ms | 0 |
| Random soak 60 min | 59.15 ms | 110.76 ms | 0 |

All windows sustained 20 reads/s. Healthy engine/API PIDs stayed unchanged, with
no healthy HALT/restart. All six injected fault windows passed: feed, volatility
defense, owner restart, queued expiry, DB delay and DB failure. The cumulative
4,886 simulated fills reconciled with recorder quantities, fees and positions.
Soak accounted-memory median growth was 16.40 MiB against the unchanged 64 MiB
budget. Observed cgroup peak was 740.70 MiB including page cache; this is not
per-process RSS or a separate peak for each window.

There was one failed engine probe in burst, recovered at the next observation
about 1.068 s later, and none in warmup, mixed or soak. Two probes before healthy
measurement and seven in injected fault/restart phases are counted separately.
The burst incident overlaps a 3.815 s `service.observe_once` span and sampled
DB/fencing work with an event-loop stall; this does not establish a unique cause
or a continuous 3.815 s trading blackout. Burst did not enable independent MM
activity sampling, so continuous quote availability cannot be inferred from it.

The separate 180 s diagnostic passed with read p99 89.61 ms. It had one engine
probe recovered after 1.037 s. Its incident context contains five venue samples,
each showing at least five live orders, with no empty sample or observer error;
maximum tick-start gap was 1.209 s and controller-cycle gap 1.011 s. Discrete
samples do not prove uninterrupted quoting or eligible two-sided availability.
Across that diagnostic's runtime counters, native submit/cancel operation maxima
were 366.82/358.90 ms, including local processing rather than just network RTT.

Raw results: `build/mm-load/20261111-033756-bb07c8/result.json` and
`build/mm-load/20261111-050801-cdb72e/result.json`. Their incident analyses retain
sampling/loss limits and source hashes. Seven unused intermediate auto-recovery
images were removed after qualification; current r7, base, rollback and unrelated
infrastructure images were preserved. No volumes or global build cache were pruned.

## Reproduced defects and integration corrections

The original recovery gap required manual HALT/financial roll-forward after an
ambiguous send or a crash between fill commit and held projection. The implementation
adds positive-proof reconciliation and atomic native application without changing
native active-versus-held ordering or cost basis.

Crash tests also exposed a pre-existing cold-start event problem: before the first
Clock tick, connector time can be NaN and a fill callback can fail before entering
SQL. Fill events now use original trade time when that clock is non-finite. Recovery
checks actual durable trade IDs/economics and can repair a dropped callback only
from complete positive proof.

TDD caught new integration gaps before deployment: cleanup fills did not advance a
matching MM baseline; durability incorrectly depended on optional drawdown; settled
RAM recovery scope needed retirement after a proved explicit Start/manual Recover;
and zero durable rows skipped coverage checks for nonzero managed RAM execution.
These now have positive, rollback and negative regressions. Cleanup advances only
baselines matching pre-mutation financial state, in the fill transaction, preserving
account/key binding and refusing to certify existing drift.

A fault-fixture race also read the old `running` snapshot just after engine death.
Its corrected check requires a new owner generation, fresh observation and terminal
old-order proof within the original 30 s budget. Failed runs remain recorded; no
financial assertion or latency threshold was removed.

One extra journal INSERT changes the fenced recorder-fill regression from 10 to 11
statements, still two flushes. This counts that transaction only; terminal proof
and held-transfer verification have additional queries. It is not a throughput
optimization claim. Final image load measurements establish the operational cost.

## Rollout limits

Migration 021 adds required runtime tables and `orders.position_applied`; creating it
does not invent historical application markers. Install required schema with writers
stopped before compatible rolling takeover. Empty ledgers can bind a baseline;
existing ledgers need independent reviewed account/financial coverage. A large legacy
ledger beyond the manual API limits still requires reviewed one-time cutover. An
intervening old non-journaling writer prevents automatic cold certification.

Persistent ambiguity still pauses shared-account risk. Trading through unknown
outcomes using worst-case reservations is deferred. Kernel OOM/every connected
Cloud interleaving, the current Demo baseline cutover, and the 30-minute 99% eligible
two-sided quote objective remain separate acceptance work. SIGKILL tests are not
presented as kernel OOM tests; API uptime is not proof of working MM quotes.

The local pnpm 9/workspace configuration and missing `.agents/skills` prevent those
unrelated wrapper checks; direct API generation/typecheck succeeded. No UI changed.
