import { describe, expect, it } from 'vitest'
import type { OverviewSupplementSchemas } from './supplement-contract'
import { deriveOverview } from './model'
import { deriveCapitalRisk, deriveMandate } from './supplement'

type Supplement = OverviewSupplementSchemas['OverviewSupplementOutput']
const unknown = { value: null, stale: true, observed_at: null, unavailable_reason: 'unavailable' }
const loading = { phase: 'loading' as const, data: undefined, error: null, refreshing: false }

function supplement(): Supplement {
  return {
    contract_version: 1, server_time: '2026-11-13T00:00:00Z',
    account_scope: { account_ids: ['17'], environment: 'demo', identity_verified: true, site: null },
    session_id: 'session', session_started_at: null,
    session: { capital_allocated: unknown, delta: unknown, benchmark_hold: unknown, fees: unknown },
    portfolio: unknown, balances: unknown, inventory_limits: unknown, references: unknown, markets: [],
    risk_rules: { observed_at: '2026-11-13T00:00:00Z', stale: false, unavailable_reason: null,
      value: [{ controller_id: null, market: null, rule_id: 'drawdown_global', unit: 'USDT',
        lower_limit: null, upper_limit: '50', current: '12', status: 'ok', unavailable_reason: null }] },
  }
}

const model = deriveOverview({ status: loading, liquidity: loading, positions: loading, balances: loading })

describe('supplement risk completeness', () => {
  it.each(['partial', 'stale'])('does not claim all guards are stable for %s risk data', (reason) => {
    const data = supplement()
    data.risk_rules.unavailable_reason = reason === 'partial' ? 'risk_policy_observation_incomplete' : null
    data.risk_rules.stale = reason === 'stale'
    const summary = deriveCapitalRisk(model, data).guardsSummary
    expect(summary.available).toBe(false)
    expect(summary.status).toBe('attention')
  })

  it('keeps a known violation visible even if other risk rules are missing', () => {
    const data = supplement()
    data.risk_rules.value![0].status = 'violated'
    data.risk_rules.unavailable_reason = 'risk_policy_observation_incomplete'
    expect(deriveCapitalRisk(model, data).guardsSummary.status).toBe('stop')
  })

  it('keeps stale or timestamp-free observations unavailable without affecting independent fresh metrics', () => {
    const data = supplement()
    data.portfolio = { observed_at: '2026-11-13T00:00:00Z', stale: true, unavailable_reason: null, value: {
      total_usdt: '10', life_usdt: '4', usdt: '6', other_assets_usdt: '0',
      valuation: { currency: 'USDT', prices: [], valuation_mark: 'mid', valued_at: '2026-11-13T00:00:00Z' },
    } }
    data.session.fees = { observed_at: '2026-11-13T00:00:00Z', stale: false, unavailable_reason: null, value: {
      total_usdt: '0.01', by_market: null, conversion_basis: 'at_fill', history_complete: true,
      through_at: '2026-11-13T00:00:00Z', backfill_processed_fill_count: 1, caught_up_at: '2026-11-13T00:00:00Z',
      processed_fill_count: 1, processing_projection_version: 1, processing_state: 'ready', reported_projection_version: 1,
    } }
    const capital = deriveCapitalRisk(model, data)
    expect(capital.portfolio).toMatchObject({ available: false, reason: 'stale_observation' })
    expect(capital.fees).toMatchObject({ available: true, stale: false })

    data.portfolio = { observed_at: null, stale: false, unavailable_reason: null, value: {
      total_usdt: '10', life_usdt: '4', usdt: '6', other_assets_usdt: '0',
      valuation: { currency: 'USDT', prices: [], valuation_mark: 'mid', valued_at: '2026-11-13T00:00:00Z' },
    } }
    expect(deriveCapitalRisk(model, data).portfolio).toMatchObject({ available: false, reason: 'observation_time_unavailable' })

    data.session.fees = { ...data.session.fees, stale: true, unavailable_reason: 'refreshing' }
    expect(deriveCapitalRisk(model, data).fees).toMatchObject({ available: true, stale: true })
  })
})

it('counts unknown guards separately from breaches', () => {
  const data = supplement()
  data.risk_rules.value!.push({ ...data.risk_rules.value![0], rule_id: 'inventory_long', controller_id: 'perp', market: 'perpetual', status: 'unknown', current: null })
  expect(deriveCapitalRisk(model, data).guardsSummary).toMatchObject({ violationCount: 0, unknownCount: 1, checkedCount: 1, status: 'attention' })
})

it('does not claim guards stable before any observation', () => {
  expect(deriveCapitalRisk(model, null).guardsSummary.status).toBe('attention')
})


it('preserves controller scope, pinned quote budget and separate USD equity', () => {
  const data = supplement()
  data.risk_rules.value![0].controller_id = 'spot-controller'
  data.risk_rules.value![0].market = 'spot'
  data.session.quote_budget = { value: { total_usdt: '200', by_market: [{ market: 'spot', budget_usdt: '100' }, { market: 'perpetual', budget_usdt: '100' }], source: 'pinned_session_catalogue', basis: 'configured_working_orders_notional' }, observed_at: data.server_time, stale: false, unavailable_reason: null }
  data.account_equity = { value: { total_usd: '12345.67', currency: 'USD', source: 'okx_account_balance_total_eq' }, observed_at: data.server_time, stale: false, unavailable_reason: null }
  expect(deriveMandate(model, data)).toMatchObject({ quoteBudget: { available: true, value: { total_usdt: '200' } }, capitalAllocated: { available: false }, riskLimits: [{ controllerId: 'spot-controller', market: 'spot' }] })
  expect(deriveCapitalRisk(model, data).accountEquity).toMatchObject({ available: true, value: { total_usd: '12345.67', currency: 'USD' } })
  data.account_equity.stale = true
  expect(deriveCapitalRisk(model, data).accountEquity).toMatchObject({ available: false })
})
