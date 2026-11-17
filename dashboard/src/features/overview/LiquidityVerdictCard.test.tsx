import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { LiquidityMarketView } from './supplement'
import { LiquidityVerdictCard } from './LiquidityVerdictCard'

function market(overrides: Partial<LiquidityMarketView> = {}): LiquidityMarketView {
  return {
    market: 'spot', pair: 'LIFE-USDT', twoSided: true,
    spreadActualBps: 20, spreadTargetBps: 30,
    bestBid: '0.04', bestAsk: '0.041', bidNotional: '100', askNotional: '100',
    buyOrderCount: 1, sellOrderCount: 1,
    coverageFraction: 0.995, totalTimeCoverageFraction: 0.999, coverageTargetFraction: 0.99,
    rollingCoverageAvailable: true, measurementReady: true, eligibleSeconds: 1700,
    restrictedSeconds: 40, eligibilityUnknownSeconds: 60, measurementReason: null, fullWindow: true,
    why: { elapsedSeconds: 1800, eligibleSeconds: 1700, notEligibleSeconds: 40, unknownSeconds: 60, quotingAbsentSeconds: 0, diagnosis: null },
    depth: { available: false, reason: null }, slippage: { available: false, reason: null },
    ...overrides,
  }
}

describe('LiquidityVerdictCard eligible coverage', () => {
  it('shows the eligible and total-time measures with restricted and unknown seconds', () => {
    render(<LiquidityVerdictCard markets={[market()]} mode="operator" />)
    expect(screen.getByText('MET')).toBeInTheDocument()
    expect(screen.getByText(/Total-time coverage 99.9% · eligible coverage 99.5%/)).toBeInTheDocument()
    expect(screen.getByText(/restricted 00:40 · eligibility unknown 01:00/)).toBeInTheDocument()
  })

  it('does not qualify the target when backend measurement is not ready', () => {
    render(<LiquidityVerdictCard markets={[market({ measurementReady: false, measurementReason: 'eligibility_history_unavailable' })]} mode="operator" />)
    expect(screen.getByText('INSUFFICIENT DATA')).toBeInTheDocument()
    expect(screen.getByText(/Target unqualified: eligibility_history_unavailable/)).toBeInTheDocument()
  })
})
