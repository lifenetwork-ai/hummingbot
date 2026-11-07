# Admin access and credential delivery status

Updated 2026-11-07. The [full A1–A6 journal](../README.md#historical-records) (Git: `reports/implementation/ADMIN_ACCESS_CREDENTIALS_PROGRESS.md`) retains detailed
Red/Green runs, lifecycle cases, rejected attempts and image identities.
[Current credential plan](../../docs/plans/ADMIN_ACCESS_OKX_CREDENTIALS_TDD_PLAN.md)
tracks remaining acceptance; [operations](../../docs/exchange-credentials.md) and
[FE integration](../../docs/fe-integration.md) define the current contract.

A1–A6 local/offline implementation covers optional Basic bootstrap, verified Google
identity/allowlists, fresh role lookup, envelope encryption (`local`/`gcp` adapters),
scoped Set/load/rotation, cancel/close/remove cleanup, durable Retry/Repair,
correlated audit, secret-free status/errors and bounded workers/query counts.
Real Google/GCP and reviewed existing-Demo cutover remain open.

The current combined runtime image `a6fe88c0…` passes unchanged security/operator
load, sixty-minute soak/five faults, four targeted E2E groups, enhancement90 and
separate activity300. Soak p99 406.14 ms, zero unexpected healthy errors,4,885 exact
simulated fills; soak engine11/mixed engine3 probes recover. Probe frequency and
burst p99 regress versus accepted rollback `e00cef38…`; attribution remains open.
Previous report stays in Git `745a86e3c`; earlier failures remain in `ff6b85ded`.
Signed identities/certificates and KMS are local/offline fixtures; Basic is disabled
during measurement and managed keys use local encryption without fallback.
[Current image report](../release/mm-runtime-followup-load.md),
[evidence](../release/mm-runtime-followup-load-evidence.json),
[source verification](MM_RUNTIME_FOLLOWUP.md).

Previous managed rotation/partial-close/crash/repair E2E results stay attached to
their original images in the historical journal in Git. They are not relabelled as additional
current-image scenarios. The existing Demo is not targeted by these tests.
