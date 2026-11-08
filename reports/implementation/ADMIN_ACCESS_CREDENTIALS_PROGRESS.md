# Admin access and credential delivery status

Updated 2026-11-09. The [full A1–A6 journal](../README.md#historical-records) (Git: `reports/implementation/ADMIN_ACCESS_CREDENTIALS_PROGRESS.md`) retains detailed
Red/Green runs, lifecycle cases, rejected attempts and image identities.
[Current credential plan](../../docs/plans/ADMIN_ACCESS_OKX_CREDENTIALS_TDD_PLAN.md)
tracks remaining acceptance; [operations](../../docs/exchange-credentials.md) and
[FE integration](../../docs/fe-integration.md) define the current contract.

A1–A6 local/offline implementation covers optional Basic bootstrap, verified Google
identity/allowlists, fresh role lookup, envelope encryption (`local`/`gcp` adapters),
scoped Set/load/rotation, cancel/close/remove cleanup, durable Retry/Repair,
correlated audit, secret-free status/errors and bounded workers/query counts.
Real Google/GCP and reviewed existing-Demo cutover remain open.

2026-11-09 real-Google preparation: added a loopback-only acceptance page reusing
the existing admin handlers/verifier/audit, with a separate `hbot_auth_google`
PostgreSQL database and an operator-specified admin invitation. No trading engine
or exchange-credential endpoints are exposed. Helper/identity tests: 53 pass;
real public-certificate discovery: HTTP 200 with two certificates. Browser login,
subject binding and Google-only restart remain pending; no real-login acceptance
is claimed and no account is provisioned in a trading ledger.

Current image **`b090430c…`** passes canonical native 2,861 + 16, all 27 available
E2E in one all execution and unchanged security/operator full load/soak3600/five
faults. Separate technical upgrade/takeover/rollback with a distinct schema-
compatible test predecessor also passes; that failed predecessor is not endorsed
as production rollback.

Zero unexpected healthy errors,4,885 exact simulated fills,30.33 MiB retained
growth. Three healthy engine probes recover without replacement. The pre-existing
signal/Event-lock mechanism is fixed in `1916e048e` and verified in both Linux images;
exclusive historical shutdown/p99 attribution remains unproven.

Failed963 records remain in Git `d0791e563` and earlier accepted a6 in `8544488ef`,
at current release paths. Offline signed identities/local encryption do not close
real Google/GCP or actual-Demo cutover. No existing real ledger is queried or
repaired; unrelated local container IDs/states and volume are retained.
[Current report](../release/mm-runtime-followup-load.md),
[evidence](../release/mm-runtime-followup-load-evidence.json),
[source/TDD](MM_RUNTIME_FOLLOWUP.md).
