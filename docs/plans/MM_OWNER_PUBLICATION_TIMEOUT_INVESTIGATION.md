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

## Follow-up investigation

- [x] Fix the confirmed diagnostic defect that truncated engine/API trace files
  on supervised restart. Red: three OS-process restart cases lose the first
  record. Green: 58 related trace/analysis/timing/incident tests pass; opt-in and
  cumulative file-byte budgets remain unchanged. Only offline instrumentation
  changes; the trading-service image remains `086454de`.
- [x] Repeat the original preparation with server-side slow-statement/error and
  lock-wait logging, alternating traced and untraced runs. Distinguish the
  diagnostic collector's 200ms timeout from the runtime publisher's 2s timeout.
- [x] Correlate a failed publication with the exact backend, WAL activity,
  CPU/I/O pressure and runtime SQL span; retain blocker evidence where present. Use a separately
  labelled WAL-pressure control if the unchanged workload does not reproduce it.
- [x] Add Red/Green coverage and a narrow correction for any confirmed defect;
  retain failed artifacts and report unresolved historical attribution honestly.
- [x] Verify stopped Demo/ledger preservation, update evidence and commit.

Follow-up establishes the matching mechanism: with a per-container 8 MiB/s
PostgreSQL write limit and the original 100,000-row bulk seed, backend 322 waits
on WALWrite and the same publication SELECT/control version 2 fails with 57014.
The retained SQL span is 2,901.57ms. Other repetitions show lock timeouts behind
WAL-delayed COMMIT. The same limit without bulk history stays clean for 65s;
wal_init_zero=off does not remedy the fault. Seven unchanged-setting repeats
and the 500,000-row unthrottled control remain separate clean observations.

The original exclusive trigger is still unproven; the unchecked historical
attribution item above is deliberately retained. No shipping engine behavior,
durability or timeout setting changed. See the report's follow-up section.

## Offline history preparation

- [x] Use Red/Green to prevent fixture seeding while the service is running;
  cover failed Stop/release, remaining DB effects, failed seed/CHECKPOINT,
  changed intent, failed recovery and unexpected child restart without resets.
- [x] Preserve the verified outflow origin, stop the full service, seed and
  CHECKPOINT offline, then require normal startup with a new owner generation
  and fresh runtime-role metrics. Keep workload rows/rates/gates unchanged.
- [ ] Repeat the 8 MiB/s PG control using the corrected preparation, then run
  the unchanged 300s load on the same shipping image. Retain any failed result.
- [ ] Update evidence/instructions, verify stopped Demo and ledger preservation,
  and commit the completed phases.

Source validation: 13 initial Red cases, plus a separate Red for a lost outflow
origin after restart; 143 related native tests and seven PostgreSQL fixture/
reporting tests pass. The first 8 MiB/s control seeds 100,000 orders/fills while
the service is exited and owner released, checkpoints, acquires generation 2
and remains stable for another 65s. A final-source repetition and the unchanged
300s load remain pending below; no measured-hour acceptance is claimed.
