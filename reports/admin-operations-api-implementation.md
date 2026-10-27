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
