# HALT cause, recovery prerequisites and OKX reconnect TDD

Scope approved 2026-11-17: explain the actual persisted Demo HALT, reproduce and
fix demonstrated code faults, publish safe cause metadata, and retain recovery
proofs. No blanket HALT reset, risk change or implicit trading restart.

Read-only Demo evidence contradicts the initial ownership-only attribution:
the stored manual reason is `A required runtime task failed; inspect service
availability status.` At 2026-11-16 08:16:53 UTC, the OKX perpetual stream task
exits with `ValueError: list.remove(x): x not in list` after WebSocket close1006
and repeated handshake502. At08:16:54, runtime health reports
`connector_task_stopped`. Later ownership loss/restarts retain this earlier stop.

- [x] RED/GREEN reconnect after an established stream fails, followed by failed
  reconnect attempts; do not reuse/remove the previous iteration's socket.
  Preserve cancellation, retry pacing, subscriptions and message delivery.
- [x] Classify ownership failures with safe fixed codes/SQLSTATE without raw
  SQL, DSNs, exception text or changed ownership/write authority.
- [x] Preserve the initial HALT cause/time/revision and specific runtime task
  evidence; distinguish later cleanup state. Unknown legacy dates stay null.
- [x] Add optional cause metadata to status and Recovery APIs; publication stays
  bounded, unknown/private reason text never crosses the API boundary, and
  recovery blockers remain authoritative. Do not broaden recovery from a string
  match without verification of the stopped runtime and durable scope.
- [x] Update generated OpenAPI/FE types, FE integration guidance and targeted
  banner behavior/tests. Show the concrete cause instead of an unconditional
  EMERGENCY label; retain safe legacy fallback and stale-state handling.
- [x] Run affected native/PostgreSQL/API/frontend checks and hooks. Record
  read-only cloud evidence, remaining live recovery prerequisites and limits.
- [ ] Deploy the repaired image, Stop and run the reviewed connected recovery
  proof/confirmation, then separately Start and monitor actual Demo. Current
  Cloud image159 is unchanged and still HALTed; no manual DB reset is applied.

Local validation: broad native Cloud Run regression4,101 +16 subtests; final
affected recovery/ownership/publication PostgreSQL88 cases (earlier five-module
run93 and targeted3 are overlapping, not extra totals); frontend632 with coverage,
lint/typecheck/build, OpenAPI/type synchronization and changed-file hooks pass.
The successful recovery transaction removes current metadata and retains its
normalized origin code in the audit/operation, preventing stale cause reuse.
Legacy/unrecognized reason plus a shutdown-kind flag cannot imply automatic
recovery. No performance qualification or Cloud recovery is inferred from these
local results.

## Revision160 connected recovery follow-up

Revision160 deploys exact commit `e926ee46c`; both cause APIs return
`runtime_task_failed` with unknown legacy time, and `recovery_reason_unsupported`
is gone. An audited Stop changes control92->93 and is acknowledged. The new
Check returns `recovery_scope_limit`; no Reset or Start is issued.

- [x] Trace the actual blocker with SELECT-only inspection:55,876 orders,
  15,169 fills, matching financial/binding/environment hashes and reviewed anchor,
  no pending intents/unapplied journal/nonterminal trackers. One old attempt is
  `rejected` with `OrderFailure`, no venue ID and no pending create.
- [x] RED reproduces rejecting this resolved attempt, falling back to lifetime
  history and hitting the caps. GREEN admits only a coherent rejected create
  with no fill/venue ID; unknown/live states and inconsistent rows remain blocked.
- [x] A read-only candidate snapshot selects `verified_checkpoint` on the
  unchanged Demo ledger, covering14,519 journaled suffix fills. This is local DB
  eligibility, not exchange verification or permission to reset HALT.
- [x] Affected PostgreSQL59 and native93 pass; preserve original RED1/control10.
- [ ] Commit/deploy the follow-up, then repeat the real Check/reviewed Reset/Start
  workflow. Demo remains stopped and HALTed atcontrol93; no manual DB repair.

Evidence: `build/halt-recovery-postdeploy-20261117`. The API commands use the
configured Basic admin identity and retain ordinary audit records. This is not a
new Google-login acceptance. No credentials, risk limits or ledger rows are edited.

Raw evidence: `build/halt-diagnostics-20261117`. The current production image
does not include these edits. Keep prior resource failures and release gates.
