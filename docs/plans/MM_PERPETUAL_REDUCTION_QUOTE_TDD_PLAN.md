# Perpetual close-first quotes

Latest 2026-11-13: revision121/e635f3da7 deploys the readiness-wait correction.
The old technical HALT is recovered after independent verification, audit264,
with zero financial/risk changes. Start succeeds at control86, both/Random/
until-stopped. The00:13 local short check is running with active orders, no HALT,
no ERROR/restart/shutdown timeout and one probe timeout. Original99% quote
acceptance and recovery beyond the API history limit remain open. See the
[revision121 recovery](../../reports/demo/cloudrun-quote-replacement-acceptance.md#revision121-verified-maintenance-recovery-and-restart).

Earlier 2026-11-12: revision117/5c36bb7bd clears the old shutdown fence and
returns to quotes, then recovers quotes after its next scheduled profile change.
This is scoped verification: unavailable/guard samples, original cleanup timing,
the liquidity test failure and the30-minute quote gate remain open. See
[revision117 results](../../reports/demo/cloudrun-quote-replacement-acceptance.md#revision117-verified-fence-recovery-and-next-profile-change).

Its subsequent901-sample baseline fails the unchanged99% target
(Spot98.36%, perpetual97.58%) while recovering all three scheduled profile
changes. Local shutdown-proof/identity fixes and freshness-fixture qualification
do not replace new-image connected acceptance; see the
[follow-up](../../reports/performance/mm-quote-replacement-latency.md#shutdown-proof-sharing-freshness-fixtures-and-executor-identity).

Earlier 2026-11-12: revision116 has diagnostics enabled but encountered a
profile-switch shutdown timeout and a retained drained recovery fence. The
initial two-sided samples do not close acceptance; the local successor-branch
correction and its qualification are recorded in the
[revision116 investigation](../../reports/demo/cloudrun-quote-replacement-acceptance.md#revision116-diagnostics-and-drained-shutdown-fence).

Earlier follow-up 2026-11-12: revision112/`1c1fa280a` runs the post-only CLOSE
correction; native readback confirms fresh-price replacements without same-executor
replay. Its completed901-sample window still fails99% (Spot86.84%, perpetual94.74%).
The bot is running without an incident HALT reset/owner restart. Probe causality
remains open; see [connected acceptance](../../reports/demo/cloudrun-quote-replacement-acceptance.md).

Status: implemented and locally qualified. Demo revision110 contains activation
commit `3af2e3419`: the explicit Demo perpetual policy is now close-first with
fee-inclusive breakeven pricing; Spot and the general catalogue default remain
disabled. A verified remaining-cost baseline was applied under maintenance
audit242 before Start84. Native OKX maker CLOSE orders on both hedge legs and
83 natural LONG close fills are verified. Cold owner handoffs479→480→481 preserve
the same continuous session, control84 and exact remaining-cost checkpoint,
without manual HALT reset. The regular Random thirty-minute quote window is
complete and fails the99% target: conservative Spot95.32%,perpetual92.65%.
Ordinary replacement gaps and unknown observations are retained. See the
[connected acceptance record](../../reports/demo/cloudrun-close-first-baseline.md)
and [local release qualification](../../reports/release/mm-availability-closeout.md).

## Outcome and scope

Add an optional perpetual inventory policy that uses ordinary maker quotes to
reduce a held opposite hedge leg before opening another leg. Reuse native
connectors, executors, ownership fencing, financial journals and checkpoints.
The first supported adapter is OKX HEDGE/CROSS. Spot remains independent.

Increasing a cap adds capacity; it does not create an inventory exit. This policy
allows inventory to cycle through OPEN and CLOSE effects. Passive quotes can
remain unfilled, so enabling it does not guarantee continuous fills, flat
inventory or the existing 99% two-sided quote target.

## Proposed behavior

| Verified held exposure | BUY quote | SELL quote |
| --- | --- | --- |
| Flat | OPEN LONG within caps | OPEN SHORT within caps |
| LONG only | OPEN LONG within caps | CLOSE LONG |
| SHORT only | CLOSE SHORT | OPEN SHORT within caps |
| Both legs | CLOSE SHORT | CLOSE LONG |

The policy is close-first **per quote side**. It does not prohibit every opening
order whenever any inventory exists. The normal caps still constrain growth.
If the opposing leg exists but cannot safely be reduced, that side waits with a
specific reason; it must not fall back to OPEN. At the cap, independently proven
reductions remain possible while new exposure is blocked.

Proposed YAML, pinned into the session:

```yaml
inventory_reduction:
  mode: close_first  # disabled by default
  price_mode: breakeven  # operator-selected fee-inclusive closing floor
```

Default-disabled serialization must preserve existing catalogue/session hashes.
Unsupported connectors, account modes, unknown fields and enabled policies without
an explicit price mode fail configuration validation.

### Selected pricing policy

Only quote reductions at prices covering the remaining position's allocated
opening fees and expected closing fees. Positions may remain open indefinitely.
Historical cumulative fees cannot be charged again to every close. The remaining
basis records owned base quantity, weighted opening principal and unallocated
opening trading fees. A partial close consumes the same fraction of principal
and opening fees; closing fees stay in native lifetime/PnL accounting. A new
opening adds only its own cost/fees. Full closure clears remaining costs.
Fee rebates are not spent in advance. Partition rounding is conservative and
tick boundaries are checked using exact ratios, independently of ambient Decimal
precision. Funding remains a separate accounting observation; these trading-fee
boundaries do not certify full account profitability.

The sidecar in `RuntimeCheckpoint.payload.reduction_basis` commits with positions,
executor and fill-application receipts. Its financial hash must match the native
checkpoint and actual positions before restoring any basis. Unknown old nonflat
costs are not reconstructed from lifetime fees. Updating a different controller
preserves unchanged verified bases; updated unknown bases are discarded. An older
writer that advances financial state without the sidecar makes that basis stale.
No new migration or quote-path history query is needed.

The existing OKX perpetual adapter's
`_update_trading_fees` is empty and `_get_fee` uses a configured estimate. Do not
treat that as a fresh account-specific fee observation. The opt-in background
reader now uses the [OKX fee-rates contract](https://www.okx.com/docs-v5/en/#trading-account-rest-api-get-fee-rates),
scoped to the authenticated USDT swap family. It polls every ten seconds with a
five-second deadline and a thirty-second monotonic cache lifetime. A failed read
clears the cache. Only an unambiguous modern fee group or explicit legacy USDT
maker rate is accepted; rebates provide no advance credit.
Unknown cost or fee evidence blocks the closing quote with a specific reason.
There is no fallback to loss-taking or market liquidation in this policy.

The pricing constraint applies to quote preparation, resting-order cancellation
and final pre-transport revalidation. A rejected price never becomes a market
order. Existing emergency Disconnect/Stop behavior remains a separate workflow.

## Native implementation and boundaries

The source audit found these existing facilities and gaps:

- `OrderExecutorConfig.position_action` already supports CLOSE and LIMIT_MAKER.
  `OrderExecutor` completes into native POSITION_HOLD accounting.
- `ExecutorOrchestrator._determine_position_side` already routes CLOSE on an
  order executor to the opposite hedge bucket. `PositionHold` applies the signed
  closing trade and realizes PnL; recorder checkpointing commits the application
  receipt with its financial rows.
- `PositionExecutor` hardcodes OPEN. Do not casually turn its entry into CLOSE:
  its barrier, unrealized-PnL and exposure semantics describe a new position.
- Life MM currently assumes position-executor fields for entry prices, barriers,
  refresh and reservations. Adapt those paths explicitly for close order quotes.
- The native terminal-first path now also supports managed order executors and
  keeps current/retired references through terminal proof. Focused tests retain
  late partial fills and reject missing/inconsistent proofs before publishing a
  hold. Full managed service fault qualification remains open.
- `ServiceHost` now separates opening admission from independently verified
  closing capacity. An above-cap market can reduce without authorizing an OPEN;
  a close reserves fees rather than a new position's opening margin.
- `BotOwnership` checks authority before intent creation and again before REST.
  Both validate resident native identity, effect, hedge leg, quantity, price,
  fresh fees and current authority. The existing send-marker transaction also
  checks persisted intent against exact native OKX wire terms after throttling.
  No extra quote-path SQL or network read was added.

Native cold recovery decodes the recorded executor type and applies terminal
closing fills once to the existing opposite hedge bucket. A missing bucket or
an over-close blocks financial projection and leaves its immutable fills pending;
it cannot create or reverse a held position. Active partial closes get a
report-only mark adjustment until their atomic hold transfer, so drawdown does
not continue valuing the closed quantity as open exposure. Unknown original fill
coverage or fees cannot become zero-cost PnL.

On OKX HEDGE, CLOSE maps BUY to `posSide=short` and SELL to `posSide=long`.
Closing orders have reduction-only semantics in this mode; the `reduceOnly`
parameter is not a replacement for the correct hedge-side mapping. Preserve
`post_only`, native contract conversion and the existing adapter mode checks.
See the [official OKX place-order contract](https://www.okx.com/docs-v5/en/#order-book-trading-trade-post-place-order).

### Quantity authority and reservations

For each controller, pair and hedge leg, closing capacity is bounded by the
minimum of fresh venue exposure and durably applied bot-owned held exposure,
minus outstanding close reservations. Unknown/manual/other-controller inventory
must not become closing authority. Unapplied entry fills are not yet reusable.

Reserve across all levels, queued actions, retries and pending cancellations.
Conservative double reservation during an account-update race is preferable to
over-closing. Release only with terminal proof and covered financial effects.
Never count a pending CLOSE as new opening exposure or subtract its proposed
amount from the actual position before it fills.

All quote/send work must remain bounded by resident orders/levels and cached fresh
observations. Do not add blocking SQL, REST or lifetime fill/executor scans to
either gate. Restoring the cost sidecar reads the current checkpoint and bounded
positions once at startup; native application updates it in the existing transaction.

Recheck after throttling/authentication and immediately before transport. A stale
proposal may shrink through the normal pre-identity preparation and quantization
path, or be rejected after identity creation. It must never grow, switch hedge
legs, turn CLOSE into OPEN, or change a signed payload's effect. A retry covers
only its unfilled remainder and retains the original closing effect.

Cancel an existing OPEN quote when its side now needs reduction; confirm terminal
effects before reusing that level. Also cancel/resize obsolete CLOSE quotes when
the reducible quantity or required price changes. Below-minimum residuals are
reported as dust; do not invent a larger close or an automatic taker fallback.

### Guards, lifecycle and compatibility

Ownership, session/step authority, manual Stop/HALT, drawdown, fresh data, valid
prices, credential authority and transport fencing still apply. A reduction
exception applies only to opening-exposure/collateral restrictions that have been
independently proven irrelevant to that exact close. It is not a cleanup scope or
an automatic HALT reset. Disconnect retains its separate verified close workflow.

Default-disabled sessions must retain legacy behavior and hashes. A live model
check corrected the initial audit assumption: `LifeMMConfig` inherits
`extra='forbid'`, so it already rejects unknown `inventory_reduction` fields.
Reuse this strict opt-in field as the compatibility fence, omitting it when
disabled; do not add a redundant outer marker. Stored `config_hash` alone is not
a downgrade fence. Prove static catalogue and archived-session rejection against
an actual older image. Rollback must stop safely rather than resume OPEN quotes.
Do not add a schema migration unless a demonstrated persistence requirement needs it.

The cached older `perp-cap-005-01-20261111` image (`5bc83d80342c`) rejects the
enabled policy in both a static catalogue and an archived session using its own
models. This proves the reader fence; it is not full service upgrade/rollback
or production rollback qualification.

### Availability fixes found during integration

The packaged checks first exposed a reservation deadlock after an empty order
was canceled. Native positive terminal proof now releases that level without
waiting for an unrelated new order to clean the old admission caches. Legacy
report DTOs cannot reoccupy a level that current native evidence has released.
Unknown terminal effects remain reserved. Bounds distinguish at most 100 live
reservations from the native default buffer of 100 already-closed executors.

The fee-outage check then exposed a different wait: reduction evidence was older
than `max_data_age_seconds` (10 seconds by default). A healthy private stream
lets native backup REST reads remain 120 seconds apart; fresh updates for one
nonzero leg cannot certify an absent opposite leg. The opt-in position worker
checks every two seconds and wakes the existing serialized account reader once
the complete snapshot exceeds half the freshness budget. It coalesces existing
work, adds no competing REST reader or gate I/O, and joins on controller Stop.
Only a successful native snapshot advances freshness; failures still pause sends.
Status reports `reduction_position_unavailable` rather than calling stale evidence
a real exposure-cap breach. No limit or freshness threshold was relaxed.

A locally blocked managed CLOSE now retires its old quote instead of retrying
stale price/authority; its native failed reference remains available for terminal
and financial proof. Readiness rejection logs carry the specific bounded reason.

One fee-recovery run still timed out waiting for a released level, while the
instrumented repeat passed. Two deterministic lifecycle tests then exposed
independent ways to strand that reservation: a delayed Stop could change a
terminated executor back to SHUTTING_DOWN after its loop exited, and an old REST
read/local cancel ACK could overwrite a positively proved terminal order with a
nonterminal state. The orchestrator now ignores Stop for closed executors, and
the tracker preserves positively proved terminal states against nonterminal
updates. Independent trade updates remain deduplicated normally. The failed
run alone does not establish which race occurred; final packaged cases must
pass after these changes, including full close followed by OPEN quotes.

## TDD implementation checklist

- [x] Audit proposal, executor, ownership, connector and financial application paths.
- [x] Verify native OKX HEDGE closing semantics against the exchange contract.
- [x] Operator selected fee-inclusive breakeven pricing.
- [x] Define remaining-cost/fee allocation, conservative partitioning and exact tick boundaries.
- [x] Persist/restore the basis with native financial checkpoints; verify commit rollback and duplicate receipts in PostgreSQL.
- [x] Prepare a read-only opening-cost proposal tool: 16 positive/negative tests,
  independent replay of the older stopped 764-fill perpetual snapshot, and
  rejection of foreign, duplicate or isolated venue legs.
- [x] Establish verified remaining opening costs for the nonflat Demo legs:
  control83,1,686 fills verified twice (777 perpetual), native/rational replay,
  guarded clone rehearsal and maintenance audit242. Cold restart preserves both
  bases and risk. The older stale proposal was not used. See the
  [connected baseline record](../../reports/demo/cloudrun-close-first-baseline.md).
- [x] Add fresh, instrument/account-scoped closing-fee observations and validation.
- [x] Add validated, default-disabled, hash-compatible policy and session capability fence.
- [x] Verify the disabled OPEN-only baseline and enabled nonflat side/effect matrix.
- [x] Test pure side/effect selection and finite shared closing-reservation arithmetic.
- [x] Bind those decisions/reservations to live controller levels and native order lifecycles.
- [x] Integrate native close order executors with maker preparation and quote refresh.
- [x] Reproduce and fix one/two-lot SELL close loss during price quantization,
  including pre-identity refresh. Round the maker price before allocating the
  original level budget; never increase queued base/notional or bypass fees.
  Include all reduction-specific regression files in the canonical CI target.
- [x] Extend native terminal-first shutdown to managed order executors; test late fills, retries and rejected proofs.
- [x] Split runtime admission and collateral accounting; verify CLOSE at both send gates.
- [x] Validate exactly-once native held application, PnL/fees and cold recovery in PostgreSQL.
- [x] Expose cached effect, target leg, reducible/reserved amount and bounded wait reasons in status/API.
- [x] Update OpenAPI/FE types, configuration and operations documentation.
- [x] Run focused PostgreSQL and packaged CLOSE fault/E2E gates on the resulting local image.
- [x] Qualify nonflat LONG/SHORT upgrade/rollback and local release coverage on
  the revision108-based candidate: native3,363+16, PostgreSQL724, OOM7, E2E36
  plus its corrected host target, four unchanged healthy load windows and the
  corrected six-fault target. Original failed `all` executions remain failed;
  CLOSE restriction-decision timing and older input bounds are reported separately.
- [x] Run actual Demo close-first qualification; reconcile REST orders/fills/fees/positions.
  Revision110: both native maker CLOSE sides,83 LONG close fills totaling
  0.0246BTC and0.3665058882USDT closing fees. Two final native scans match all
  1,805 fills. All118 native applied-order receipts since initialization replay
  exactly from the accepted baseline into financial rows and remaining costs.
  Cold restarts preserve session/control and both remaining-cost bases. At that earlier cutoff, a natural SHORT
  close fill had not yet been observed. Revision111 now has47 verified
  post-deployment SHORT close fills in the06:26UTC native/financial readback,
  with allocated opening/closing fees covered. No fill was forced. See the
  [new connected record](../../reports/demo/cloudrun-quote-replacement-acceptance.md).
- [ ] Run the regular-profile 30-minute quote target without weakening its classifier.
  Revision110's completed898-sample regular Random window fails: Spot489/513
  (95.32%) and perpetual819/884 (92.65%), including unknown in the failure
  denominator. Guard exclusions are385/14 samples, not uptime. Seven unknown
  samples occur around three profile transitions; one has a6.55s-old status
  after sequential API reads. The temporary shutdown fence clears automatically;
  no manual HALT reset or owner replacement occurs in the measured window.

### Required behavioral cases

1. Disabled policy and Spot preserve prior proposals and catalogue hashes.
2. Flat, LONG-only, SHORT-only and both-leg states follow the action matrix.
3. At/exceeding opening caps, valid closes proceed; OPEN and unsupported reductions fail.
4. Two levels cannot reserve the same remaining leg quantity twice.
5. Partials, late account updates and unapplied holds never create extra closing capacity.
6. Pending cancel retains capacity; terminal cancel with a late fill applies exactly once.
7. Venue shrink/flat between proposal and REST cannot reverse or open a position.
8. No owned quantity, foreign inventory, stale data, negative/nonfinite values and dust wait explicitly.
9. CLOSE uses the correct native `side`/`posSide`, contract units and post-only type.
10. Zero free opening collateral does not incorrectly consume close collateral or bypass fee checks.
11. Maker price moves, fee changes and the chosen price mode are rechecked before send.
12. Stop/switch, expiry, drawdown, lost ownership and pending credentials still fence orders.
13. Long reductions and short reductions preserve the correct bucket, realized PnL and original fees.
14. Filled CLOSE must not acquire a new position's unrealized PnL in drawdown reporting.
15. Process death or lost acknowledgement at send/ACK/fill/hold commit/checkpoint never duplicates
    the close or its financial application; ambiguous outcomes remain fenced.
16. Cold restart restores effects and reservations, then reconciles before new quotes.
17. Upgrade and unsupported rollback cannot silently convert a stored closing intent into OPEN.
18. Status identifies reduction, reservation waits, dust and unprofitable-price waits;
    stale/unknown observations cannot be counted as quote availability.

Use focused failing unit tests first, real isolated PostgreSQL transactions for
financial/capability cases, and the existing offline OKX simulator for request
mapping, fills, faults and restart. Existing OrderExecutor tests alone do not
qualify the managed service's shutdown, journal or transport paths.

The executor regression tests failed against the prior implementation: a partial
main order could disappear from a forced snapshot, and late fills on an initially
empty retired order were omitted from a managed retry's remaining quantity/fees.
These pre-existing gaps are fixed locally. The inactive-controller basis carry
test also caught a defect in the new sidecar implementation before deployment.
None of these local passes enables the close-first policy or closes Demo gates.

The full-cycle packaged fixture uses two independent outside book levels per
side and includes its own working quotes in the venue book. The earlier
single-level attempt (`build/e2e-offline/20261111-133423-33de54`) remains failed:
the complete close was durably applied, but conservative filtering of recently
terminal own orders exhausted the only visible price level. The guard correctly
reported `market_price_unavailable`; the test did not establish continuous
outside liquidity. Adding an explicit outside backup level qualifies the
lifecycle case, not thin-book availability or the unchanged 99% target.

Local verification on 2026-11-11: the runtime/controller/connector, core,
configuration and focused CLI selection passes 1,614 tests plus thirteen
subtests; the full CLI selection passes 1,370 tests, with
22 logging tests in a separate process.
The focused selection includes nine CLI tests; these counts overlap and are not
an additive total.
The combined upstream executor suite leaves a test logger handler behind, so its
logging bootstrap checks need process isolation. The PostgreSQL owner/recovery,
cost-checkpoint, transport, position-batch and fill-flush selection passed 127
tests in a fresh serial run, including strict native execution coverage.
Dashboard lint, typecheck, coverage tests
and build pass after regenerating API types. Native test/framework warnings and
existing frontend jsdom/bundle warnings remain; they are not runtime evidence.
No load run or actual close-first Demo qualification is included in these counts.

### Packaged local evidence

The local amd64 runtime is image `bf6177dc408d…`, registry manifest
`68db092b1560…`. It overlays 18 current Python/schema files on the newly built
Cloud Run runtime target; it is not an unmodified full Dockerfile build of the
final source. `build/reduction-overlay-r6-20261111/source-manifest.json` records
the exact source hashes checked at the original feature qualification. Later
duration-default changes have separate verification below.

| Case | Result | Local evidence directory under `build/e2e-offline/` |
| --- | --- | --- |
| LONG close-first full cycle | PASS | `20261111-133801-2d9a38` |
| SHORT close-first full cycle | PASS | `20261111-133940-480de4` |
| Default MM restart/control/reconciliation | PASS | `20261111-134117-097ee3` |
| Default GET-only lost-ACK recovery | PASS | `20261111-134451-2c5f6d` |

Each case independently verifies native post-only hedge mapping, one lost ACK
without duplicate POST, partial-close deduplication, original fees/realized PnL,
durable remaining costs after cold restart, quote cancellation on fee failure,
fresh-fee recovery, full closure and return to two OPEN quotes. Final Stop leaves
zero venue exposure and no working orders; three immutable fills and their fees
match native PostgreSQL accounting exactly. Remaining principal/opening fees
are cleared. All fills are simulator stimuli, not actual OKX Demo fills.
The default MM group covers control replacement, preserved restart anchors,
stale-data recovery, dual-market fills/reconciliation, Random expiry and DB-fault
recovery; it does not execute the nine-profile selection matrix. The default
lost-ACK group restores both quote sides without replacing the owner. The local
manifest is `build/reduction-overlay-r6-20261111/qualification.json`.

## Demo acceptance

Before enabling on Demo: exact account binding and financial baseline, no unknown
mutations, compatible image, explicit pinned pricing policy and clear status.
Witness actual maker BUY/CLOSE SHORT and SELL/CLOSE LONG fills. Verify each reduces
its intended held leg without growing the opposite leg, and validate transitions
back to OPEN only after both venue and owned-ledger evidence permit them.

Stop for independent complete REST reconciliation, then cold restart and verify
no duplicate economic effects or manual financial repair. Quote continuity is a
separate measured gate: the previous regular-profile result was 97.219%, not a
passing baseline. Wider spreads or new exclusions cannot be used silently to
declare the unchanged 99% target passed.

### Deployment and continuous-operation follow-up — 2026-11-11

Cloud Build `669d7dd8…` successfully deployed commit `768ee611c` as
`hummingbot-demo-00106-8kt`, manifest `9bbaebb6eaab…`. The full CI image's 18
feature source/schema hashes match the original qualified feature source.
Startup/live return 200 and the post-deploy WARNING/ERROR slice was empty.
The initial stopped state was `session_expired`: the previous 30-minute Random
session ran from 14:27:55 to 14:57:55 Vietnam time. It was not a deployment HALT.

At the operator's request, native Start80 created a continuous Random session
for both markets, retaining the YAML ten-minute steps and existing protection.
The receipt succeeded; status was running with `expires_at=null` and no HALT.
The actual audited API principal is the configured Basic bootstrap account.
No HALT reset, financial repair or close-first activation was performed.
Private receipts and installed-source proof are under
`build/cloudrun-reduction-acceptance-20261111/`.

The local follow-up adds `default_duration_mode: until_stopped` to the Demo YAML
and resolves omitted Start/Switch modes against that default. Explicit timers
still win, permission is required, old timed hashes remain compatible and saved
sessions retain their accepted mode/deadlines. The initial tests reproduced the
missing configuration support, then the backend selection passed 1,428 tests;
the duration and admin/session PostgreSQL selections passed 23 tests. This small
follow-up also passes OpenAPI/FE type consistency, dashboard lint/typecheck/
coverage/build and pre-commit checks. It changes the original frozen source
and has not yet been deployed.
It does not close the close-first, full-release/load or quote-target gates above.
