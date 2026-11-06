# Runtime follow-up remediation

Date: 2026-11-07 (Asia/Saigon). Source phases follow
[this plan](../../docs/plans/MM_RUNTIME_FOLLOWUP_REMEDIATION_TDD_PLAN.md) and the
[read-only review](../README.md#historical-records) (Git: `reports/investigations/mm-runtime-followup-code-review.md`).
Only local source/native/PostgreSQL verification is performed. No existing Demo,
real exchange, Google/GCP or production change; no new image/hour qualification.

## R1 — Availability boundaries

Risk checkpoint/status collection and typed rendering now have a narrow protected
boundary. Unexpected view failures produce null observations with scoped incidents;
healthy observations clear them. Ownership/SQL failures and cancellation propagate.
Actual risk checkpoint writes and cleanup remain outside the optional boundary.

Stored outflow projection requires a valid policy, account/environment/pair binding
and integer coverage timestamp. Malformed stopped checkpoints remain retained and
the owner/control service stays available. A running intent with invalid stored
risk data uses the existing durable HALT before admission; raw checkpoints and
unrelated recovery fields remain intact. Required HALT-write failure loses
authority and propagates. Cold-HALT reconciliation is not bypassed.

The Stop path also uses protected drawdown observation capture before real cleanup;
a rendering error cannot prevent cleanup, while a real cleanup failure still
propagates. Semantic invalid-drawdown projections keep one continuing incident
instead of logging false recovery on every poll.

Fresh checkpoint incidents produce the English operator state **Risk data needs
review**, disable Start/Switch and retain Stop. Stale/render-only incidents do not
become cached risk authority. A null risk observation means unavailable, not disabled.

Initial native Red:12 failed/3 passed; extra checkpoint/Stop Red:7 failed;
operator view Red:2 failed/2 passed after correcting a test-only `actions` field
name. Thirty native + five real-PG cases are added. Related native87 and PG10
pass; focused suites overlap final broad results. Logs:
`/tmp/hbot-runtime-r1-red.log`, `/tmp/hbot-runtime-r1-extra-red.log`,
`/tmp/hbot-runtime-r1-operator-red-corrected.log`, `/tmp/hbot-runtime-r1-ui-green.log`,
`/tmp/hbot-runtime-r1-postgres.log`. Formatting/lint checks pass.

## R2 — One strict steady readiness traversal

A running session without listing bootstrap now performs only the final strict
readiness check. Preparing/bootstrap sessions keep qualification, the initial
bootstrap-permitted check, anchor/checkpoint writes and the final strict check.
Per-order and after-throttle checks remain fresh; no readiness cache is introduced.

Nine native cases: two Red failures/seven passes before the change; all pass after.
A real LIFE-controller fixture measures reference/exposure checks from two to one
per controller, with no steady session write. It verifies strict rejection, data
changes between calls, recovery, preparation changes during commit, bootstrap and
inactive sessions. Related native46 and PostgreSQL joint-send readiness11 pass.
This halves those traversals, not total engine CPU. Logs:
`/tmp/hbot-runtime-r2-red.log`, `/tmp/hbot-runtime-r2-green.log`,
`/tmp/hbot-runtime-r2-postgres.log`.

## R3 — Narrow fresh valuation and per-report hold reuse

Valuation readiness uses the existing executor's current filled-quote property
instead of constructing the full PnL/custom DTO. PositionExecutor's open-base
fallback still catches exposure awaiting transfer to PositionHold. DTO-only legacy
adapters retain their interface. Non-finite current/open/retained quantities now
return not-ready rather than being normalized away or accepted with a valid mark.
Fresh mark validation, new fills, held exposure, scope checks and critical SQL
failure propagation remain. No report from a prior tick becomes risk authority.

Unified reports pass the already-computed position summaries to performance
aggregation, following existing per-report executor DTO reuse. Standalone performance
calls still compute fresh values. Scope filtering, held fees, PnL and volume remain;
the next call observes changed prices and rejects stale marks. No resident cache,
SQL change, connector rewrite, framework, worker or Redis is added.

Eighteen new native cases: initial nine Red failures/four passes, then four held
non-finite Red failures/one pass. Related native56 pass. The main Make regression
target includes the new report tests. Correctness checks cover current/held exposure,
zero exposure, finite values, stale/recovered marks, fee/PnL/volume, scope filters
and no cross-call reuse. Logs: `/tmp/hbot-runtime-r3-red.log`,
`/tmp/hbot-runtime-r3-held-red.log`, `/tmp/hbot-runtime-r3-final-green.log`.

### Measurements and deliberately deferred work

[Raw evidence](mm-runtime-followup-evidence.json) retains the before/after sample
arrays and limitations. Seven alternating warmed pairs, 500 calls per sample,
compare original methods from `45707b06e` to current methods with separate equivalent
fixtures in one native process. Actual PositionExecutor/PositionHold methods use
synthetic connector/order inputs. These are component CPU timings, not engine p99,
SQL/REST savings, exchange performance or a new load acceptance.


- Valuation readiness, 1 resident(s): median CPU 24.93 → 4.10 µs per call.
- Valuation readiness, 12 resident(s): median CPU 256.87 → 26.98 µs per call.
- Unified held report, 1 resident(s): median CPU 27.10 → 20.13 µs per call.
- Unified held report, 12 resident(s): median CPU 195.30 → 113.71 µs per call.

The steady-admission preliminary seven alternating pairs used real LIFE controller
methods with a fixture provider (500 calls per sample); raw results are retained.
The robust claim is two reference/exposure traversals become one per controller,
not that total engine CPU halves.

JSON normalization is unchanged. Preliminary native medians for inner capture /
outer detach are about 0.010 / 0.005 ms with an empty account, 0.523 / 0.188 ms with
100 synthetic balances (~14 KB), and 5.065 / 1.829 ms with 1,000 balances (~139 KB).
Those are per-component samples, not production payload measurements. The copies
serve NaN rejection and mutation isolation; combining them is deferred without
representative live payload evidence. Existing detached-publication tests remain.

The first measurement fixture lacked two order metadata attributes; it was fixed
before measurement. This was a fixture error, not another runtime defect. The
valuation path with drawdown enabled does not explain the earlier six-probe load
where drawdown was disabled. No single old probe root cause is claimed.

## Frozen-source verification

Source is frozen at `504343b9b` after availability `45707b06e` and steady
readiness `1f4f19de6`. All recorded runtime
file SHA-256 values match the measured/current files.

- Full `make test-cloudrun-regression`: **2,780 passed + 11 subtests** (202 config,
  11 client performance, 1,814 runtime/model/connector/executor and 753 remaining
  strategy/core/guard cases), exit 0.
- `make swagger-check`: pass, exit 0.
- Full disposable PostgreSQL suite: **523 passed**, exit 0, 785.07 s.
  Log: `/tmp/hbot-runtime-followup-final-postgres.log`.
- Formatting/lint/pre-commit and changed-document relative links pass.

Logs retain Pydantic/AsyncMock and SQLAlchemy deprecation warnings; no test failure
occurred.
The five new PostgreSQL cases are included in the full suite, not added to its
count again. No Demo data change, new migration, API configuration override,
new dependency or process architecture is introduced.

A changed runtime image still needs its separately identified image/E2E/load
qualification before deployment. This source result does not inherit the earlier
accepted image's hour gate or establish that every historical probe cause is fixed.

## Subsequent image verification — 2026-11-07

New image `bf4e1f77…` separately passes the unchanged full load/hour/five faults,
four targeted E2E groups and enabled-enhancement90. Soak p99 306.91 ms, zero
unexpected healthy errors, 21.89 MiB retained growth and 4,885 exact simulated
fills/fees/positions. Three healthy probes recover without child replacement;
measurement and attribution limits remain. One unused image removed; all container
references/volumes retained. [Image report](../release/mm-runtime-followup-load.md),
[evidence](../release/mm-runtime-followup-load-evidence.json).
