import { describe, expect, it } from 'vitest'
import { deriveDecision } from './deriveDecision'
import type { CapitalRiskView, LiquidityMarketView } from './supplement'

function metMarket(overrides: Partial<LiquidityMarketView> = {}): LiquidityMarketView {
  return {
    market: 'spot',
    pair: 'LIFE-USDT',
    twoSided: true,
    spreadActualBps: 30,
    spreadTargetBps: 40,
    bestBid: '0.0411',
    bestAsk: '0.0414',
    bidNotional: '6000',
    askNotional: '5900',
    buyOrderCount: 3,
    sellOrderCount: 3,
    coverageFraction: 0.995,
    coverageTargetFraction: 0.99,
    rollingCoverageAvailable: true,
    fullWindow: true,
    why: {
      elapsedSeconds: 1800,
      eligibleSeconds: 1790,
      notEligibleSeconds: 5,
      unknownSeconds: 5,
      quotingAbsentSeconds: 3,
      diagnosis: null,
    },
    depth: { available: false, reason: null },
    slippage: { available: false, reason: null },
    ...overrides,
  }
}

const baseCapital: CapitalRiskView = {
  positions: [],
  netPosition: { pair: 'LIFE-USDT', side: 'FLAT', amount: '0', pnlUsdt: null, observed: true },
  portfolio: { available: false, reason: null },
  sessionDelta: { available: false, reason: null },
  fees: { available: false, reason: null },
  holdBenchmark: { available: false, reason: null },
  balances: [],
  guardsSummary: { status: 'ok', headline: 'ok', violationCount: 0, totalCount: 8, available: true },
}

describe('deriveDecision', () => {
  it('marks liquidity rows met when all markets meet spread & coverage targets', () => {
    const d = deriveDecision([metMarket(), metMarket({ market: 'perpetual' })], baseCapital)
    expect(d.rows.find((r) => r.goal === 'Trade facilitation')?.verdict).toBe('met')
    expect(d.rows.find((r) => r.goal === 'Liquidity maintained')?.verdict).toBe('met')
  })

  it('marks spread not_met when a market exceeds target spread', () => {
    const d = deriveDecision(
      [metMarket(), metMarket({ market: 'perpetual', spreadActualBps: 62, spreadTargetBps: 35 })],
      baseCapital,
    )
    expect(d.rows.find((r) => r.goal === 'Trade facilitation')?.verdict).toBe('not_met')
    expect(d.overall).toBe('not_met')
  })

  it('treats one-sided market as partial coverage', () => {
    const d = deriveDecision([metMarket({ twoSided: false })], baseCapital)
    expect(d.rows.find((r) => r.goal === 'Liquidity maintained')?.verdict).toBe('partial')
    expect(d.overall).toBe('attention')
  })

  it('reports unknown for fees and benchmark when unavailable', () => {
    const d = deriveDecision([metMarket()], baseCapital)
    expect(d.rows.find((r) => r.goal === 'Capital & costs reasonable')?.verdict).toBe('unknown')
    expect(d.rows.find((r) => r.goal === 'Performance understood')?.verdict).toBe('unknown')
  })

  it('compares session PnL against hold benchmark when present', () => {
    const d = deriveDecision([metMarket()], {
      ...baseCapital,
      holdBenchmark: { available: true, stale: false, observedAt: null, value: { baseline_at: '', basis: 'initial_spot_assets', hold_pnl_usdt: '+610.00', initial_life: '0', initial_usdt: '0', outperform_usdt: '+430.80', session_pnl_usdt: '+1040.80', transfer_treatment: 'excluded_from_both', valuation: { currency: 'USDT', prices: [], valuation_mark: 'mid', valued_at: '' } } },
    })
    const row = d.rows.find((r) => r.goal === 'Performance understood')
    expect(row?.verdict).toBe('met')
    expect(row?.note).toContain('beats benchmark')
  })

  it('flags inventory breach as not_met risk', () => {
    const d = deriveDecision([metMarket()], {
      ...baseCapital,
      positions: [{
        market: 'perpetual', pair: 'LIFE-USDT', side: 'LONG', amount: '700000',
        entry: '0.0412', pnlUsdt: null, marginUsdt: null, liqPrice: null, inventoryLimit: '600000',
      }],
    })
    expect(d.rows.find((r) => r.goal === 'Risk within limits')?.verdict).toBe('not_met')
  })
})

describe('supplement completeness gates', () => {
  it.each([false, true])('never marks incomplete or stale fees as met (stale=%s)', (stale) => {
    const capital: CapitalRiskView = {
      ...baseCapital,
      fees: { available: true, stale, observedAt: null, value: {
        total_usdt: '0.12', by_market: null, conversion_basis: 'at_fill', history_complete: stale, through_at: '',
      } },
    }
    expect(deriveDecision([metMarket()], capital).rows.find((r) => r.goal === 'Capital & costs reasonable')?.verdict).toBe('unknown')
  })

  it.each([
    { coverageTargetFraction: null },
    { fullWindow: false },
    { rollingCoverageAvailable: false },
  ])('does not turn missing rolling SLA evidence into success: %j', (missing) => {
    const decision = deriveDecision([metMarket(missing)], baseCapital)
    expect(decision.rows.find((r) => r.goal === 'Liquidity maintained')?.verdict).toBe('unknown')
  })
})
