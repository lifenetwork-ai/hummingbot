# Runtime logic audit: durable HALT and fill replay

Date: 2026-11-02 (Asia/Saigon).
[Plan](../../docs/plans/MM_RUNTIME_LOGIC_AUDIT_TDD_PLAN.md).
Two confirmed runtime defects are fixed with Red/Green regressions. Broad source
checks and scoped image lifecycle/recovery checks pass. [Portable evidence](mm-runtime-logic-audit-evidence.json).
This report does not relabel previous image acceptance or claim all possible
defects are eliminated.

## Confirmed defect A: durable HALT left the core active

The observer's final ownership read could detect durable recovery_state.halt,
set running=false/phase=halted and publish that status without stopping an
existing strategy, clock/connectors or pending startup. New-risk fences still
blocked new submissions, but existing resting orders could remain exposed.
Once the local running flag was cleared, the usual running-only cleanup branches
could be skipped on subsequent iterations. This affects both an earlier HALT and
one arriving after the entry read; it is not caused by the latest query reduction.

Eight native regression cases cover early/late HALT with accurate/stale running
flags, cleanup failure, pending startup and an already-idle halted service.
The original six actionable cases fail before the fix; the first seven pass
after using the existing stop_core/shutdown_core path before publication. Failed cleanup
raises for process recovery without publishing a clean observation; no new owner
release or automatic HALT reset is introduced. An already-idle HALT does not
repeat cleanup.

Five real PostgreSQL cases exercise early/late HALT and successful/failed cleanup,
with existing owner locks, a recorded resting order, cancellation and terminal
readback through the existing shutdown path. Success publishes halted only after
cleanup, retains the original HALT/reason and blocks a successor from acquiring
the current owner's lock. Failure preserves shutdown_incomplete, the unresolved
order and ownership, with no successful status publication. These cases pass.

An initial integration fixture used an unbound JSON literal in SQLAlchemy text;
a bind parameter corrects that fixture. An idle-case test insertion initially
landed inside a nested helper; the corrected test module collects and passes.
Those retained helper failures are not application-runtime defects.

The first HALT patch introduced a cold-recovery problem before packaging: it
could cancel startup before existing reconciliation cancelled restored orders.
A separate Red reproduces that intermediate-source regression. The correction
keeps startup reconciling a previously observed HALT with admission closed,
reports starting rather than completed HALT cleanup, and uses the existing
reconciliation deadline/readback. After prior-HALT rejection completes, core
cleanup runs and halted is published. This brings the focused set to eight
native and five real-PG cases. No new-risk permission or automatic recovery reset
is granted. The earlier six failing legacy cases and the prototype-only cold
recovery failure retain distinct attribution.

## Confirmed defect B: fee text caused a false conflicting-fill replay

Recorder duplicate identity checks compared raw trade_fee JSON. Equivalent
Decimal values such as 0.2/0.2000 or 0.001/0.0010 differed as strings, causing an
otherwise identical replay to raise and close recorder persistence. This is a
representation mismatch, not evidence of an actual exchange accounting change.

Two PostgreSQL Reds reproduce the false conflict, while three existing/new true
fee-change cases already reject correctly. The correction reuses TradeFeeBase
and TokenAmount decoding for exact Decimal comparisons; it preserves fee class,
percentage token, currency, sign, values and original list order. Unknown shapes
retain strict equality behavior, and nonfinite changed payloads are rejected.
The original raw stored fee JSON is never normalized or overwritten.

Eighteen native cases cover equivalent formatting, rebates, actual semantic
changes, malformed/extended shapes and payload preservation. PostgreSQL checks
confirm one fill/status row, original raw fees, the same committed-fill callback
payload on replay and healthy persistence for equivalent values. Actual fee
changes still close persistence; an injected commit failure still cannot
acknowledge a fill. Six targeted PostgreSQL cases pass after the fee fix.

## Reviewed boundaries without an additional confirmed defect

- Stop/Switch validation already rereads control/session identity after cleanup
  and configuration awaits; final per-order/transport ownership fences remain.
- Session deadlines use database time, preserve original anchors/deadlines and
  reject stale generation/expired authority. No session-policy change is made.
- Recorder acknowledgments stay after transaction commit, and original amount,
  price and trade identity checks remain authoritative.
- Funding observations use a fenced transaction holding the bot row and owner
  identity/backend-lock check. No exchange mutation or send authority moves into
  the background observer.

## Final verification and artifact scope

Final source `7b727628f` passes **2,310 native tests plus 11 subtests, 296 real
PostgreSQL tests and Swagger generation**. Passing suites retain their reported
dependency/deprecation/resource warnings. Fix commits are `3f8a9395c` and
`87e1d594b` for HALT, and `7b727628f` for fee replay.

Candidate `hummingbot-cloudrun:logic-audit`, image `95cde31c` / manifest
`24ea415a`, packages that source. It preserves the dependency layers of prior
diagnostic candidate `18422eca`; the two changed packaged modules are service.py
and markets_recorder.py. All 1,155 packaged source/config hashes still match
after verification. The raw helper metadata calls the base an accepted image;
that means its earlier targeted diagnostic checks, **not full-release acceptance**.

On the packaged service, disposable PostgreSQL and exchange simulator, eight
live maker quotes exist before an externally injected durable HALT. Cleanup
leaves zero live quotes in 1.055 seconds including polling. Switch returns 409
without changing the control version; the HALT reason is retained before restart.
An explicit restart increases owner generation, retains HALT, leaves no pending
intents and accepts no additional orders. Liveness remains 200. The unchanged
container start timestamp does not independently prove unchanged child-engine
PID, and the local cleanup time is not a production cancellation SLA.

Two standard packaged E2E groups also pass on this exact image: spot
fill/partial-fill/replay, ACK/ownership/DB recovery; and LIFE MM's three profiles
across spot/perpetual/both, Stop/restart and DB recovery. These groups include
runtime smoke. This is targeted lifecycle acceptance, **not all E2E scenarios
or a new full R1/R2/load/sixty-minute soak**. No connected Demo or deployment is
performed. The older full-gate image `e2d393b1` predates both fixes; its historical
passes remain valid only for those recorded runs and do not close these defects.
Historical residual-probe attribution is unchanged by this logic audit.

No API/YAML/env, migration, risk-limit or cache change is needed. Owned test
containers and the temporary registry tag are removed; the candidate and prior
images remain. Original Demo volume/account/config and unrelated containers are
untouched. Raw attempts: `build/logic-audit-20261102/`.
