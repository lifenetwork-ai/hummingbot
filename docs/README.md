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
| [MM controls](life-mm-controls.md) | Quote guards, drawdown, spot outflow, listing bootstrap, inventory skew and fee/NATR spreads |
| [Local load testing](life-mm-load-tests.md) | Commands, workloads, declared budgets and verification policy |
| [Economic stress replay](life-mm-economic-stress.md) | Pre-listing synthetic execution assumptions, accounting and metric limitations |

## Plans and API contract

- [PostgreSQL / Cloud Run port plan](plans/POSTGRES_CLOUD_RUN_PORT_PLAN.md)
- [LIFE / OKX implementation plan](plans/LIFE_OKX_IMPLEMENTATION_PLAN_TDD.md)
- [LIFE / OKX incremental enhancements — TDD plan](plans/LIFE_OKX_MM_ENHANCEMENTS_TDD_PLAN.md)
- [Pre-listing economic stress plan](plans/LIFE_MM_ECONOMIC_STRESS_PLAN.md)
- [Operations API and configuration ownership plan](plans/ADMIN_OPERATIONS_API_TDD_PLAN.md)
- [Working-order activity and diagnostics plan](plans/MM_QUOTE_ACTIVITY_TDD_PLAN.md)
- [Until-stopped MM sessions plan](plans/MM_UNTIL_STOPPED_TDD_PLAN.md)
- [MM memory retention review plan](plans/MM_MEMORY_RETENTION_REVIEW.md)
- [OKX WS position startup TDD plan](plans/OKX_WS_POSITION_STARTUP_TDD_PLAN.md)
- [OpenAPI contract](api/openapi.json), generated from API handlers/models with
  `make swagger` and checked with `make swagger-check`. This JSON is API
  documentation, rather than a test-result artifact.
