# MM volatility defense implementation

Implementation of the [ordered TDD plan](../docs/plans/MM_VOLATILITY_DEFENSE_TDD_PLAN.md).
Date: 2026-10-28 (Asia/Ho_Chi_Minh). Runtime acceptance remains open until its
own phase has run; baseline or earlier release evidence is not new acceptance.

## V0 — Baseline contracts

The existing MM base already produces three weighted levels on spot/perpetual:
at reference 0.1, the 0.4/0.9/1.8% levels produce the six proposed prices and
50/30/20 allocations within each side of a 200-quote total budget. The deployed
catalogue still selects spot/perpetual/both and two equally weighted levels.

Integration hooks: `LifeMMController.update_reference(sample_guard=True)` owns
policy sampling; ordinary readiness/send calls use cached observations.
`ServiceHost.mm_quote_valid` is called through `BotOwnership.mm_quote_check` at
intent and final transport. Existing controller `StopExecutorAction` preserves
positions. `BotOwnership._mutate_recovery` supplies fenced JSON checkpoints.
`SpreadPolicyStatus`, guard events and the service publication supply typed API
and history views without exchange calls from FE polling.

The new baseline tests are characterization, so they do not claim a Red/Green
runtime fix. Deterministic candle and clock cases follow in the owning phases.

Verification: five characterization cases pass. Candle fixtures reuse the
completed/forming/gapped feed helper in `test_life_mm_spread.py`; a separate wall
and monotonic clock fixture prevents offline time from earning recovery credit.

## V1 — Existing spread floors

Verified every level at zero/moderate/shock NATR and explicit static fallback
with `min_multiplier: 1`, ordered spreads and existing fee/rounding behavior.
Legacy low-multiplier/tighter-fallback settings remain readable. The MM guide
now explains three-level weights and the existing floor settings.

Verification: 31 focused spread/characterization tests pass. This phase needs
no new runtime flag; defense-specific validation is implemented with V2's schema.

## V2 — Reduced sizing and stale quote protection

Red: six new schema/raw-NATR/sizing/controller cases failed before implementation;
an additional task-context case failed because per-level authority did not exist.
Green: raw NATR is retained separately from the clamped spread multiplier, reduced
sizing composes with bootstrap/skew allocation, recovery needs distinct candles
and elapsed monotonic time, and final terms are checked against their own level.
Existing quote cancellation is coalesced, preserves positions and retains pending
reservations; no replacement occurs while an executor is shutting down.

The existing session context now carries optional level identity into async
connector tasks without changing the session authority tuple. Legacy policies
retain their existing send signature. Defense remains rejected at catalogue
admission until V3 installs durable pause/recovery; this commit cannot enable a
partial defense in the service.

Verification: 107 controller/spread/skew/session tests pass; a separate 134-test
selection including service and position executors passed before five additional
bootstrap/skew/per-level cases were added. The regression Make target includes
the new defense tests. Default deployed YAML and original file-mode policies
remain unchanged. An initially incomplete bootstrap test book was corrected to
supply real bid/ask entries; no production guard was weakened.

## V3 — Durable pause and recovery

Red reproduced an extreme threshold being treated only as reduction, backward
candle replay earning recovery, a missing durable checkpoint adapter and static
fallback continuing spread composition after a known extreme event. A retention
regression also demonstrated that a strong controller map kept a retired
controller alive. Green adds strict severity/recovery sampling, a bounded
owner-fenced JSON pause checkpoint and weak controller references.

Latches are account/environment and connector/pair scoped. Profile/session IDs,
Stop/Start/Switch and market selection do not clear them. Control CAS prevents
stale checkpoint clearing, transaction rollback leaves the latch in place,
corrupt/account-mismatched checkpoints require recovery, and YAML policy changes
are rejected while the corresponding pause is active. Recovery never credits
offline time. The existing account-identity helper is shared with spot outflow.

Controller cancellation requests precede the transition write; quantities and
filled positions retain the existing executor lifecycle. Static fallback cannot
clear a pause. Bootstrap admission cannot bypass defense readiness. Final REST
transport rejects an earlier narrower quote through the real owner and recorder.
Catalogues can now admit the complete feature; unsupported unmanaged mode is
rejected. No database table or migration was added.

Verification: 107 focused defense/spread/guard/bootstrap tests and 15 disposable
PostgreSQL defense cases pass. The native regression invocation also passed
1,732 tests plus 11 subtests before the final standalone paused-composition case
was added. Existing position-executor tests cover partial-fill preservation and
terminal cancellation proofs. Image/fault/load and Demo acceptance remain V5.

## V4 — Observable operation

Red reproduced missing typed raw-NATR/defense status, a generic operator label
for a volatility pause and stale snapshots still presenting a known pause/cancel
state. Green adds English labels/actions, Decimal/UTC status, current effective
allocation, recovery samples/time, pending cancellation and saved-latch status.
The global HALT/Stop/expiry contract stays authoritative; stale observations are
unknown rather than healthy. FE policy override remains rejected.

Only sampled state/reason changes log WARNING/INFO with bounded DEBUG detail;
checkpoint failures log ERROR. Repeated cached readiness/status evaluations do
not advance recovery or spam transition logs. Existing guard events record state
changes, and an attached checkpoint readiness check performs zero DB statements.
Swagger and the three canonical operating/integration guides were updated.

Verification: 63 native API/operations/defense/activity cases and 28 PostgreSQL
defense/history cases pass; Swagger generation/check and guide-link validation
pass. An earlier full PostgreSQL run (before the additional lifecycle/retention
cases) passed 226 tests. Final-source broad regression and image/load/Demo gates
remain V5.

## V5 — Acceptance in progress

The first dedicated image attempt failed in its observer: separate ledger reads
spanned quote replacement and produced an empty comparison group. The harness
now evaluates one ledger snapshot and retains first terminal-cancel time rather
than a later duplicate acknowledgment time. That retry passed weighted levels,
partial fills, delayed cancellation, durable pause/restart/new continuous-random
session, automatic recovery, all market modes and position-preserving Stop on
image `809f75ed0a05`. Its result remains scoped to that image.

A 72-case declared synthetic comparison passed independent accounting and replay
integrity. It also exposed an observability defect: intermediate spread state
was logged before later price validation failed, producing ten contradictory
WARNING transitions in five unchanged control samples. A Red test reproduced
this; Green publishes/logs only the final controller decision. This changes the
final runtime source, so image/load acceptance will use a rebuilt artifact.

A second compatibility Red test found that serializing disabled defense as a
new null field changed legacy policy payloads for readers that forbid unknown
fields. A standard Pydantic wrapper now omits only disabled defense; enabled
defense remains fully pinned. The 83-case controller/session/economic/fixture
selection and Swagger check pass after this change. Enabled-policy rollbacks
still require a runtime that understands the feature and its saved latches.

The test harness now includes the enabled three-level defense image scenario,
an explicit load flag/Make target without changed budgets, delayed simulator
cancellation with first-terminal proof, and an optional economic comparison
variant with per-market simulated state-time metrics. The original default
economic matrix and disabled load fixtures remain available. The 32-case
economic/exchange/diagnostic selection passes. Final-image acceptance is pending.

The old private Demo database port was unreachable and no retained Demo database
container/backup was found. The user requested recreating the container. A Demo
PostgreSQL container has been recreated with a dedicated named persistent volume;
no exchange position or recovery state has been reset. Exchange compatibility
testing remains after the local gates and account-state checks.

Three further boundary Red tests reproduced: Stop racing the first pause write
discarded the breach, automatic random replacement could reset an unfinished
recovery window, and a cold profile could jump directly to normal sizing inside
the hysteresis band. Green allows owner/account-fenced recording across control
changes while retaining strict CAS for clearing, defers automatic replacement
without extending old step authority, and starts cold controllers conservatively
at reduced size until qualification. Account binding precedes defense sampling.
Recording risk during HALT does not clear it; automatic clearing remains blocked.
The 68-case focused selection and 32 PostgreSQL defense/session cases pass. The
plan/guides now state these contracts explicitly. Earlier image results remain
historical; final artifact/load acceptance uses this corrected runtime.
