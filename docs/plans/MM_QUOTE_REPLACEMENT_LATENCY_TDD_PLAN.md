# MM quote replacement latency

Scope: optimize the verified revision110 path without changing caps, fee-inclusive
breakeven, terminal proof, native financial commits or quote qualification.
Existing Demo runs and failed quote windows retain their original scope.

The regular Random window has16 Spot and57 perpetual missing-quote samples;
the conservative two-sided fractions are95.32%/92.65%. Twenty native replacement
pairs have median1.1915s and maximum5.508s between last fill and next venue
creation. Those timestamps do not identify a unique code bottleneck or prove
continuous quote downtime.

Code inspection finds an unconditional5s sleep inside order-executor shutdown,
plus its normal loop sleep. A cancel ACK can update the order during that wait
without waking the task. Managed closing quotes will use a coalescing wake event;
legacy executors retain their timing. Waking a loop grants no new order authority.

## Checks

- [x] RED: cancel ACK during the5s wait must retire a managed CLOSE promptly,
  retaining all original and late partial fills. Include ACK-before-wait,
  both hedge directions, duplicate/unrelated events and cancellation cleanup.
- [x] GREEN: wake managed executor waits on relevant state changes; keep legacy
  waits and the indeterminate-outcome fence. Commit remains the capacity boundary.
- [x] Verify a completed order does not leave a managed loop sleeping; an ACK
  without financial commit cannot release the controller level.
- [x] Reproduce and remove the extra managed-controller scheduling wait after
  publication, with coalescing, no busy loop and no concurrent control cycles.
- [x] Add bounded stage timing to the existing opt-in offline activity collector
  for terminal callbacks, native checkpoint and subsequent quote processing.
- [x] Run focused executor/controller/financial tests and disposable PostgreSQL
  commit/rollback/recovery checks. Qualify the packaged candidate separately.
- [x] Review profile preparation and status/liquidity sampling delays using
  retained evidence. Preserve the shutdown fence and unknown-as-failure rule.
- [ ] Connected quote qualification needs a new deployed artifact; do not assign
  new local behavior or a99% pass to the currently running revision110.

## Additional incident found during connected observation

At04:28–04:30UTC on2026-11-12, revision110/owner483 is not trading:
`bootstrapping_recovery` waits on a Spot creation intent with a durable HTTP200,
matching client ID, empty venue order ID and `sCode=50037` receipt. The existing
classifier recognizes50036 only. Repeated51603 reads then remain inconclusive.
The startup retry path also fails to raise `operator_required` after five minutes.

- [x] RED four classifier/connector cases for50037, then GREEN for both50036/50037.
- [x] RED PostgreSQL replay and startup escalation; reject only exact saved
  receipts after current account/environment validation. Keep ambiguous receipts,
  local fills and acknowledged venue identities protected.
- [x] Resolve intent, saved tracker and runtime audit in one transaction;
  interruption rolls all three back. Native FAILED callback follows commit.
- [x] Verify a real successor owner resolves the receipt with no second POST and
  no financial/checkpoint change; ordinary51603 still pauses and requests review.
- [x] Startup and post-cancellation bootstrap waits use the same attention policy.
  Operator presentation requests support without inventing a HALT or reset button.
- [x] Deploy the qualified changes and verify the existing blocked intent follows
  the native recovery path, then repeat connected quote qualification.

Do not mark this incident fixed on Cloud Run from local tests. No manual Cloud DB
update or trading Start has been issued in this follow-up.

## Local outcome

Main regression:3,483 tests plus16 subtests pass. All738 PostgreSQL tests pass;
the17-case expiry selection includes two additional final native-transport cases.
The final stopped-Start attention edge was RED before correction; its16 operator
and API tests pass. Pre-commit and whitespace checks pass.

The300-second diagnostic image probe passes its original budgets with300 matching
fills, four actual closing fills across both hedge legs and verified managed-key
cleanup. Four matched terminal→financial-commit→next-controller observations are
retained. The final image differs only in the operator presentation edge and
passes a real-network simulator lost-ACK E2E: recovery2.97s, same owner, no duplicate
submission, quotes restored. This is scoped local acceptance, not a full new soak
or a connected quote-target pass. See the
[evidence record](../../reports/performance/mm-quote-replacement-latency.md).

## Connected revision111 and post-only follow-up

The50037 intent is resolved automatically with exactly one runtime audit; bot
control84 continues on owner484. The completed901-sample regular Random window
is quality-valid but fails99%: Spot93.46%, perpetual87.89%. The original classifier
and all unknown/guard categories are retained. No incident HALT reset or owner
restart occurs; transient profile-shutdown fences remain failing observations.
See [connected acceptance](../../reports/demo/cloudrun-quote-replacement-acceptance.md).

- [x] Prove native cancelSource31 and nineteen same-price attempts by one CLOSE
  executor; separate this from an unknown submission or a financial mismatch.
- [x] RED four live-cancel cases, then retire managed maker CLOSE through native
  terminal/financial application rather than replay its prepared price.
- [x] Verify partial/zero fills, both sides, duplicate/foreign events and the
  unmanaged path. Native3,491+16, PostgreSQL35 and both packaged hedge cases pass.
  The old image fails; its failed run is retained. No proof or risk guard is relaxed.
- [ ] Deploy this additional local correction and repeat connected qualification.

Three event-clock timestamps exceed the manual verifier's one-second allowance;
retained native snapshots still match the actual execution times. Keep that
separate limitation visible without rewriting history or widening the tolerance.
