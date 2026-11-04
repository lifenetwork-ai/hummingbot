# Owner publication timeout investigation

2026-11-04 (Asia/Saigon).

Investigate the pre-window engine exit on image `086454de`: PostgreSQL canceled the
owner-fenced `bot_state ... FOR UPDATE` publication at 06:48:59.915 UTC, then the
supervisor restarted the engine. Preserve the passing hour and this failed
preparation observation under their actual scopes.

- [x] Retain the exact statement, deadline, owner/control-version predicates,
  error/restart/bootstrap times and unchanged source/image identity.
- [x] Capture bounded pre-window PG activity/locks/blocking PIDs and transaction
  ages, API worker/SQL spans and engine/API stacks during the original preparation.
  Run only short disposable-network diagnostics; no new hour is needed to locate
  a pre-window failure.
- [x] Test the suspected wait path with controlled real-PG transactions, compare
  lock and non-lock controls, and separate reproduced mechanisms from historical
  attribution. Keep 2s statement/3s I/O deadlines and all ownership fences intact.
- [x] If a runtime defect is confirmed, demonstrate Red/Green and implement a
  narrow fix. Otherwise publish bounded findings and the remaining evidence gap.
- [x] Update the current acceptance register, preserve stopped Demo/23 fills,
  publish evidence and commit the completed investigation phase.

Diagnostics never reset recovery, mutate the actual Demo ledger, place exchange
orders or change production limits. All generated orders and credentials belong
to the existing offline simulator and disposable PostgreSQL.

Bounded investigation: three valid preparations, 1,836 PG samples, no reproduced
persistence error/restart; three new plus eleven related PG tests pass. A traced
1.182s publication COMMIT has eleven same-PID WAL I/O wait witnesses during
history insertion. No runtime fix is claimed.

- [ ] Attribute the original timed-out SELECT using a contemporaneous wait/stack
  witness or a matching controlled reproduction. The successful WAL-delayed
  COMMIT is a lead, not closure of this item.

[Report and evidence](../../reports/investigations/mm-owner-publication-timeout.md).
