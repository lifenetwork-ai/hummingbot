# Submission-log image release verification

Date: 2026-11-01 (Asia/Saigon). Status: **in progress**.

Candidate `a190b024` packages application source `4af96c0ee` as a local derivative
of accepted image `3f5cbd0f`. Exactly four Python modules change; native dependencies
are unchanged. This is not a clean full Dockerfile rebuild. The existing stopped
Demo service remains on `3f5cbd0f` until the declared local gates pass.

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

## Declared remaining gates

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

Raw evidence: `build/submission-log-release-20261101/`. The runner's containers,
networks and sleep assertions are task-owned; the persistent Demo DB and its
volumes are preserved.
