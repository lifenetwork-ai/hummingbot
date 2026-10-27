# Cloud Run logging verification — 2026-10-27

The rebuilt `hummingbot-cloudrun:local` image passes the packaged runtime smoke,
all 13 functional offline scenario groups and inspection of actual service logs.
Cloud Run and Cloud Logging ingestion have not been exercised.

| Item | Verified result |
|---|---|
| Runtime source | `938145833` (initial logging implementation: `15690be9d`) |
| Image ID | `sha256:df5d240f42c5d6bcf9aa2de648f17730bb602ac17b0e8580901185256776296e` |
| Image size / architecture | 1,366,580,877 bytes, approximately 1.37 GB; Linux amd64 |
| Packaged runtime | All 61 checked source/config/schema paths match the frozen source |
| Native regression | 1596 tests plus 11 subtests; 18 targeted logging cases |
| Static verification | Swagger and applicable pre-commit hooks pass |
| Image smoke | ABI/JIT/TLS, toolchain removal, all five severities, NETWORK admission, UTC/context, redaction, startup failure JSON, Python warnings and SQL parameter suppression |
| Functional image matrix | All 13 groups pass, including the nine LIFE profile/market combinations |
| Actual service logs | 2531 records from 36 files; zero malformed/non-JSON lines and zero verbose SQL records |

## Defects caught by image verification

The first candidate, `bcd946aa3398`, passed the original packaged smoke but failed
inspection of actual container logs. Bootstrap reset SQLAlchemy's WARNING level
to NOTSET, enabling query and bound-value logging even with `engine.echo=False`.
The interrupted run emitted 27060 SQL INFO records. Pydantic serializer warnings
also produced 12 multiline stderr lines outside the JSON formatter.

Three Red tests reproduce SQL output at INFO/DEBUG and Python warnings bypassing
logging. Commit `938145833` retains the SQLAlchemy WARNING threshold and routes
Python warnings through the existing formatter. Legacy file/YAML initialization
restores its original warning handling. The reusable image smoke now checks both
behaviors. No SQL query, database schema, trading policy or dependency is added.

The first matrix was deliberately interrupted after four completed groups;
it is not recorded as a passing full run. Its logs/result remain under ignored
`build/e2e-offline/20261027-165522-708508/`. The rejected local image was removed
after final verification, retaining its source/hash and failure evidence.

## Final image evidence

The rerun passes schema validation, original spot runtime, V2 spot/perpetual,
LIFE sessions, funding/backfill, availability, quote guards, drawdown, spot outflow,
listing bootstrap, inventory skew and spread policy. It uses disposable PostgreSQL
and an isolated exchange simulator, with no OKX account connection.

Collected logs contain 2405 INFO, 73 WARNING and 53 ERROR records. ERROR records
include deliberate fault scenarios; passing E2E does not mean zero ERROR logs.
All six availability incident records match their operator/retry severity, and
four recovery events are retained. No configured dummy credential appears in
the inspected service logs. DEBUG/CRITICAL emission is checked separately in the
runtime smoke; normal service tests use the INFO threshold.

The log inspection covers selected service containers, excluding native
PostgreSQL/simulator output and standalone CLI output. Credentials, full raw logs
and disposable databases are not copied into this report. All test containers,
including the temporary image-retention container, were removed.

This run does not repeat the upgrade/rollback gate because the historical
comparison image is no longer cached; no reconstructed baseline is substituted.
`release_scope_complete` remains false. No new load/soak gate, real OKX session,
Cloud Run deployment or Cloud Logging ingestion is certified by these checks.

See [portable evidence](cloudrun-logging-verification.json), the
[operations guide](../docs/cloudrun.md#logs-and-severity), and raw final results under
`build/e2e-offline/20261027-171618-8bf611/`. Build/check logs and packaged hashes
remain under ignored `build/service-logging/`.
