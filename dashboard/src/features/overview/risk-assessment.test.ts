import { describe, expect, it } from 'vitest'
import type { components } from '../../core/api/schema'
import { contextStatus } from '../../test/mocks/contextFixtures'
import { assessRiskRules } from './risk-assessment'

const observedAt = '2026-11-11T00:00:00Z'
const currentGuard = (reason_code: string | null = null, data_quality: 'known_order_filtered' | 'unavailable' = 'known_order_filtered') => ({
  ready: true,
  data_quality,
  evaluated_at: observedAt,
  reason_code,
})
const currentDrawdown = { ready: true, stale: false, observed_at: observedAt, enabled: true, reason_code: null }
const currentOutflow = { ready: true, stale: false, observed_at: observedAt, buy_allowed: true, reason_code: null }

function statusWith(overrides: Record<string, unknown> = {}) {
  return { ...contextStatus, ...overrides } as unknown as components['schemas']['StatusResponseOutput']
}

describe('assessRiskRules', () => {
  it('counts two market quote guards, drawdown, and spot outflow in stable order', () => {
    const assessment = assessRiskRules(statusWith({
      markets: [
        { market: 'spot', pair: 'LIFE-USDT', quote_guard: currentGuard() },
        { market: 'perpetual', pair: 'LIFE-USDT', quote_guard: currentGuard('quote_blocked') },
      ],
      drawdown: { ...currentDrawdown, enabled: false, reason_code: 'ignored_while_disabled' },
      spot_outflow: { ...currentOutflow, buy_allowed: false, reason_code: null },
    }), 'fresh')

    expect(assessment.rules.map(({ label, state }) => [label, state])).toEqual([
      ['Quote Guard — spot (LIFE-USDT)', 'clear'],
      ['Quote Guard — perpetual (LIFE-USDT)', 'blocked'],
      ['Drawdown', 'clear'],
      ['Spot Outflow', 'blocked'],
    ])
    expect(assessment).toMatchObject({ checkedCount: 4, totalCount: 4, violationCount: 2, unverifiedCount: 0 })
    expect(assessment.rules[3]?.reasonCode).toBe('buy_denied')
  })

  it('keeps unavailable market evidence unverified while counting the other rules', () => {
    const assessment = assessRiskRules(statusWith({
      markets: [
        { market: 'spot', pair: 'LIFE-USDT', quote_guard: currentGuard() },
        { market: 'perpetual', pair: 'LIFE-USDT', quote_guard: currentGuard(null, 'unavailable') },
      ],
      drawdown: currentDrawdown,
      spot_outflow: currentOutflow,
    }), 'fresh')

    expect(assessment).toMatchObject({ checkedCount: 3, totalCount: 4, violationCount: 0, unverifiedCount: 1 })
    expect(assessment.rules[1]?.state).toBe('unverified')
  })

  it('requires each drawdown and outflow observation timestamp even when the overall status is fresh', () => {
    const assessment = assessRiskRules(statusWith({
      markets: [{ market: 'spot', pair: 'LIFE-USDT', quote_guard: currentGuard() }],
      drawdown: { ...currentDrawdown, stale: true },
      spot_outflow: { ...currentOutflow, observed_at: null },
    }), 'fresh')

    expect(assessment).toMatchObject({ checkedCount: 1, totalCount: 3, violationCount: 0, unverifiedCount: 2 })
    expect(assessment.rules.map((rule) => rule.state)).toEqual(['clear', 'unverified', 'unverified'])
  })

  it('does not treat absent markets or stale observations as passing rules', () => {
    const allUnknown = assessRiskRules(undefined, 'unknown')
    expect(allUnknown).toMatchObject({ checkedCount: 0, totalCount: 2, violationCount: 0, unverifiedCount: 2 })
    expect(allUnknown.rules.every((rule) => rule.state === 'unverified')).toBe(true)

    const missingMarketGuard = assessRiskRules(statusWith({ markets: [{ market: 'spot', pair: 'LIFE-USDT', quote_guard: null }] }), 'fresh')
    expect(missingMarketGuard).toMatchObject({ checkedCount: 0, totalCount: 3, violationCount: 0, unverifiedCount: 3 })

    const stale = assessRiskRules(statusWith({
      markets: [{ market: 'spot', pair: 'LIFE-USDT', quote_guard: currentGuard() }],
      drawdown: currentDrawdown,
      spot_outflow: currentOutflow,
    }), 'stale')
    expect(stale).toMatchObject({ checkedCount: 0, totalCount: 3, violationCount: 0, unverifiedCount: 3 })
  })
})
