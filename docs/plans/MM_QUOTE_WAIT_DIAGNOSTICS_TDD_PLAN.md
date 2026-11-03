# Quote-wait diagnostics

Date: 2026-11-03. Extend existing quote activity/status, without new services,
queries, exchange requests, configuration or changes to trading authority.

- [x] Red: distinguish pending submission/cancellation, executor reports, known
  blockers and unavailable diagnosis by side; local pending is not proof of send.
- [x] Red: continuous sampled stage duration resets on gaps, invalid data,
  owner/session changes, restart, Stop and recovery, including one-sided quotes.
- [x] Red: retain only a fresh sanitized proposal exception class, never its text.
- [x] Green: typed optional diagnosis in both existing API views and warning logs,
  with English guidance and compatibility with old stored observations.
- [x] Verify native/API/PG coverage and Swagger, document FE fields, publish
  evidence, update checklists and commit. Prior image/load acceptance does not
  automatically transfer to changed production source.

[Report/evidence](../../reports/investigations/mm-quote-wait-diagnostics.md):
baseline Red15/API Red2/structured-log Red1; final canonical native2,429 plus11
subtests, focused44, relevant real-PG19 and Swagger pass. Diagnostic output is
read-only and does not establish a unique historical probe cause. Accepted image
51d4d879 remains the previous artifact; no rebuild/load/Demo acceptance is claimed.
