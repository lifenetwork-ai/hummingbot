import { describe, expect, it } from 'vitest'
import type { ResponseBody } from '../../core/api/types'
import { deriveOverview } from './model'
import { deriveCapitalRisk } from './supplement'

type Supplement = NonNullable<ResponseBody<'getRuntimeStatus'>['overview']>
const unknown = { value: null, stale: true, observed_at: null, unavailable_reason: 'unavailable' }
const loading = { phase: 'loading' as const, data: undefined, error: null, refreshing: false }

function supplement(): Supplement {
  return {
    contract_version: 1, server_time: '2026-11-13T00:00:00Z',
    account_scope: { account_ids: ['17'], environment: 'demo', identity_verified: true },
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
})
