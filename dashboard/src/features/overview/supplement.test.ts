import { describe, expect, it } from 'vitest'
import type { OverviewSupplementSchemas } from './supplement-contract'
import { deriveOverview } from './model'
import { deriveCapitalRisk } from './supplement'

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
        lower_limit: null, upper_limit: '50', current: '12', status: 'ok' }] },
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
