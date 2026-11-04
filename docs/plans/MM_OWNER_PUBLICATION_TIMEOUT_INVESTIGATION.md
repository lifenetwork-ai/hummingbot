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

- [ ] Attribute the original historical trigger using a contemporaneous
  wait/stack witness or equivalent host/storage evidence. The matching controlled
  reproduction below establishes a mechanism, not exclusive historical attribution.

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
- [x] Repeat the 8 MiB/s PG control using the corrected preparation, then run
  the unchanged 300s load on the same shipping image. Retain any failed result.
- [x] Update evidence/instructions, verify stopped Demo and ledger preservation,
  and commit the completed phases.

Source validation: 13 initial Red cases, one separate Red for lost outflow
origin and six Red caller-wiring cases; **169 related native tests and seven
PostgreSQL compatibility tests pass**. All six maintained seeding entrypoints
use the shared offline preparation. The process experiment captures generation
after the planned restart; memory diagnostics require fresh role metrics.

Two corrected 8 MiB/s PG controls each seed 100,000 orders and 100,000 fills
with no application connection, owner lock or pending order. Normal restart
acquires generation 2; another 65s remains free of publication failure or
unexpected child restart. These are stopped-session diagnostics, not live-MM
acceptance under the imposed I/O limit.

The unchanged 300s workload passes on shipping image `086454de`: 6,600 measured
HTTP 200 responses, zero unexpected errors/stale, primary/auxiliary p99
113.94/36.87ms and 300 exact synthetic fills/fees/positions. Two isolated engine
probe timeouts recover without restart; no claim of zero slow probes is made.
A separate 120s slow-report test passes with one intentional SQL timeout,
successful fresh retry, zero stale replies, 120 exact synthetic fills and
unchanged engine PID/owner/session. Other converted long suites have common
lifecycle/wiring coverage, not new full-suite results.

Commits `7bdb5b6f6` and `2b4011556` change host-side test preparation only.
Runtime image, fences, durability and deadlines remain unchanged. Demo is
restored stopped with all 23 fills and four ledger hashes intact. No new hour,
actual Demo trade or production deployment is claimed. Historical exclusive
attribution above remains open.

[Correction report](../../reports/investigations/mm-owner-publication-timeout.md#offline-history-preparation-correction),
[verification evidence](../../reports/investigations/mm-offline-history-preparation-evidence.json).
