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

The new runtime image `96368970…` completes unchanged security/operator load and
passes soak3600/five fault assertions, but **fails burst main/auxiliary p99**.
Soak reconciles 4,884 simulated fills with zero unexpected healthy errors and
26.09 MiB retained growth. Twelve healthy probe transitions recover without child
replacement. Corrected `all` fails inventory shutdown; remaining targets pass; a separate host fixture fix
supplies dummy live perpetual credentials, without changing runtime gates.

The previous accepted `a6fe88c0…` gate and earlier scoped lifecycle evidence remain
in Git `8544488ef` and original journals. Offline signed identities/certificates
and local encryption do not replace real Google/GCP acceptance. The current
daemon has no existing Demo service/database; this run does not target a real
account, perform cutover or repair historical Demo data.
[Current image report](../release/mm-runtime-followup-load.md),
[evidence](../release/mm-runtime-followup-load-evidence.json),
[source verification](MM_RUNTIME_FOLLOWUP.md).

Shutdown follow-up: corrected `all` E2E passes fourteen groups before a nonzero
inventory-skew shutdown; all thirteen remaining/targeted groups pass, covering twenty-seven unique
groups across attempts. The failed `all` remains failed. A real-signal
TDD reproducer confirms an Event-lock hazard, fixed in source `1916e048e` (75 related
passes). This is a possible mechanism, not proof of the old exit's unique cause.
`96368970…` predates the fix; new-image qualification remains open. See the
[runtime checklist](../../docs/plans/MM_RUNTIME_FOLLOWUP_REMEDIATION_TDD_PLAN.md).
