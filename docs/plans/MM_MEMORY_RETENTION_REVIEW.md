# Long-running MM memory retention review

- [x] Reproduce retained event waiters, fill history and retired executor identities with Red/Green tests.
- [x] Bound service-only diagnostic history without truncating durable PG records or legacy balance calculations; retain active position deduplication.
- [x] Red/Green the Stop-during-read-only-reconciliation HALT discovered by the first image probe; preserve uncertain mutation recovery.
- [ ] Verify native/PG lifecycle and run a bounded image memory/lifecycle probe with RSS and retained-object counts.
- [ ] Record results, limitations and commits in a separate report. A short stable probe cannot prove unlimited leak-free uptime.
