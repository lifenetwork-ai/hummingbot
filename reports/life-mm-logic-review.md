# LIFE MM logic-flow review — 2026-10-26

Status: the initial consolidated checks pass; final checks and image verification
are being repeated after the cold-CLI lock regression found during E2E. The implementation
reuses the existing service, MM controller, control store, connector helpers and
funding checkpoint. No new framework, storage table or cache is needed.

## Confirmed defects and fixes

| Flow | Reproduced behavior | Corrected behavior |
|---|---|---|
| Partial fill and pending cancellation | The first fill sets `is_trading`; remaining live entry quantity drops out of exposure. `SHUTTING_DOWN` also stops being active before cancel acknowledgement. Another level can exceed inventory/gross caps. | Reserve each configured entry while active or shutting down, until its terminal hold/termination. Preserve separate perpetual long/short exposure. |
| Decimal quote boundaries | `Decimal(float_spread)` makes the theoretical 0.099/0.101 quotes fall outside those exact configured price bounds. | Convert the spread through its decimal string in the shared MM price helper. |
| Start/switch superseded during awaits | Cleanup or configuration loading yields to a newer stop/switch, but the old row can still initialize the previous strategy and cause a false recovery HALT. | Re-read owner/control/session state after both awaits; abandon superseded intent before `prepare`. |
| CLI/MM/legacy transitions | CLI stop leaves MM preparing/running; later admin start stays blocked. Explicit legacy YAML selection retains a stale MM snapshot. | Share the MM stopping/generation handshake with CLI stop, and replace the MM snapshot on explicit legacy selection. Preserve recovery/HALT. Load the MM model before the control transaction so cold CLI imports cannot block the owner heartbeat. |
| Stopped revision bootstrap | Missing previous YAML or credentials prevents a stopped service from exposing controls. | Validate the deployed selection when it is requested to run; stopped startup remains controllable. |
| Observational reconciliation | Matching IDs/status/fills can hide wrong instrument, side, original size, type or price. Spot market size can use quote units. Amendments can evade the final read. | Compare essential working terms using existing symbol/type/contract helpers. Missing terms are incomplete; differences are mismatches; changes between reads invalidate the comparison. |
| Funding history coverage | Once the first order ages beyond retention, already-observed history is marked missing. Downtime gaps start too early; an expired overlap can produce reversed bounds. | Preserve contiguous committed coverage. Start an actual gap at its last successful end, trim old overlap, and repair false gaps wholly inside proven coverage. |
| Configured funding cadence | A one-hour poll interval is marked overdue/stale after the fixed two/five-minute thresholds. | Persist poll cadence with the checkpoint; overdue uses that interval and stale uses max(five minutes, two intervals). |

## TDD evidence

Failing assertions were executed before the corresponding fixes. Logs remain in
ignored `build/logic-review/`; the portable result will retain their identities
and the final tested image.

| Area | Red evidence | Targeted Green evidence |
|---|---|---|
| MM exposure and price | Seven failures for partial-fill/price cases; six failures for the cancellation window | 163 controller, PMM, PositionExecutor and ExecutorOrchestrator tests |
| Service/control | Seven native failures plus four real-PG failures; one additional cold-import heartbeat failure | 26 native lifecycle/session tests, 47 initial PG tests and 53 targeted PG tests after the cold-import correction |
| Reconciliation/reporting | Eleven order-term/missing-field/amendment failures, then two market-size-unit failures | 63 admin/reconciliation tests and 11 PG reporting/reconciliation tests |
| Funding | Five failures for retention/overlap/downtime/cadence/repair | 19 funding and service-observer tests |

The consolidated native regression passes **1,321 tests and nine subtests**:
149 configuration, 11 performance, 931 CLI/model/connector/engine/V2 and 230
controller/session/transport/funding tests. Existing deprecation warnings remain.
The initial **126 PostgreSQL tests** pass against a fresh disposable database.
The additional cold-import regression brings the final suite to 127 tests; that
rerun is in progress. The 13 funding cases pass after formatting-only corrections.

## Findings from the first image attempts

The first V2 fixture expected the old float-rounded quote of 98.99. The actual
ledger records a buy at 99.00, the exact 1% discount to a 100 anchor. The fixture
now checks 99 in the wire order, recorded fill and held position. Its failed run
remains retained rather than counted as acceptance.

The next run passed both V2 paths and all nine MM combinations, then its new CLI
stop case exposed a control-row lock timeout. The initial CLI/MM fix lazily
imported `MMSession` while holding `SELECT ... FOR UPDATE`; cold dependency loading
blocked the service heartbeat long enough to close the process. A deterministic
real-PG Red test now publishes from another connection during that import.
Loading the model before taking the transaction lock passes this test and the
53-case targeted control/ownership/service selection. The final image must be
rebuilt and reverified after this correction.

## Behavior and verification limits

Full-entry reservation is deliberately conservative. If account observations
already contain a partial fill, that filled amount can temporarily count twice
until the executor reaches its terminal hold. Quoting may therefore pause earlier
at tight caps. After termination, account snapshots remain authoritative; this
change does not add atomic sequencing between exchange fills and balance updates.

The review covers the declared OKX spot/perpetual/both MM paths. It does not claim
all possible upstream strategy/connector defects have been eliminated. The new
image is verified locally; existing real Demo funding/trading evidence retains
its original image identity. No real exchange orders or Demo database changes
are part of this follow-up.

Operational contracts: [admin reconciliation](../docs/admin-api.md#observational-reconciliation),
[funding observation](../docs/postgres-runtime.md#funding-while-trading-is-stopped).
