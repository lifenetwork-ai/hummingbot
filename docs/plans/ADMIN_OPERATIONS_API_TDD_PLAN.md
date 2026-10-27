# Operations API and configuration ownership — TDD plan

Scope approved 2026-10-27: backend API, Swagger and FE handoff only. The dashboard
will be built in a separate FE repository. Preserve the current trading engine,
PostgreSQL ownership, receipts and session snapshots.

Primary operators may be non-technical: all API/Swagger messages are English,
short and actionable. Technical identities/configuration hashes belong in advanced
details. Swagger UI/schema/assets require ENV=dev/development (case-insensitive);
the requested default is dev. The production template explicitly sets production.

## Configuration contract

API commands may start, switch or stop a session; select `market_mode`, fixed
`profile_id` or random `selection`; and select `duration_seconds` within YAML
bounds. Concurrency/identity fields are preconditions, not configuration overrides.

References/weights, the random pool/step duration, market templates, price/spread/
inventory/leverage settings and risk policies belong to deployed YAML. New API
requests must reject overrides, even when their values equal YAML defaults.
Environment/credentials/DB/logging settings belong to deployment environment or
secrets, not the command API. API calls never write YAML or environment files.

An accepted session pins its effective catalogue/request in PostgreSQL. Restart
restores that snapshot, anchors and deadlines; deployment of different YAML does
not silently patch it. A new start/switch uses that responding revision's deployed
catalogue, subject to existing durable policy bindings. Legacy persisted sessions
and receipts remain readable; historical overrides are labelled, not silently
rewritten. Recovery HALT is never cleared by these APIs.

## Ordered implementation

- [x] A — Red/Green YAML-only profile configuration and structured HTTP/domain
  errors, request IDs, CORS response headers and documented error responses.
  Keep legacy receipts readable; update API callers/fixtures that used overrides.
- [x] B — Red/Green configured capabilities, process/owner identity, observation
  freshness, read-only recovery/action guidance and pinned/deployed configuration
  provenance. Add a catalogue-hash precondition to guard against revision drift.
- [x] C — Red/Green bounded command/session history discovery, stable check lookup
  and page-level recorded fill aggregates without N+1. Use existing tables and
  indexes unless query evidence requires a small additive index.
- [x] D — Complete API/YAML/environment ownership tables, restart/rollout examples,
  API/FE guide and Swagger. Mark the external FE scope explicitly.
- [x] E — Run native regression, disposable PostgreSQL, current-image targeted
  functional/polling verification and changed-file checks; save scoped evidence
  and commit. Do not claim browser, real exchange or cloud acceptance from Python
  tests or historical image results.

Each implementation phase records its Red/Green evidence and a local commit.
Phase A: eight operator-contract tests first failed, then passed; four non-dev
Swagger cases first failed, then passed. The focused post-change suite passed
53 tests; disposable PostgreSQL admin/store/reconciliation selection passed eight.
Phase B: three HTTP tests and a real-PG catalogue precondition test first failed;
47 focused tests and five PG tests then passed, including immutable archive lookup,
restart decoding, same-key retry after YAML changes and safe publication fields.
Phase C: three missing-history/progress tests and a 20,000-event query-plan test
failed before their fixes. The final history/configuration/migration selection
passed 28 PostgreSQL tests. Order pages use three queries for both one and 500 rows.
The deployment access route remains an operator deployment prerequisite: the
template's internal ingress is not changed by this work. No cloud deployment,
live account interaction, frontend app, automatic recovery reset or extra cache
service is part of this implementation.

Final scoped verification: 1,634 native tests plus 11 subtests, all 210 PostgreSQL
tests, generated-schema drift and changed-file hooks pass. Image `a2d823c5a2c9`
matches all 18 changed runtime/schema files. Its targeted LIFE MM matrix passes
nine profile/market combinations, restart, expiry, reconciliation and DB-fault
handling. A 60-second diagnostic with 100,000 orders and 100,000 fills completes
1,500 reads without errors or budget violations. This is not the full release
matrix or a new hour soak. See the [report](../../reports/admin-operations-api-implementation.md)
and [portable evidence](../../reports/admin-operations-api-evidence.json).
