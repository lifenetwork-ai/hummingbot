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
Real Google admin login now passes in the isolated helper. Real GCP KMS/IAM and
reviewed existing-Demo cutover remain open.

2026-11-09 real-Google preparation: added a loopback-only acceptance page reusing
the existing admin handlers/verifier/audit, with a separate `hbot_auth_google`
PostgreSQL database and an operator-specified admin invitation. No trading engine
or exchange-credential endpoints are exposed. Helper/identity tests: 53 pass;
real public-certificate discovery: HTTP 200 with two certificates.

Real browser login succeeded at `2026-11-08T22:07:39.754690Z`. The operator-pasted
result was independently checked through the existing account/bootstrap/audit
APIs: active bound Google admin, expected email and role, and `account_bound` /
`login` succeeded with the same request ID. Only the helper's private env was
changed to disable Basic; after restart, the previous Basic credential returns
401 `basic_auth_disabled`. A second real Google browser login after this restart
succeeds at `2026-11-08T22:09:59.629386Z`. Its request ID and actor match durable
audit; Basic was independently checked again and remains rejected. The operator
result reports `basic_auth_enabled=false`, `google_auth_enabled=true` and
`google_admin_ready=true`. [Portable evidence](google-login-acceptance.json)
contains no ID token or credential. Detailed local evidence is retained at
`build/google-login-local/real-login-evidence.json`; no account is provisioned in
a trading ledger and no trading service settings were changed. This qualifies
the Google admin helper path, not real-account role mutation tests, GCP KMS, a
production gateway/browser deployment or an image-level trading acceptance run.

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
at current release paths. Local encryption does not close real GCP KMS or
actual-Demo cutover. No existing real ledger is queried or
repaired; unrelated local container IDs/states and volume are retained.
[Current report](../release/mm-runtime-followup-load.md),
[evidence](../release/mm-runtime-followup-load-evidence.json),
[source/TDD](MM_RUNTIME_FOLLOWUP.md).
