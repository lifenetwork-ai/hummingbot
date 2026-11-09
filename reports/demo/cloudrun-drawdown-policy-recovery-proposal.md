# Cloud Demo drawdown policy recovery — approved and applied

2026-11-09, Asia/Ho_Chi_Minh. Revision55 (`hummingbot-demo-00055-g2s`), image
`0acdcba89…`, serves100% traffic with2vCPU/1GiB. The actual `/profiles` catalogue
contains200USDT global/100USDT per-controller drawdown limits. Both credential
slots remain Connected. The pre-repair bot was stopped/HALTED at control/ack15/15.
The operator separately approved this exact policy re-arm. It committed as audit39
after full backup/restore rehearsal; all financial/credential table fingerprints
and PnL peaks were retained. The real Google-controlled600-second observation
passes and the bot is left RUNNING, Until stopped, control/ack16/16.

The existing checkpoint deliberately retains the old25/15USDT policy, spot
`drawdown_controller_limit` breach and `_is_stop_triggered=true`. Deployment did
not acknowledge/reset that risk stop. Native policy binding rejects the old
checkpoint with the new policy, as designed.

## Reviewed mutation

After fresh exchange/DB readback, quiesce service writers, verify the owner is
absent, take a restore-reviewed backup and acquire the exclusive advisory lock.
Abort on a changed baseline, pending exchange effects or unrelated HALT.

In `bot_state.recovery_state.strategy_checkpoints["life_mm_profiles.yaml"]`:

- Change only the policy limits from global25/controller15 to global200/controller100.
- Re-arm `_is_stop_triggered`: true →false.
- Acknowledge `mm_drawdown_breach`: the recorded spot breach →null.
- Clear its `drawdown_exited_controllers`: the two stopped controllers →[].

Remove only the associated top-level `halt`/`reason`, and append one maintenance
audit identifying this approval and the old/new policies. Preserve the global
PnL peak2.897705638971199999999999983 and controller peaks
spot2.586496555971199999999999983/perpetual0.3112090830000000000000000. All other
checkpoint/recovery fields, orders, fills, aggregate positions, funding, account
bindings and credentials remain unchanged. Keep the bot stopped during the repair.

Restore automatic min1/max1 scaling with the same revision, image,2vCPU/1GiB and
other configuration. Then use the operator's Google identity to Start a new
both/follow_market Until-stopped Demo session and repeat the bounded observation.
The existing0.008 BTC gross cap remains: current long0.0030/short0.0048 BTC leave
insufficient room for new perpetual quotes. This operation does not close those
positions or relax that cap.

## Completed read-only validation

- Live catalogue confirms200/100; DB contains323 orders/68 fills/3 position rows,
  no creation-pending order and the expected old drawdown HALT.
- A copied-state preview invokes native `BotOwnership.bind_mm_drawdown()`.
  The old binding is rejected; the proposed binding succeeds and restores the
  exact retained global/controller peaks with no stop latch or breach.
- This is an in-memory preview, not an applied DB transaction. Fresh REST,
  backup/restore rehearsal and final baseline assertions remain prerequisites
  to applying the operator-approved transaction.

## Applied verification

- Current68 fills were independently matched to REST quantity/price/original fees;
  venue pending orders0 and long0.0030/short0.0048 BTC were unchanged before repair.
- Service manual scaling0 left no owner or other application DB clients. A full
  PostgreSQL16 custom-format backup restored into an isolated local PostgreSQL16
  container; the exact policy-only transaction/native binding passed there first.
- The live transaction held the exclusive advisory lock, changed only the reviewed
  recovery/checkpoint fields and appended audit39. All other bot-state fields and
  financial/credential table fingerprints were unchanged; old peaks remained exact.
- Automatic min1/max1 scaling, revision55/image/resources were restored. API became
  stopped/ready with no HALT/error and both status/catalogue showing200/100.
- A local relay helper initially mishandled maintenance HTML503 as local HTTP500,
  producing the browser JSON-parse error. A helper Red/Green reproduction now
  preserves structured HTTP503 without body/token disclosure. This is not an
  exchange/runtime/OAuth credential defect. The operator subsequently completed
  real Google sign-in and the new bounded Demo session was started.

This applied approval is distinct from the earlier initialization-checkpoint repair.
Any subsequent unrelated risk breach still needs its own review; limits and peaks
must not be silently reset to make a run pass.

## Completed bounded Demo observation

Revision55:21:19:52–21:30:09 local, including command startup and final reads.
The600-second monitoring loop records115 samples; its last periodic sample is at
597.544s and final readback confirms continued running after the full interval.
No Stop/Revoke is issued at successful completion. Both slots remain Connected.

- Actual runtime policy shows100USDT per controller/200USDT globally; no HALT,
  runtime error, application ERROR or observed owner/engine replacement.
- 70/70 recorded fills match individual REST quantity/price/original fees at the
  final financial readback. Two new natural spot fills occur at21:28:00/21:28:02
  within this measurement; BTC cash delta and perpetual leg quantities match.
  Trading remains active and can generate more fills after this bounded proof.
- Final REST readback has two working spot SELL orders, no perpetual working
  orders, unchanged long0.0030/short0.0048 BTC. BUY allocation/skew and the existing
  gross cap still constrain sides; this is not a two-sided or new perpetual-fill pass.
- One startup probe warning is300.938ms, followed by healthy observations. One
  controller progress-confirmation warning is5.529s; periodic perpetual no-quote
  warnings retain their known inventory-limit explanation. They are not omitted.
- Available Cloud memory minute means are42.11–46.01% of1GiB; CPU means are
  2.82–5.09% of2vCPU. This is bounded headroom evidence, not a long-run leak test
  or a unique explanation for the original15-second unresponsive interval.

The prior revision54 run still fails its15/25USDT policy. This subsequent run
uses separately approved100/200USDT settings and does not retrospectively convert
the earlier result into a pass. Full soak, two-sided SLO and perpetual full-fill
handoff remain separate acceptance scopes. Private pre-rearm/accepted-running
backups are retained; an active-owner snapshot still needs normal recovery review.
