# Until-stopped MM sessions — TDD plan

Add an explicit session duration mode without adding an auto-renew worker.
`timed` keeps existing defaults/bounds. `until_stopped` requires deployed YAML
permission and no duration_seconds; fixed and random selection share this control.
Random keeps finite step deadlines. Stop, HALT, owner fencing and all bound risk
policies remain authoritative. Bootstrap retains its own finite episode deadline.

- [x] A — Red/Green request/catalogue validation, indefinite fixed/random clocks,
  strict final-send gates, skipped-window restart and old-session compatibility.
- [x] B — Red/Green service scheduling, bootstrap expiry and PostgreSQL HTTP
  start/switch/stop/retry/restart, including disabled current YAML versus a pinned
  active session and HALT behavior.
- [ ] C — Capabilities, English Swagger/FE controls and documentation, native/PG
  regression, scoped report and local commits. Preserve existing timed examples.

No automatic startup, HALT reset, bound-policy reset or bootstrap renewal is
introduced. Missing deadlines on a timed or malformed session must fail closed.
