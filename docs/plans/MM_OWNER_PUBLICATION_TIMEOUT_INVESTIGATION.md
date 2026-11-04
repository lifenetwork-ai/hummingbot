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
restored stopped with all 23 fills and four ledger hashes intact. That initial
short-verification phase did not include a new hour, actual Demo trade or deployment. Historical exclusive
attribution above remains open.

[Correction report](../../reports/investigations/mm-owner-publication-timeout.md#offline-history-preparation-correction),
[verification evidence](../../reports/investigations/mm-offline-history-preparation-evidence.json).

## Requested full-hour confirmation

- [x] Run the unchanged standalone 3,600s Random soak on `086454de` using the
  corrected offline preparation. Retain all preparation and measured-window
  failures; keep original rates, resource/deadline limits and acceptance budgets.
- [x] Verify continuous samples, HTTP freshness/errors, fill/fee/position ledger,
  memory retention and unexpected engine failures/restarts. Restore the stopped
  actual Demo with unchanged ledger hashes, publish evidence and commit results.

Completed: measured window 2026-11-04 10:47:14.268–11:47:14.268 UTC.
Runner artifacts: `build/mm-offline-history-soak-20261104T104559Z/`;
raw result: `build/mm-load/20261104-174603-b267d6/`.

**PASS:** 79,200 HTTP 200 replies, no unexpected errors/stale, primary/auxiliary
p99 260.39/77.27ms, 3,595 exact synthetic fills/fees/positions, all three Random
profiles and 58 continuous samples (maximum gap 65.50s). Retention growth is
28.15MiB against the unchanged 64MiB budget. No unexpected service failure,
publication failure or child restart appears in the retained logs. Planned
pre-window shutdown/restart and generation 1→2 are explicitly recorded.

Eight engine-probe timeout episodes recover; the longest observation interval
is 3.798s and maximum reported probe duration is 338.964ms. All eight derived
intervals have independent simulator fills plus local engine fill logs; seven
also have new simulator-accepted orders. This establishes some MM work during
those intervals, not every tick deadline or continuous two-sided quote coverage.
Per-tick/activity diagnostics were not enabled in this unchanged acceptance run.

Demo is restored stopped, owner available, no recovery block, all 23 fills and
four ledger hashes unchanged. Shipping image/settings are unchanged. This closes
the requested standalone-hour confirmation only; no new full R1/R2, actual Demo
trade, Cloud Run qualification or exclusive historical attribution is claimed.

[Hour report](../../reports/investigations/mm-owner-publication-timeout.md#full-hour-confirmation-after-offline-preparation),
[portable evidence](../../reports/performance/mm-offline-history-soak-evidence.json).
