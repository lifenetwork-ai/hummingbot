# Verification results

Start with the [current image/load report](release/mm-runtime-followup-load.md)
and [remaining-work register](../docs/plans/README.md). Operational procedures and
API/FE contracts live in [docs](../docs/README.md).

Current image **bf4e1f77…** passes the unchanged full load/60-minute soak/five faults,
four targeted E2E groups and enabled-enhancement90. Soak p99 **306.91 ms**, zero
unexpected healthy errors, **21.89 MiB** retained growth and **4,885 simulated fills**
reconcile. Three healthy probe episodes recover; no healthy ERROR/child restart.
Mixed/burst timings are slower than the prior run; attribution/continuity limits
remain explicit. One unused image removed; all container references/volumes retained.
[Portable evidence](release/mm-runtime-followup-load-evidence.json).

| Topic | Current entry |
| --- | --- |
| [Release](release/README.md) | Current candidate and retained rollback, exact image/gate scope |
| [Implementation](implementation/README.md) | Concise delivery status and latest TDD/source verification |
| [Performance](performance/README.md) | Current optimization, memory and economic measurements |
| [Demo](demo/README.md) | Actual OKX evidence, approvals and image/account scope |
| [Platform](platform/README.md) | Cloud preparation and operating-guide links |
| [Investigations](investigations/README.md) | Current limits and historical incident lookup |
| [History archive](archive/README.md) | Lossless completed plans, superseded reports and full journals |

Archived JSON/Markdown/SQL bytes and image/date-specific failures are preserved;
use the archive manifest to locate an old path. Archival does not close unresolved
items or certify a newer image. Raw logs/ledgers remain under ignored `build/`;
credentials and private account configuration stay outside the checkout.
