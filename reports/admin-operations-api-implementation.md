# Operations API implementation evidence

2026-10-27. Implements the [TDD plan](../docs/plans/ADMIN_OPERATIONS_API_TDD_PLAN.md).
FE is a separate repository. The application continues to use existing service,
control transactions, receipts and engine. Cloud deployment is outside this work.

## A — YAML ownership and operator errors

Eight new HTTP tests first failed because overrides were accepted or errors lacked
actionable fields. New API commands now reject all profile-setting overrides,
including references, random pools and step durations. Duration remains a bounded
session control. Historical receipt models are separate from new request models,
so legacy payloads can still be inspected without allowing them on new requests.

Errors expose safe field paths, stable codes, concise English detail, retry advice
and a server-generated request ID. Storage raises typed conflict identities;
HTTP never parses exception messages or echoes invalid inputs. Reconciliation
in-progress and cooldown conflicts differ; cooldown has a Retry-After value.

Four non-development Swagger tests first failed. ENV defaults to dev; dev and
development are case-insensitive. UI, schema and assets return 404 for other values
even with valid Basic Auth. The production template explicitly sets production.
Operational API authentication remains unchanged; ENV is not OKX_TRADING_ENV.

Verification: 53 focused unit/HTTP/schema/config tests and eight disposable
PostgreSQL admin/store/reconciliation tests passed. A broader overlapping focused
run also passed 66 tests before the ENV gate was added. Existing dependency
deprecation warnings remain. Image/load verification follows after the API phases.

The offline API harness now places its AVAX reference and timed random-selection
test policy in immutable YAML fixtures instead of API overrides. Its original
scenario assertions remain in place; execution is recorded in the final phase.

## B — Configuration provenance and operator status

Profiles now expose configured market modes, a canonical catalogue hash and the
API/YAML ownership policy. Start/switch accept an optional `expected_catalogue_hash`
precondition. The FE should send it from the reviewed profile response; a different
revision returns `catalogue_changed`. Matching idempotent retries still return
their original receipt before this check, preserving accepted intent.

`GET /sessions/{id}/configuration` reads the pinned current catalogue or the
immutable session-created event. It explicitly labels source/current-vs-creation
profile context and historical overrides. It never replaces saved settings with
the responding revision's YAML. API history can therefore explain an old session
after a YAML deployment or restart without rewriting its state.

Status adds short English operator guidance, advisory action availability,
responding-process versus published-owner environment/revision, observation
freshness and a safe recovery summary. Old nested readiness is revoked in the
response when owner/session observations are stale. Only recovery booleans are
added to the existing publication query; private recovery state remains excluded.
Stop remains available as durable intent during owner loss when a session exists.
These presentation fields do not replace trading authorization or clear HALT.

Three HTTP tests and one real-PG catalogue test failed before implementation.
Post-change selection: 47 focused unit/HTTP tests and five disposable PG tests
passed, including old/current catalogue comparison, stable retries across YAML
changes, archived configuration lookup and private-state exclusion. ENV gating
and trading environment remain independent.
