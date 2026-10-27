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

## C — Discoverable history and bounded order progress

Command and session lists use filter-bound keyset cursors over existing tables.
Command pagination preserves microsecond timestamp ties. Session summaries expose
the last recorded transition, current-session identity and links to configuration
and events; legacy sessions without a creation event are explicitly not invented.
Reconciliation receipts have a stable ID lookup even after a newer check begins.
HTTP contracts omit storage retry keys/hashes and retain Basic Auth and UTC output.

Order pages aggregate recorded fill amounts in one grouped query and reuse that
result for missing-side inference. One-row and 500-row pages both use three SQL
queries. Recorded remaining amount is original quantity minus recorded fills,
not exchange resting quantity; provisional/inconsistent rows return null remaining.
No per-row API requests or new cache is necessary.

Three missing-feature tests first failed. A 20,000-event EXPLAIN test then exposed
session discovery scanning the primary-key stream through unrelated guard events.
Migration 017 adds the ordinary `(bot_id,event_type,id)` index; the final plan uses
it with at most two filtered rows. An initial partial-index attempt failed the
existing schema validator and was replaced by an ordinary index, preserving the
validator rather than broadening migration behavior. The final history, HTTP,
configuration and migration selection passed 28 PostgreSQL tests. The focused
HTTP/docs/Swagger selection also passed 28 tests.

## D — FE and maintainer handoff

The [ownership matrix](../docs/admin-configuration-ownership.md) separates session
controls, immutable YAML settings, deployment environment and durable risk-policy
bindings. Examples cover restart, changed YAML, legacy overrides and technical
recovery. The API guide and original plan's command examples no longer recommend
reference/random overrides on new requests.

The [FE guide](../docs/admin-dashboard-integration.md) keeps Market, Profile/Random
and Duration as the editable session form. It specifies short English operator
messages, read-only managed settings, Details for technical fields, stable history
links, safe retry behavior, shared-wallet accounting and per-widget freshness.
Swagger is generated offline for FE types; production HTTP docs stay hidden.
Frontend implementation/browser acceptance and deployment routing are external
prerequisites, not completed features in this repository.
