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
    totalTimeCoverageFraction: 0.995,
    coverageTargetFraction: 0.99,
    rollingCoverageAvailable: true,
    measurementReady: true,
    eligibleSeconds: 1790,
    restrictedSeconds: 5,
    eligibilityUnknownSeconds: 5,
    measurementReason: null,
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
  netPosition: { pair: 'LIFE-USDT', side: 'FLAT', amount: '0', inventoryLimit: null, pnlUsdt: null, observed: true },
  portfolio: { available: false, reason: null },
  sessionDelta: { available: false, reason: null },
  fees: { available: false, reason: null },
  holdBenchmark: { available: false, reason: null },
  balances: [],
  guardsSummary: { status: 'ok', headline: 'ok', violationCount: 0, totalCount: 8, available: true },
}

describe('deriveDecision', () => {
  it('reports no problems when all markets meet spread & coverage targets (fees unknown is a data gap, not a problem)', () => {
    const d = deriveDecision([metMarket(), metMarket({ market: 'perpetual' })], baseCapital)
    expect(d.hasProblems).toBe(false)
    expect(d.issues.filter((issue) => issue.kind === 'problem')).toEqual([])
    expect(d.dataGapCount).toBe(1) // session fees
  })

  it('flags a market that exceeds its spread target', () => {
    const d = deriveDecision(
      [metMarket(), metMarket({ market: 'perpetual', spreadActualBps: 62, spreadTargetBps: 35 })],
      baseCapital,
    )
    const mandate = d.issues.find((issue) => issue.area === 'Liquidity mandate')
    expect(mandate?.kind).toBe('problem')
    expect(mandate?.detail).toContain('Perp market below target mandate')
    expect(d.hasProblems).toBe(true)
  })

  it('flags a one-sided market', () => {
    const d = deriveDecision([metMarket({ twoSided: false })], baseCapital)
    const quoting = d.issues.find((issue) => issue.area === 'Spot quoting')
    expect(quoting?.kind).toBe('problem')
    expect(quoting?.detail).toContain('one-sided')
  })

  it('reports fees as a data gap when unavailable', () => {
    const d = deriveDecision([metMarket()], baseCapital)
    expect(d.issues.find((issue) => issue.area === 'Session fees')?.kind).toBe('data_gap')
  })

  it('uses conservative eligible coverage instead of total-time coverage for the target', () => {
    const d = deriveDecision([metMarket({ coverageFraction: 0.98, totalTimeCoverageFraction: 0.999 })], baseCapital)
    const mandate = d.issues.find((issue) => issue.area === 'Liquidity mandate')
    expect(mandate?.kind).toBe('problem')
    expect(mandate?.detail).toContain('Spot market below target mandate')
  })

  it('flags inventory breach as a problem', () => {
    const d = deriveDecision([metMarket()], {
      ...baseCapital,
      positions: [{
        market: 'perpetual', pair: 'LIFE-USDT', side: 'LONG', amount: '700000',
        entry: '0.0412', pnlUsdt: null, marginUsdt: null, liqPrice: null, inventoryLimit: '600000',
      }],
    })
    expect(d.issues.find((issue) => issue.area === 'Inventory')?.kind).toBe('problem')
  })

  it('summarizes guard status as problem (attention/stop) or data gap (unavailable)', () => {
    const attention = deriveDecision([metMarket()], { ...baseCapital, guardsSummary: { status: 'attention', headline: 'Drawdown approaching limit', violationCount: 1, totalCount: 8, available: true } })
    expect(attention.issues.find((issue) => issue.area === 'Risk guards')?.kind).toBe('problem')

    const stop = deriveDecision([metMarket()], { ...baseCapital, guardsSummary: { status: 'stop', headline: 'Drawdown limit hit', violationCount: 2, totalCount: 8, available: true } })
    expect(stop.issues.find((issue) => issue.area === 'Risk guards')?.kind).toBe('problem')

    const unavailable = deriveDecision([metMarket()], { ...baseCapital, guardsSummary: { status: 'ok', headline: 'Guards data unavailable', violationCount: 0, totalCount: 0, available: false } })
    expect(unavailable.issues.find((issue) => issue.area === 'Risk guards')?.kind).toBe('data_gap')
  })
})

describe('supplement completeness gates', () => {
  it.each([
    { stale: false, history_complete: false, processing_state: 'ready' as const },
    { stale: false, history_complete: true, processing_state: 'catching_up' as const },
    { stale: true, history_complete: true, processing_state: 'ready' as const },
  ])('never treats stale, catching-up, or incomplete fees as sufficient data: %j', (feeState) => {
    const capital: CapitalRiskView = {
      ...baseCapital,
      fees: { available: true, stale: feeState.stale, observedAt: null, value: {
        total_usdt: '0.12', by_market: null, conversion_basis: 'at_fill', history_complete: feeState.history_complete, through_at: '',
        backfill_processed_fill_count: null, caught_up_at: null, processed_fill_count: null,
        processing_projection_version: null, processing_state: feeState.processing_state, reported_projection_version: null,
      } },
    }
    expect(deriveDecision([metMarket()], capital).issues.find((issue) => issue.area === 'Session fees')?.kind).toBe('data_gap')
  })

  it.each([
    { coverageTargetFraction: null },
    { fullWindow: false },
    { rollingCoverageAvailable: false },
    { measurementReady: false },
    { eligibleSeconds: 0 },
  ])('does not turn missing rolling SLA evidence into a problem or success: %j', (missing) => {
    const decision = deriveDecision([metMarket(missing)], baseCapital)
    expect(decision.hasProblems).toBe(false)
    expect(decision.issues.find((issue) => issue.area === 'Liquidity mandate')).toBeUndefined()
  })
})
