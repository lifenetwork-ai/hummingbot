# Documentation

Guides describe how the current code works and how to operate or verify it.
Implementation history, audits, measured results and acceptance artifacts live
in [reports/](../reports/README.md). Disposable logs and raw run artifacts remain
under the ignored `build/` directory.

## Guides

| Document | Purpose |
|---|---|
| [PostgreSQL runtime](postgres-runtime.md) | Environment, migrations, storage, ownership, recovery and funding observation |
| [Cloud Run runtime image](cloudrun-runtime.md) | Build, packaging, image references and runtime caches |
| [Cloud Run deployment](../deploy/cloudrun/README.md) | Service configuration and deployment verification |
| [Admin API](admin-api.md) | Authentication, commands, reporting and reconciliation |
| [OKX environments](okx-environments.md) | Live/Demo routing, sites and credential variables |
| [Local load testing](life-mm-load-tests.md) | Commands, workload, budgets and verification policy |

## Plans and API contract

- [PostgreSQL / Cloud Run port plan](plans/POSTGRES_CLOUD_RUN_PORT_PLAN.md)
- [LIFE / OKX implementation plan](plans/LIFE_OKX_IMPLEMENTATION_PLAN_TDD.md)
- [LIFE / OKX incremental enhancements — TDD plan](plans/LIFE_OKX_MM_ENHANCEMENTS_TDD_PLAN.md)
- [OpenAPI contract](api/openapi.json), generated from API handlers/models with
  `make swagger` and checked with `make swagger-check`. This JSON is API
  documentation, rather than a test-result artifact.
