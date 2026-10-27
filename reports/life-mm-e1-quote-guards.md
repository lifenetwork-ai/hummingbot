# E1: strict market and sideways-range guards

Date: 2026-10-26 (Asia/Ho_Chi_Minh).
Baseline: `fa75bc57f`. Scope: E1 of the
[enhancements plan](../docs/plans/LIFE_OKX_MM_ENHANCEMENTS_TDD_PLAN.md).
Status: E1 complete and verified locally.

## Changes and reuse

The existing LIFE controller now supports opt-in strict target-market deviation
and profile-specific manual sideways ranges. Threshold equality is allowed;
recovery uses a tighter boundary, elapsed monotonic time and at least two distinct
public observations sampled by the existing control loop. Repeated readiness or
final-send calls cannot advance recovery. They can immediately revoke admission.
Restart resets the delay while preserving persisted policy, anchors and deadlines.
Repeated reference checks preserve a composed spread multiplier for future policy.

Qualification reuses connector books and active/cached/lost order identities, with
bounded level/identity traversal. Stable acknowledged unfilled orders contribute
known remaining quantity. Pending, partial, recent terminal and out-of-order private
observations contribute full original quantity as a conservative upper bound.
Only usable depth above the combined bound qualifies; smaller ambiguous levels
are skipped. Stable acknowledged depth inconsistencies block admission. Residual
must exceed double rounding noise and venue quantity minima. This preserves
usable clearly external depth without treating private partial-fill residue as
independent liquidity. No new order tracker, cache, exchange reader or SQL query
was added to the send path.

Existing both-market admission, post-throttle transport authorization and
position-preserving cancellation enforce the decision. Feed/guard waits retain
management API access; they cannot clear HALT or extend expiry. Absolute price,
exposure, recorder and ownership rules remain authoritative. An initial anchor
cannot bypass strict qualification on an empty/own-only market.

Market deviation settings live in market templates; manual ranges live only in
sideways profiles. Existing catalogue/command/session snapshots carry them.
Defaults remain disabled, with no new environment variable or migration. The
existing status contract/Swagger exposes optional guard reason, source quality,
Decimal price/deviation and UTC observation/recovery times.

## Connector quantity defect found by TDD

OKX perpetual WebSocket snapshots previously retained contract quantities while
REST snapshots and diffs already converted them to base units. This would make
known-order subtraction inconsistent whenever contract size differs from one.
A failing non-unit-contract test reproduces the mismatch. WS snapshots now reuse
the existing cached contract conversion, matching the other two paths. Existing
one-unit fixtures remain unchanged economically; no private exchange mutation is
retried and no trading/order sizing behavior is replaced.

## Verification

| Check | Result |
|---|---|
| Native regression | 1,406 tests and nine subtests passed: 150 / 11 / 961 / 284 |
| Focused controller/decision/API tests | 99 passed with RuntimeWarning treated as errors; overlaps regression |
| Disposable PostgreSQL suite | 137 passed |
| Final-source real-PG guard selection | Three passed, 134 deselected; overlaps full suite |
| Swagger | Generated and check passed |
| Image source hashes | Eighteen runtime/config/contract files match packaged paths |
| Image fault scenarios | Eight groups passed, including strict guards with a deliberate cold restart |
| Guard-enabled diagnostic load | Sixty seconds passed: p95 54.99ms / p99 437.33ms; 1,200 HTTP 200 responses, no unexpected errors or budget violations |

Real-PG cases prove post-throttle guard rechecking with cancellation permitted,
expiry retaining priority over recovered prices, and policy/anchor preservation
across ownership handover while the controller delay restarts. Native tests cover
threshold/range boundaries, invalid settings, empty/stale/crossed/own-only books,
bounded depth, full-quantity bounds for partial/cancel/create transitions, rounding
noise, inconsistent observations, shared admission and repeat-read recovery rules.
Broad suites retain existing deprecation/mock warnings; raw logs are preserved.

Final image: `sha256:52a7a27f8062f02f47a71140474e185bd2741e6bbeffe0729975547fc8cbe095`
(1,364,587,950 bytes). All eighteen checked runtime/config/contract hashes match
packaged paths. Final E2E artifact:
`build/e2e-offline/20261026-180417-0eb7e3/result.json`. It includes the nine original
profile/market combinations, V1/V2 lifecycle, DB/recorder/transport uncertainty,
funding, E0 availability and the new guard matrix. The final guard case confirms
actual spot inventory survives partial-fill cancellation and clean restart,
anchors/deadline are unchanged, and the new process waits for guard recovery.
No unexpected process restart occurred; one deliberate restart was tested.

The guard-enabled probe used a 1 CPU/2 GiB bot under local ARM-to-amd64 emulation,
100,000 historical orders and 100,000 fills, twenty requested reads/second, about
100 market updates/second and one fill/second. Actual completed read rate was
19.91/s; all 1,200 reads completed. Command acceptance p95 was 14.09ms and observed
RSS was 326,066,176 bytes. Sixty unique exchange/recorder fills and positions
match, two funding bills are identified and three daily checkpoints are present.
Budgets and the ordinary workload were retained; the explicit fixture flag
enables guard policy and includes own orders in simulated book depth.
Artifact: `build/mm-load/20261026-181923-75546a/result.json`.

[Portable evidence](life-mm-e1-quote-guards.json) records source/image identity,
scope, fixture policy, results and the unsuccessful harness attempt. Raw artifacts
remain under ignored `build/e1/`, `build/e2e-offline/` and `build/mm-load/`.

The first focused image attempt hit a test-harness `None.split` when the healthy
market had a null reason. The new predicate now handles that valid API state;
the application had correctly reached the own-only market wait. This attempt is
retained at `build/e2e-offline/20261026-180014-c7f4e5/result.json` and does not count
as acceptance. Restart order-count assertions now sample after shutdown completes.

## Limits

Filtering known orders is not proof of independent fair value. Public/private
streams have no atomic common snapshot; unknown/manual and other-account orders
cannot be identified. The existing 30-second terminal cache can conservatively
reduce eligible depth. Guard freshness cannot exceed that cache's coverage.
Thin/empty/own-only listings wait under strict mode; no bootstrap bypass or
automatic widening was added.

This is local simulator/disposable-PG verification. No real OKX account, Cloud Run
deployment, previous-image upgrade gate or new sixty-minute certification is
claimed. E2 and subsequent phases remain open. Operational semantics and example
configuration are in the [guard guide](../docs/life-mm-controls.md#quote-guards).
