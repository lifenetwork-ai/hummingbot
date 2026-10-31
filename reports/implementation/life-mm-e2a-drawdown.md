# E2a: managed V2 report drawdown

Date: 2026-10-26 (Asia/Ho_Chi_Minh). Baseline: `96d6fae98`.
Scope: E2a of the [enhancements plan](../../docs/plans/LIFE_OKX_MM_ENHANCEMENTS_TDD_PLAN.md).
Status: E2a complete and verified locally.

## Delivered behavior

The existing MM catalogue now wires optional global and controller drawdown
limits to the V2 runner. Both default to `null`; no production threshold is
invented. An MM adapter uses the existing owner checkpoint and bounded service
shutdown, preserving filled spot inventory and perpetual positions, including
partial fills. Legacy runner drawdown dispatch and stop behavior remain intact.

The metric is **covered V2 bot trading-report peak drawdown**, not exact account
equity or a newly created session's PnL. Global and per-controller peaks have the
upstream zero floor and include covered report history. Fees recorded separately
on held positions are subtracted once; funding is excluded. Existing active
executor versus held-position mark/quantity conventions and historical Float
precision remain explicit limitations. See the [operating guide](../../docs/life-mm-controls.md#drawdown).

Both configured controller identities remain in scope when one market is
disabled. Retained nonzero exposure uses the existing provider's public,
non-trading book subscription and classified read-only initialization retry.
Missing/stale report or mark coverage blocks new risk and requests normal
position-preserving cancellation; fresh coverage can resume the same session.
Unknown exposure outside the declared scope requires review. Missing data never
becomes a fresh zero PnL or an artificial peak.

Policy, stable scope, monotonic peaks and breach/stop metadata extend the existing
`bot_state.recovery_state.strategy_checkpoints` entry. Manual/random/profile and
spot/perpetual/both switches, clean restart and unclean HALT cannot reset them.
Commands cannot disable/change an armed policy or bypass it via a legacy runner.
Superseded startup must pass the existing command-version check under the owner
row lock before binding policy. No table, migration, PnL ledger, service, cache,
new environment variable or reset endpoint was added.

The existing status endpoint/Swagger exposes scope, limits, peaks, observed
PnL/drawdown, stable waiting/breach reasons and explicit fee/funding coverage.
Stopped/stale observations retain peaks and confirmed breach but expose current
PnL/drawdown as `null`. DTO serialization keeps Decimal strings and UTC timestamps
consistent for both normal observer publication and final shutdown publication.

## Red/Green findings

- Initial report tests: eight failed, one passed. They demonstrate omitted
  disabled-market exposure, held trade-fee loss at handoff, invalid/stale marks,
  incomplete initialization and skipped retained position storage.
- Initial runner tests: ten failed, four passed. They establish finite thresholds,
  zero-floor/equality/breach semantics, per-controller scope, checkpoint failure
  behavior and position-preserving risk stop rather than legacy stop actions.
- Config/service wiring: two failed. Limits and full stable scope were not passed
  to the existing runner; no inactive execution controller is needed.
- Decision-age regression: one failed. A newer report-cache timestamp could make
  an old sampled drawdown decision appear fresh. Readiness now requires freshness
  of both the cached report and the actual risk observation.
- First image fault run reached retained-market valuation and stale-feed recovery
  but failed clean termination. The normal observer normalized Decimal values,
  while final publication used the raw new drawdown snapshot. A native shutdown
  test failed, then passed after snapshot serialization used the existing API DTO.
  The failed image/run remain recorded; no timeout or exit assertion was relaxed.

Required checkpoint SQL failure is also injected against real PostgreSQL: the
transaction rolls back, the durable peak is not overwritten and admission closes.
Fixtures were corrected for an invalid mocked persistence method; this is not
reported as a production defect. Broad suites retain pre-existing warnings.

## Verification

| Check | Result |
|---|---|
| Native regression | 1,437 tests and nine subtests passed: 151 / 11 / 972 / 303 |
| Focused tests | 73 passed with RuntimeWarning treated as errors; overlaps regression |
| Disposable PostgreSQL suite | 147 passed; final drawdown selection passed 11 / 137 deselected, adding explicit random-generation coverage |
| Swagger and scoped hooks | Passed |
| Packaged source | Twenty-one runtime/config/contract files match final image |
| Focused image drawdown case | Passed, including two intentional restarts |
| Final full image matrix | All nine groups passed; retained feed makes no account mutations |
| Guard + drawdown load probe | Sixty seconds passed: p95 74.81ms / p99 518.06ms, 1,200 HTTP 200 responses and zero unexpected errors |

Real-PG coverage includes durable monotonic peaks, clean ownership handover,
manual market/session changes, immutable armed policy, generic CLI bypass
rejection, superseded startup, invalid persisted peaks, unclean HALT, real retained
Position rows and failed peak-write rollback. Native cases also exercise safe
read retries/cancellation, retained-controller breaches, report/decision freshness,
fee basis, flat known history, legacy behavior and stopped API serialization.
The additional random-step case uses the real session transaction and a new
runner bind: advancing generation retains the bot peak/policy and original
session deadline instead of establishing a new baseline.

Final image:
`sha256:84f4d33ae3153a049aaddaa55532a58a446a00a6656629f456e8e46ec7922dd0`
(1,364,627,702 bytes). The complete matrix is retained at
`build/e2e-offline/20261026-195801-fa070f/result.json`. The drawdown case verifies
that the public retained-market feed makes no account mutations, there are no
perpetual orders after switching to spot, partial exposure survives both restarts,
and a new admin start after breach is rejected. Clean shutdown took 2.05 seconds;
the subsequent stop took 1.26 seconds. Startup observations were 8.54–8.82 seconds.

The combined probe used a 1 CPU/2 GiB bot under ARM-to-amd64 emulation, 100,000
historical orders and 100,000 fills, twenty requested FE reads/second, about
100 book updates/second and one fill/second. Actual completed rate was 19.93/s,
command acceptance p95 was 28.77ms and observed RSS was 328,478,720 bytes.
Sixty unique exchange/recorder fills and positions matched; two identified
funding bills and three daily snapshots were present. No budget was relaxed.
The effective test-only YAML enables global/controller limits of 1,000/500 USDT
and strict guards; these are not production risk defaults. Artifact:
`build/mm-load/20261026-201233-ff4087/result.json`.

Immutable identities, source hashes and measurements are in the
[portable evidence](life-mm-e2a-drawdown.json). Raw logs/ledgers stay in ignored
`build/`. E2b–E6 remain open; this phase makes no new sixty-minute,
previous-image upgrade, Cloud Run deployment or real OKX Demo acceptance claim.
