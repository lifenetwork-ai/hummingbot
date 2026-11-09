# Local Demo Random cleanup incident — recovery proposal

Prepared 2026-11-09. **Separately approved and applied on 2026-11-09.**
The operator answered “Duyệt phục hồi Demo và test tiếp” to this specific proposal.
The prior account-binding approval was not reused for this HALT reset.

## Observed state

On `b090430c…`, all nine fixed profile/mode cases and their Stop commands passed.
Random `until_stopped` then resumed the same session after a clean active container
restart. At the next step change, cleanup reached its existing eight-second
deadline; the engine exited/restarted and retained HALT. The helper issued Stop
when its running-state assertion failed. This run remains failed.

Many maker attempts had been accepted and quickly canceled. Their retained
references all require fresh status/fill readback. The sequential readback path
dates to `dcaa46fd4d` (2026-10-24); native/PG Red tests independently reproduce
blocked read progress and incomplete shutdown. The proposed bounded-reader fix
does not increase deadlines or skip terminal proof. Missing historical per-RPC
spans prevent claiming the exact contribution of every remote request.

The halted Demo container was subsequently stopped cleanly (exit0/non-OOM).
Independent Demo REST/WS checks show no pending SPOT/SWAP orders, with held
perpetual long `0.0100 BTC` / short `0.0032 BTC` unchanged. Control/acknowledgement
is `144/144`, desired state stopped, `runtime_active=false`, no pending creations,
no pending mutations/credential operation, no `shutdown_incomplete` or reason key.
The retained `_is_stop_triggered` checkpoint remains false.

All 23 prior fills are value-equivalent to the pre-cutover clone and all
3,513 prior order IDs remain. Three new real spot fills match their individual
trade-ID REST quantity/price/original fees and per-order aggregates. Fee-adjusted
BTC cash independently agrees exactly at `0.998960180584 BTC`. No real perpetual
fill is claimed for this run.

Private incident backup: `managed-demo-local-20261109/random-restart-halt.dump`.
The retained local KEK and env files stay outside the repository/build folder.

## Remediated candidate checks

Commit `aba5d745a`, image `4451974e…`: native2,868 +16 subtests, PostgreSQL557,
all1,229 packaged source files matching, real-signal smoke and paired controlled
readback witness pass. Targeted until-stopped Random/restart/Stop and managed
lifecycle E2E pass on the pinned candidate. This is not a new full release/soak,
or proof of the original incident's individual remote-call timings.

## Concrete proposed maintenance

After the remediated image passes its local/package checks and operator approval:

1. Keep every Demo writer stopped; retain another backup. Repeat independent
   account/order/quantity/fill checks and unchanged-state fingerprints.
2. Lock the bot row, assert stopped/no-owner, control/ack `144/144`, HALT true,
   runtime/pending flags false and saved risk-stop false.
3. Remove **only** the `halt` key from `bot_state.recovery_state` and append one
   maintenance audit event. Do not change checkpoint, positions, orders, fills,
   credentials, control version, account binding or risk limits.
4. Recheck immutable financial history and all other recovery keys, commit, then
   start the remediated image stopped and verify loaded credential versions.
5. Repeat bounded Random/restart/Stop and the requested managed credential
   lifecycle checks. Disconnect closes managed perpetual exposure and retains
   spot cash as already documented; it never deletes the API key at OKX.

A rolled-back dry run in the separate `okx_demo_incident_review` database confirms
that removing this one key preserves every other recovery entry and rollback
restores HALT. The approved actual transaction subsequently removed only `halt` and appended
one maintenance audit; all other tables and bot-state fields matched their
pre-transaction fingerprints. Another private backup was retained. The remediated
image starts stopped with both version2 slots connected. Connected Random and
cleanup verification subsequently pass: steps0/1/2, same-session active restart,
Stop, verified close/remove and API reconfigure/stopped cold restart. The final
bot has no HALT, pending orders or perpetual exposure. [Result](managed-local-connected-followup.md).
Stop and report changed
account/exposure, pending effects or any new blocker; this proposal does not
authorize another repair.

Review requirement: [PostgreSQL operations](../../docs/postgres-runtime.md#ownership-and-unresolved-exchange-effects).
