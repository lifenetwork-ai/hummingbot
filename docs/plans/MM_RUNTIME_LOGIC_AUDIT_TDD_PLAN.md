# Runtime logic audit and regression fixes

Date: 2026-11-02 (Asia/Saigon). Follow the completed
[residual probe investigation](MM_ENGINE_RESIDUAL_PROBE_INVESTIGATION.md).

- [x] Review ownership/control/session, shutdown, fill replay and optional
  background-observer boundaries. Report confirmed defects separately from risks.
- [x] Red/Green external durable HALT while core/startup remains active. Cleanup
  resting quotes/startup before a halted snapshot; failed cleanup must require
  process recovery rather than publish success. Preserve existing stop/recovery.
- [x] Red/Green equivalent duplicate fill fees with different Decimal text.
  Reuse existing fee models, preserve the first raw payload, reject real fee
  changes and never acknowledge a failed commit.
- [ ] Run broad native/PostgreSQL checks and relevant image lifecycle/recovery
  tests; record actual scope without borrowing older artifact acceptance.
- [ ] Publish findings/evidence, update checklists/progress and commit phases;
  clean owned resources. Do not mutate Demo/account/config or deploy.
