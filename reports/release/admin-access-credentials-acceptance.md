# Admin access and encrypted credential acceptance

Local/offline acceptance is complete on final source/image below. The final related
PostgreSQL rerun passed 148 tests. Real Google/GCP and reviewed Demo cutover remain open.
No production deployment or change to the existing Demo service/database was made.

Additional local managed-environment cold-restart coverage is recorded in the
[follow-up report](admin-managed-environment-e2e.md). It extends the test harness
without changing this runtime image or rerunning/relabeling the load results below.

## Implemented

- Google whitelist identities with `read`, `write`, `admin`; fresh role lookup on
  every request and mutation-time authorization. Basic bootstrap is explicitly
  env-controlled and can be disabled after a Google administrator is bound.
- Input-only exchange credentials, encrypted/account-bound PostgreSQL storage,
  local/GCP key-provider adapters, safe connection status and atomic mutation audit.
  Source selection, provider, routing and strategy YAML remain deployment-owned.
- Stop-acknowledged Set/rotation without auto-start. Guided Disconnect cancels bot
  orders, closes managed perpetual LONG/SHORT, verifies native fills/fees/checkpoints,
  detaches consumers, then erases the selected encrypted bundle. Spot is preserved.
- Durable same-operation retry and same-account cleanup-key repair. Unknown exposure,
  uncertain sends and unsupported modes retain credentials and show operator action.
- English API/UX guidance, audit pagination, Swagger annotations and FE/runbook handoff.

Reuse: existing aiohttp handlers, bounded worker scheduler, PostgreSQL receipts,
ownership, supervisor, native connectors/order tracker/recorder. One Cloud Run service
retains separate API and trading processes with bounded pools. No new web framework,
external queue or Redis was introduced. Admission fixes change API queue headroom,
not worker count, DB pool limits or MM quote logic. Queues allow 64 total/32 reports/
30 heavy jobs; control priority and physical cancellation reservations remain tested.

## Exact candidate and scope

- Source: `3a3011d6f`; tag `hummingbot-cloudrun:admin-admission-final`.
- Image: `sha256:1bb946c39830f79044b520b47dd4d324419cf7f0d63271363673e2ce137b57a3`.
- Manifest: `sha256:c92d3d6fefabb18e2d4b2cc2ee78a2b6122989f0d115533166763a667945f2cd`.
- Image size: 1,374,394,138 bytes, below the original 1.5 GiB budget.
- Portable [evidence](admin-access-credentials-evidence.json) retains hashes, workload,
  limits, query counts, full-window outcomes and bounded capture counters.

| Verification | Result | Artifact/log |
| --- | --- | --- |
| Final native release regression | 2,680 tests plus 11 subtests pass | `/tmp/hbot-admin-admission-native.log` |
| Full PG regression before identity admission changes | 442 pass; SQL/models unchanged by later queue fixes | `/tmp/hbot-admin-release-postgres-clean-final.log` |
| Related auth/credential/cleanup PG regression on final code | 148 pass | `/tmp/hbot-admin-closeout-postgres.log` |
| Identity, admission and worker regressions | 65 focused checks pass; overlaps native total | `/tmp/hbot-admin-report-admission-green.log` |
| Final packaged ABI/JIT/TLS/logging/schema | Pass; 20 migrations, safe request/actor IDs, no arbitrary extra/secret export | `build/e2e-offline/20261105-145245-1ebb6e/result.json` |
| Full 17-scenario E2E/upgrade baseline | Pass on earlier `8cd8052d…`; not relabelled as final-image full E2E | `build/e2e-offline/20261105-083127-b83ad5/result.json` |
| Final 600-second retention/startup | Pass; +18.07 MiB RSS against 32 MiB, p99 615.41 ms, three control transitions, no contract-size startup error | `build/memory-review/20261105-145439/result.json` |
| Final native managed cleanup/restart | Pass; 62 exact simulated fills/fees, flat perpetual, preserved spot, erased envelopes, no resurrection | `build/mm-load/20261105-150636-a30e50/result.json` |
| Final unchanged full load/soak/fault gate | Pass, `release_scope_complete=true`, no diagnostic relaxation | `build/mm-load/20261105-150933-07d0a1/result.json` |
| Swagger drift check | Pass | `/tmp/hbot-admin-swagger-closeout.log` |

Tests use isolated PostgreSQL/simulator data. These are not actual OKX Demo fills.
No totals add overlapping focused suites into an inflated test count.

## Final load results

| Window | Seconds | Main reads | Main p95 / p99 | Command admission p95 | Unexpected HTTP errors |
| --- | ---: | ---: | --- | ---: | ---: |
| Warmup | 300 | 6,000 | 64.40 / 357.15 ms | 41.54 ms | 0 |
| Mixed | 900 | 18,000 | 97.14 / 570.17 ms | 56.77 ms | 0 |
| Bursts | 90 | 1,800 | 102.12 / 667.14 ms | 135.93 ms | 0 |
| Random soak | 3,600 | 72,000 | 114.48 / 638.82 ms | 38.20 ms | 0 |

All 97,800 main reads and 24,450 auxiliary reads completed HTTP 200. Soak auxiliary
p99 was 573.10 ms. The fixed mixed window maintained approximately 20 main reads/s,
100 market updates/s and 1 synthetic fill/s; the unchanged 500-update/s burst stimulus
passed. Intentional random-transition gaps are included in soak's observed rates.
All 4,884 exchange/recorder fills, exact fees and positions reconciled; two identified
funding bills and daily snapshots were checked. Soak retained growth was **28.125 MiB**
against the original **64 MiB** budget, using cgroup current minus file plus shmem.
No production GC policy or runtime/error/latency budget was relaxed.

All five fault windows passed: stale feed, owner restart, queued expiry, DB delay and
DB failure. Their expected errors are explicitly separated from healthy-window
latency; no hidden retry of a failed normal window is used.

Security: signed offline Google-shaped identities use the official verifier with
fixture public certificates; no private signing key is persisted and no shipping
verification bypass exists. Basic is off before measurement, env exchange keys are
removed, and managed bundles use real local encryption. There are 100,000 audit rows
alongside 100,000 historical orders/fills. Across normal windows, 1,467 extra security
requests exercised 163 role cycles and 489 expected denials without caching roles.
Instrumented audit queries remain one query/page and credential status three queries.
Already loaded authorized keys kept MM active while the test-only provider rejected
unwrap; provider access was restored before intentional cold/crash recovery tests.
Separate supervised checks cover slow/denied cold loads with admin health available.

## Retained failures and fixes

| Failed attempt | Finding and action |
| --- | --- |
| `20261105-081017-a5bc18`, image `37c36234…` | Native cleanup requested singular `/market/ticker`, absent from the perpetual throttler. Red with the real throttler reproduced pre-transport failure. `4eb311ff5` reuses registered `/market/tickers` and selects the exact instrument. No close was sent by that failed attempt. |
| Seed callback/static safety and SQL-only Seeder cases | Restored the required offline-only seed callback; minimal adapters lacked optional `env`. Retained the guard, added compatibility regressions, reran affected tests and clean full PG. |
| Bulk 20,000-event fixture during concurrent compilation | Timed out on FK checks before the query/index assertion. Setup now has a bounded 60-second local timeout; the exact original timeout is restored before EXPLAIN. Runtime/query budgets are unchanged; no exclusive host cause is claimed. |
| `20261105-092334-94dc45`, earlier successful full gate | Two denial-audit writes could not enter bounded DB admission. Errors were visible, but formatter dropped request/actor extras. Two Red JSON/text tests reproduced it; `c33a73cc9` preserves safe IDs and explicit fallback correlation, never arbitrary extras. |
| `20261105-114223-64ff90` | One `/statistics/daily` 503 was fresh-role admission rejected before the handler, not aggregate SQL. Correlation ID and Red capacity test identify it. `1e4253d47` combines verify/fresh lookup on the existing identity worker. |
| `20261105-123257-e6d1d3` | One client timeout plus 23 identity 429s failed soak despite passing retention. Twenty-reader Red test exposed ten-job reader headroom. The queue fix retains bounds and reserved control. The isolated timeout has no exclusive cause established and is not silently called fixed. |
| `20261105-142118-543f93` | Four reporting-admission 503s and command p95 503.66 ms against 500 ms failed burst. Extended control-burst regression reproduces rejection of combined readers. `3a3011d6f` unifies bounded queue headroom without adding workers/pool connections. Final gate above repeats unchanged and passes. |

Collector invocations using missing test filenames and initial incorrect HTTP audit
assertions were corrected and are not counted as successful runs. Red/development
logs and phase evidence remain referenced in Git history and the phase progress.

## Availability and audit limits

Retained healthy-window logs show no ERROR, unexpected HALT or child restart. Safe
request/actor IDs are present on verified denial logs. Successful mutations/audit
commit atomically; anonymous polling/auth failures use platform logs. Denial-history
writes are bounded: DB unavailability/admission timeout can prevent a DB audit row,
with explicit correlated `admin_audit_unavailable` logs. No invented audit backfill
or claim of complete DB history during an outage is made.

Recovered slow probes remain: **3 engine episodes in mixed** (longest observed
transition 3.023 s), **22 episodes in soak** (longest 1.627 s), none in warmup/burst.
All recorded episodes recovered inside their window. These are supervisor transition
intervals, not measured quote downtime. Capture has bounded buffers/queues/span loss;
fill/fee/position agreement and sampled activity do not prove continuous two-sided
quotes or establish an exclusive cause. The historical single HTTP timeout did not
recur in this final gate, but its root cause remains unproven.

API and engine are separate processes with bounded work/pools, sharing CPU/PostgreSQL
by design. Reporting overload does not itself request a trading Stop. Shared-resource
exhaustion can still affect both; there is no absolute uptime guarantee.

## External acceptance still open

Real Google sign-in/certificate discovery, real GCP KMS/IAM with synthetic credentials,
and a separately reviewed managed-credential cutover of the existing OKX Demo ledger
need the operator's resources. The operator confirmed local/offline continuation.
Unavailable checks are not passes. The exchange API key must still be deleted on
OKX by its owner; Python reference removal is not physical memory zeroization and
backups can retain old encrypted rows.

See [FE integration](../../docs/fe-integration.md), [credential operations](../../docs/exchange-credentials.md),
[phase progress](../implementation/ADMIN_ACCESS_CREDENTIALS_PROGRESS.md) and the
[implementation checklist](../../docs/plans/ADMIN_ACCESS_OKX_CREDENTIALS_TDD_PLAN.md).
