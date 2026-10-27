# OKX private position startup ordering — TDD

- [x] Reproduce position snapshots resolving symbols before contract sizes; cover cold/warm reconnect, concurrent REST and cancellation during metadata fetch.
- [x] Publish symbol and validated contract-size metadata together using the existing instruments response and lazy symbol lookup; retain readiness and quantity guards.
- [x] Run connector/native and PostgreSQL regression, then verify the built image under session churn, fills and Stop/Start with no contract-size startup errors.
- [x] Update prior follow-up references, record scoped evidence and commit completed phases.

Results: [report](../../reports/okx-ws-position-startup.md), [evidence](../../reports/okx-ws-position-startup-evidence.json).
