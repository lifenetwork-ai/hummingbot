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

## E — Final scoped verification

Portable evidence: [admin-operations-api-evidence.json](admin-operations-api-evidence.json).
Runtime commit `a04d56808`; image
`sha256:a2d823c5a2c907c5db45edb24adace180ad4c489577da2a59bb35f80064143a6`.
All 18 changed runtime/schema files match source byte-for-byte. The Linux amd64
image is 1,366,753,844 bytes and was exercised under emulation on this arm64 host.

| Verification | Result |
|---|---|
| Native regression | 1,634 tests + 11 subtests passed |
| Full disposable PostgreSQL suite | 210 passed |
| Focused harness/Swagger checks | 17 passed; overlaps native suite |
| Contract/style/docs | Swagger drift check, changed-file hooks and five guides' local links passed |
| Packaged runtime | ABI/JIT/TLS/logging smoke, connector discovery and all 17 SQL scripts passed |
| Targeted LIFE MM E2E | Nine profile/spot/perpetual/both combinations passed; exact quotes, CLI handshake, restart anchors/deadlines, stale-feed wait/recovery, random/expiry, held-position funding, reconciliation and DB-fault HALT verified |
| New API on image | YAML overrides rejected; catalogue precondition, command/session discovery, pinned config after restart, stable check ID and recorded fill fields verified |

The first image attempt stopped at the old smoke assertion expecting 16 SQL
scripts. The fixture now expects 17 and checks migration 017 by name; the same
image then passed. Earlier build output is not used as current-image evidence.

The 60-second polling diagnostic seeded 100,000 orders and 100,000 fills. It kept
the original eleven-route 20 reads/s, market and fill workload, and existing
event/liquidity reads. Three new command/session/configuration reads per second
were added; no budget was relaxed. The bot was limited to one CPU and 2 GiB.

| Metric | Observed |
|---|---|
| Original population | 1,200 / 1,200 reads; p95 69.94 ms, p99 301.42 ms |
| Auxiliary population, including new operations reads | 300 / 300 reads; p95 75.72 ms, p99 254.74 ms |
| Errors / budget violations | 0 / none |
| Market updates / fills | 99.96 updates/s; 1 fill/s |
| Start / switch / stop applied | 2.69 s / 2.90 s / 1.79 s |
| Command acceptance p99 | 46.93 ms |
| Peak RSS | 331,530,240 bytes |
| Independent ledger | 60 exchange fills = 60 recorder fills; positions matched; two identified funding bills |

Raw artifacts are retained in `build/admin-operations/`,
`build/e2e-offline/20261027-220115-fb7e8d/` and
`build/mm-load/20261027-220614-8388f1/`; hashes and summaries are in the portable
evidence. Existing dependency warnings remain. No browser app, real OKX request,
Cloud Run deployment, full release/upgrade matrix or new sixty-minute soak is
claimed. FE implementation and hosted access-route verification remain separate.
