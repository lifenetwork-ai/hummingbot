# Runtime logic audit: durable HALT and fill replay

Date: 2026-11-02 (Asia/Saigon).
[Plan](../../docs/plans/MM_RUNTIME_LOGIC_AUDIT_TDD_PLAN.md).
Source fixes are being verified. This report does not relabel previous image
acceptance or claim all possible defects are eliminated.

## Confirmed defect A: durable HALT left the core active

The observer's final ownership read could detect durable recovery_state.halt,
set running=false/phase=halted and publish that status without stopping an
existing strategy, clock/connectors or pending startup. New-risk fences still
blocked new submissions, but existing resting orders could remain exposed.
Once the local running flag was cleared, the usual running-only cleanup branches
could be skipped on subsequent iterations. This affects both an earlier HALT and
one arriving after the entry read; it is not caused by the latest query reduction.

Seven native regression cases cover early/late HALT with accurate/stale running
flags, cleanup failure, pending startup and an already-idle halted service.
The original six actionable cases all fail before the fix; all seven pass after
using the existing stop_core/shutdown_core path before publication. Failed cleanup
raises for process recovery without publishing a clean observation; no new owner
release or automatic HALT reset is introduced. An already-idle HALT does not
repeat cleanup.

Four real PostgreSQL cases exercise early/late HALT and successful/failed cleanup,
with existing owner locks, a recorded resting order, cancellation and terminal
readback through the existing shutdown path. Success publishes halted only after
cleanup, retains the original HALT/reason and blocks a successor from acquiring
the current owner's lock. Failure preserves shutdown_incomplete, the unresolved
order and ownership, with no successful status publication. These cases pass.

An initial integration fixture used an unbound JSON literal in SQLAlchemy text;
a bind parameter corrects that fixture. An idle-case test insertion initially
landed inside a nested helper; the corrected test module collects and passes.
Those retained helper failures are not application-runtime defects.

## Confirmed defect B: fee text caused a false conflicting-fill replay

Recorder duplicate identity checks compared raw trade_fee JSON. Equivalent
Decimal values such as0.2/0.2000 or0.001/0.0010 differed as strings, causing an
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

Broad native/PostgreSQL and relevant image lifecycle checks follow the two
focused fixes. No API/YAML/env, migration, risk limits or cache change is needed.
Raw attempts: `build/logic-audit-20261102/`. Demo account/config/volume and Cloud
Run deployment are outside this follow-up.
