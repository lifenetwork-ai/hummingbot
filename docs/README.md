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
| [Cloud Run logging](cloudrun-logging.md) | JSON console logs, severity levels, redaction and operational filters |
| [Cloud Run deployment](../deploy/cloudrun/README.md) | Service configuration and deployment verification |
| [Admin API](admin-api.md) | Authentication, commands, reporting and reconciliation |
| [Operations dashboard integration](admin-dashboard-integration.md) | FE screen/API mapping, commands, freshness, polling and operational UX |
| [Session history and liquidity](life-mm-history-liquidity.md) | Atomic transitions, recorded configuration, sampled quote coverage and FE polling |
| [Optional fee/NATR spreads](life-mm-spread-policy.md) | Existing maker fee conversion and completed-candle NATR, fallback/wait and rounding |
| [Strict quote guards](life-mm-quote-guards.md) | Opt-in market deviation, sideways range, own-order filtering and recovery |
| [Managed MM drawdown](life-mm-drawdown.md) | Covered V2 report limits, retained-market valuation, durable peaks and position-preserving HALT |
| [Spot inventory skew](life-mm-inventory-skew.md) | Original PMM interpolation, cash-only signal, bounded sizing and neutral fallback |
| [Listing bootstrap](life-mm-listing-bootstrap.md) | Bounded empty/own-only listing quotes, durable deadline and outside-market qualification |
| [Spot USDT outflow](life-mm-spot-outflow.md) | Rolling committed cashflow, pending BUY reservations, account binding and BUY-only admission |
| [OKX environments](okx-environments.md) | Live/Demo routing, sites and credential variables |
| [Explicit OKX Demo preset](okx-demo-configuration.md) | Enabled switches, BTC-USDT policies, packaging and reason-based recovery |
| [Local load testing](life-mm-load-tests.md) | Commands, workload, budgets and verification policy |
| [Pre-listing economic stress](life-mm-economic-stress.md) | Synthetic paired controller replay, execution assumptions, accounting and limitations |

## Plans and API contract

- [PostgreSQL / Cloud Run port plan](plans/POSTGRES_CLOUD_RUN_PORT_PLAN.md)
- [LIFE / OKX implementation plan](plans/LIFE_OKX_IMPLEMENTATION_PLAN_TDD.md)
- [LIFE / OKX incremental enhancements — TDD plan](plans/LIFE_OKX_MM_ENHANCEMENTS_TDD_PLAN.md)
- [Pre-listing economic stress plan](plans/LIFE_MM_ECONOMIC_STRESS_PLAN.md)
- [Operations API and configuration ownership plan](plans/ADMIN_OPERATIONS_API_TDD_PLAN.md)
- [OpenAPI contract](api/openapi.json), generated from API handlers/models with
  `make swagger` and checked with `make swagger-check`. This JSON is API
  documentation, rather than a test-result artifact.
