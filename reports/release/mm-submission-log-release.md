# Submission-log image release verification

Date: 2026-11-01 (Asia/Saigon). Status: **declared local gates complete; Demo upgraded, trading stopped**.

Candidate `a190b024` packages application source `4af96c0ee` as a local derivative
of accepted image `3f5cbd0f`. Exactly four Python modules change; native dependencies
are unchanged. This is not a clean full Dockerfile rebuild. After the declared
gates pass, the canonical Demo service is upgraded from `3f5cbd0f` while trading
is stopped. Portable [evidence](mm-submission-log-release-evidence.json) retains
image/source hashes, exact measurements and local raw-result fingerprints.

## Completed packaging checks

The four source/package hashes match, and actual imported module paths resolve
to those Python files. A network-disabled packaged smoke exercises connector
failure handling, the real order-tracker failure event, position executor and
JSON formatter. Session closure logs INFO, readiness waits WARNING, and generic
unsafe guards / lost ownership ERROR. Reason codes, redaction, FAILED state and
retry bookkeeping all pass. The initial smoke launcher omitted the application
import path; adding the explicit packaged PYTHONPATH fixed that launcher without
changing the image. Both outcomes remain in raw evidence.

Source-level TDD/regression results are in
[the logging review](../platform/submission-block-logging.md). Earlier image load
and connected Demo results retain their separate scopes.

## Targeted simulator E2E

All three targeted groups pass on the same image and manifest `5962a8dc`:
spot-outflow (`20261101-214207-9168c4`), funding (`20261101-214333-d65135`), and
LIFE-MM (`20261101-214546-1646a7`). The existing packaged runtime/ABI/JIT/TLS
smoke passes. These targeted results are supplemented by the independent R1/R2
checks below; this does not claim a rerun of the entire E2E matrix.

## Full R1 — passed

Run `20261101-215045-297059` passes all declared healthy windows, each with zero
unexpected API errors and no budget violations:

| Window | Duration | Read p95 | Read p99 |
| --- | --- | --- | --- |
| Warmup | 300s | 61.89ms | 370.21ms |
| Mixed | 900s | 60.30ms | 177.01ms |
| Burst | 90s | 66.92ms | 388.07ms |
| Soak | 3,600s | 95.96ms | 643.14ms |

All six fault cases pass: feed, volatility defense, owner restart, queued-command
expiry, database delay and database failure. Independent verification confirms
unchanged source/image, resource limits and fixture scope. The cumulative ledger
matches 4,885 unique fills and positions. The hour has 58 samples, maximum sample
gap 65.95s, retention growth 14.39 MiB (budget 64 MiB) and container peak 759.58 MiB.
Raw cgroup growth is 63.97 MiB; retention also accounts for file/shared memory.
Random actually selects all three profiles across six steps.

Healthy-window logs contain 77 recovered probe episodes (longest 4.957s) and no
child replacement. The offline fallback sends three engine SIGUSR2 stack-dump
requests after three consecutive failed probes. These are measurement
interventions, not runtime repairs, and the trace cannot be described as entirely
passive. Their presence does not establish the cause of those delays or of the
older failed burst. The ordinary-entrypoint R2 control excludes this fallback.

At the end of soak, the API trace has reached its 32 MiB cap and dropped 30 records;
engine/API child-span drops are 316,497/14,859. Incident rings retain bounded
history (111 engine and 64 API incidents, zero suppressed incidents). These
limits prevent claiming complete diagnostic coverage despite a passing gate.

The earlier burst failure is not explained by this new passing burst window.

## Matched ordinary-entrypoint R2 — passed

Run `production-control-20261101-232026-8c99d2` uses the same immutable image,
resources, fixtures, historical dataset and cache policy as R1. It starts the
ordinary service entrypoint without the load instrumentation fallback.

| Window | Duration | Read p95 | Read p99 | Unexpected errors |
| --- | --- | --- | --- | --- |
| Warmup | 300s | 75.32ms | 848.64ms | 0 |
| Mixed | 900s | 55.97ms | 347.47ms | 0 |

Both windows pass unchanged budgets; 1,200 unique fills and positions reconcile.
Independent verification confirms stable supervisor/engine/API PIDs and 1,221
frozen source files. There are 16 recovered probe episodes (longest 4.056s), no
child replacement and no diagnostic signal. This is a 20-minute matched control,
not a second full-hour soak. Raw `diagnostic=true` and
`release_scope_complete=false` flags are preserved; eligibility comes from the
passing full R1 baseline and the independently checked matched control scope.

## Stopped Demo upgrade — passed

Fresh read-only preflight before and after the upgrade verifies both credential
sets, REST authentication, public book streams for BTC/ETH/AVAX spot and BTC
perpetual, and private spot/perpetual order subscriptions. There are no pending
exchange orders or admin commands. No exchange/account mutation is issued.

The persistent DB has a private backup, and the previous container is preserved
stopped. Canonical `hummingbot-local-demo` now runs `a190b024` with revision
`4af96c0ee`; the private YAML/environment files are unchanged. Order, fill,
executor and position fingerprints match both the pre-gate snapshot and the
pre-upgrade snapshot; control state is unchanged. DB counts remain 850 orders,
six historical fills and three position rows. Perpetual long 0.0084 BTC and
short 0.0027 BTC match exchange contract-to-base conversion.

`/startup` and `/live` return 200. `/ready` returns 503 with `state=stopped`, as
expected while trading is deliberately stopped. There is no HALT or unresolved
mutation. The reviewed startup logs contain 22 INFO records and no ERROR records.
This verifies stopped upgrade/connectivity, not a new active-session or natural
fill acceptance. Earlier connected trading results retain their original image
scope in the [Demo report](../demo/okx-demo-latent-fixes.md).

Rollback material is in the private `~/.config/hummingbot/log-release-demo-*`
archive (backup and effective runtime environment), with the predecessor
container named `hummingbot-demo-before-log-release-*`. The local raw upgrade
review identifies the exact archive/container. Rolling back the image while
stopped does not require restoring the unchanged ledger; any later DB restore
needs its own operator review because it can overwrite subsequent history.

## Declared gates

1. Targeted spot-outflow, funding and LIFE-MM simulator E2E on the same image,
   including the existing runtime/ABI smoke. This is not the full E2E matrix.
2. Full unchanged R1: warmup300 / mixed900 / burst90 / soak3600, then all six
   injected recovery cases. Preserve any failed attempt. Freeze source/image,
   two CPU / 2 GiB application, 100,000 historical fills, pool/cache policy,
   TTL15 and incident cap128. No rate, duration, latency or memory-budget change.
3. Matched ordinary-entrypoint R2 warmup300 / mixed900 against a passing full R1;
   independently verify resources, fixtures, ledgers and process identities.
   R2 is a control, not another full-hour soak.
4. Fresh read-only Demo preflight, private backup and stopped upgrade, preserving
   trading configuration and durable history. Verify health, recovery, image/
   revision and unchanged ledger fingerprints; retain the predecessor stopped.

Healthy gates retain zero unexpected API errors, p95 <=500ms and p99 <=1000ms,
64 MiB retention growth and 1.5 GiB container peak. Retention excludes file cache
but includes tmpfs/shared memory: `current - (file - shmem)`. Publish raw memory
and actual Random profile coverage as well. Matching fills and stable process IDs
do not prove uninterrupted quoting, and one-hour retention does not prove
unlimited leak-free uptime. Injected fault intervals are excluded from healthy
latency measurements; ambiguous effects retain recovery/HALT.

No controlled Demo Start, new natural-fill claim, live account, HALT reset, DB
repair or Cloud Run deployment is included. The earlier burst p99 root cause
remains open; a later passing gate cannot explain or erase it.

Raw evidence: `build/submission-log-release-20261101/`. Temporary gate containers
and networks are cleaned up; the persistent Demo DB, volumes and stopped
predecessor remain. The earlier unexplained burst and historical H1/H2/H3 remain
attribution limits; no new runtime fix or causal explanation is inferred from
this passing release verification.
