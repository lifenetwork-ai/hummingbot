# Backend Overview reporting — verification

Validated on 2026-11-14 in `codex/backend-overview-reporting`, based on `origin/dev` at `8b46a1cee`. Runtime: Python 3.12.16, Node 22.23.5 (CI major version), disposable PostgreSQL 16 and local Chromium.

## Scope

This branch contains backend reporting, tests, documentation, generated OpenAPI/TypeScript artifacts and three existing fixtures updated with `overview: null`. No Overview UI implementation/layout changes, migration, extra exchange API calls, or changes to order admission/execution/risk enforcement are included. `core/mm_liquidity.py` changes are bounded reporting history only.

Contract and limitations: [overview-reporting.md](../../docs/overview-reporting.md). Fees/recovery are explicitly incomplete; missing allocation/baseline/transfer/reservation sources remain null. Future FE consumers must respect stale/completeness flags and must not invent an SLA target. The existing UI in this branch does not consume the new supplement.

## Results

| Result | Check | Evidence |
| --- | --- | --- |
| PASS | `make swagger` / `make swagger-check` | Generated artifacts and drift checks exit 0. |
| PASS | Backend command below | 389 tests passed; 6 existing Pydantic deprecation warnings. |
| PASS | PostgreSQL command below | 9 tests passed; no DB test skipped. |
| PASS | `pnpm run lint` / `pnpm run typecheck` | Both exit 0 on the isolated branch. |
| PASS | `pnpm run test:coverage` | All 610 tests across 120 files and configured thresholds passed. |
| PASS | `pnpm run build` | Production build passed; existing large-chunk advisory remains. |
| PASS | `pnpm run test:e2e` | All 30 existing Chromium tests passed; none added/changed. |
| PASS | Applicable pre-commit hooks | Whitespace/EOF, file size, flake8, private-key scans, autopep8 and isort. |
| SKIP | YAML / legacy ESLint hooks | No matching files; dashboard ESLint ran separately. |
| PASS | `git diff --check` | Exit 0. |
| SKIP | Full backend repository suite | Affected surface and nearby upstream regressions selected only. |
| SKIP | Deployment / live exchange | No real-account or production verification. |

## Backend

```sh
conda activate hummingbot
python -m pytest -q \
  test/hummingbot/core/test_overview_coverage.py \
  test/hummingbot/core/test_overview_market_observations.py \
  test/hummingbot/core/test_overview_recovery.py \
  test/hummingbot/core/test_mm_liquidity.py \
  test/hummingbot/core/test_mm_liquidity_admission.py \
  test/hummingbot/core/test_mm_quote_activity.py \
  test/hummingbot/model/test_overview_fees.py \
  test/hummingbot/cli/test_admin_overview.py \
  test/hummingbot/cli/test_overview_capture.py \
  test/hummingbot/cli/test_overview_fee_cache.py \
  test/hummingbot/cli/test_admin_observations.py \
  test/hummingbot/cli/test_reporting_snapshot_copy.py \
  test/hummingbot/cli/test_service_availability.py \
  test/hummingbot/cli/test_service_publication.py \
  test/hummingbot/cli/test_mm_service.py \
  test/hummingbot/cli/test_service_control_lifecycle.py \
  test/hummingbot/cli/test_risk_observation_boundaries.py \
  test/hummingbot/cli/test_admin_api.py \
  test/hummingbot/cli/test_admin_quote_activity.py \
  test/hummingbot/cli/test_admin_configuration.py \
  test/hummingbot/cli/test_overview_contract.py \
  test/hummingbot/cli/test_swagger.py \
  test/hummingbot/cli/test_cleanup_exchange_runtime.py \
  test/hummingbot/client/config/test_runtime_config.py \
  test/hummingbot/core/test_uncertain_order_reservations.py \
  test/hummingbot/strategy_v2/executors/test_managed_terminal_publication.py \
  --timeout=30
```

HTTP tests require local socket permission. The tested files include the nearby scoped-recovery regressions already merged into dev.

## PostgreSQL

The executed run used a disposable `hbot_test_overview_pr` database at localhost:55441. The fixture drops and recreates public; provide only a disposable `hbot_test_*` URL.

```sh
HBOT_TEST_POSTGRES_URL="$HBOT_TEST_POSTGRES_URL" python -m pytest -vv \
  test/integration/postgres/test_overview_fees.py \
  test/integration/postgres/test_admin_reporting_optimization.py --timeout=30
```

This proves local scope/aggregation, not completeness of real exchange history.

## Frontend

Run from `dashboard/` with Node 22:

```sh
pnpm run lint
pnpm run typecheck
pnpm run test:coverage
pnpm run build
pnpm run test:e2e
```
