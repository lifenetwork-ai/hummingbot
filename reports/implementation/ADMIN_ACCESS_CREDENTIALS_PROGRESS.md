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

Latest source `d9488143d` passes native2,780 + 11 subtests and full PG547. See
[source details](MM_RUNTIME_FOLLOWUP.md#residual-probes-ownership-reads--2026-11-07);
this does not inherit the preceding image's hour gate.
Image `837e7997…` passes the unchanged full security/operator load, sixty-minute
soak/five faults, four targeted E2E groups after one disclosed restart retry, and
enhancement90. Signed Google
identities/certificates and KMS in this run are local/offline fixtures; Basic is
disabled during measurement and managed keys use local encryption without fallback.
[Current image report](../release/mm-runtime-followup-load.md),
[evidence](../release/mm-runtime-followup-load-evidence.json).

Previous managed rotation/partial-close/crash/repair E2E results stay attached to
their original images in the historical journal in Git. They are not relabelled as additional
current-image scenarios. The existing Demo is not targeted by these tests.
