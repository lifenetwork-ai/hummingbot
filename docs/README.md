# Documentation

Guides describe how the current code works and how to operate or verify it.
Implementation history, audits, measured results and acceptance artifacts live
in [reports/](../reports/README.md). Disposable logs and raw run artifacts remain
under the ignored `build/` directory.

## Guides

| Document | Purpose |
|---|---|
| [FE integration](fe-integration.md) | API/auth, operator UX, API/YAML ownership, working orders, history/charts and browser acceptance |
| [Cloud Run operations](cloudrun.md) | Runtime images, service/deployment configuration, logging and release verification |
| [PostgreSQL operations](postgres-runtime.md) | Environment, migrations, persistence, ownership/recovery and idle funding observation |
| [OKX operations](okx-operations.md) | Live/Demo routing, credentials, enabled Demo preset and blocked-quote runbook |
| [Exchange credential encryption](exchange-credentials.md) | Source selection, local/GCP keys, envelope scope and controlled key transition |
| [MM controls](life-mm-controls.md) | Quote guards, drawdown, spot outflow, listing bootstrap, inventory skew and fee/NATR spreads |
| [Local load testing](life-mm-load-tests.md) | Commands, workloads, declared budgets and verification policy |
| [Economic stress replay](life-mm-economic-stress.md) | Pre-listing synthetic execution assumptions, accounting and metric limitations |

## Plans and API contract

- [Current delivery and remaining verification](plans/README.md)
- [PostgreSQL / Cloud Run](plans/POSTGRES_CLOUD_RUN_PORT_PLAN.md)
- [Admin access / credentials](plans/ADMIN_ACCESS_OKX_CREDENTIALS_TDD_PLAN.md)
- [Actual Demo versus current offline image](plans/OKX_DEMO_CURRENT_IMAGE_ACCEPTANCE.md)
- [Latest runtime TDD checklist](plans/MM_RUNTIME_FOLLOWUP_REMEDIATION_TDD_PLAN.md)
- [Historical plans/reports in Git](../reports/README.md#historical-records)
- [OpenAPI contract](api/openapi.json), generated with `make swagger` and checked
  with `make swagger-check`; this remains API documentation, not a result artifact.
