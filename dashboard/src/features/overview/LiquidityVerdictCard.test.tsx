import { fireEvent, render, screen } from '@testing-library/react'
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

  it('handles missing bestBid and null actual spread cleanly', () => {
    render(
      <LiquidityVerdictCard
        markets={[
          market({
            bestBid: null,
            spreadActualBps: null,
            spreadTargetBps: 8,
            twoSided: false,
          }),
        ]}
        mode="operator"
      />,
    )
    expect(screen.getByText('ONE-SIDED')).toBeInTheDocument()
    expect(screen.getByText('/ 8.00 bps')).toBeInTheDocument()
  })

  it('auto-expands why panel when diagnosis is present', () => {
    render(
      <LiquidityVerdictCard
        markets={[
          market({
            twoSided: false,
            why: {
              elapsedSeconds: 1800,
              eligibleSeconds: 0,
              notEligibleSeconds: 1800,
              unknownSeconds: 0,
              quotingAbsentSeconds: 45,
              diagnosis: 'Check buy order balance',
            },
          }),
        ]}
        mode="operator"
      />,
    )
    expect(screen.getByText('Check buy order balance')).toBeInTheDocument()
    expect(screen.getByText('45s')).toBeInTheDocument()
  })

  it('renders depth ladder in incremental bands when depth data is available', () => {
    render(
      <LiquidityVerdictCard
        markets={[
          market({
            depth: {
              available: true,
              stale: false,
              observedAt: '2026-11-18T00:00:00Z',
              value: {
                basis: 'own_working_orders',
                reference_mid: '64960',
                levels: [
                  { offset_bps: 25, bid_usdt: '0.00', ask_usdt: '13.41' },
                  { offset_bps: 50, bid_usdt: '0.00', ask_usdt: '13.41' },
                  { offset_bps: 100, bid_usdt: '0.00', ask_usdt: '13.41' },
                ],
              },
            },
          }),
        ]}
        mode="operator"
      />,
    )
    expect(screen.getByText('Depth Ladder')).toBeInTheDocument()
    expect(screen.getByText('0–25 bps')).toBeInTheDocument()
    expect(screen.getByText('25–50 bps')).toBeInTheDocument()
    expect(screen.getByText('50–100 bps')).toBeInTheDocument()
    // In incremental mode, 13.41 is only in the first band; outer bands show 0.00
    expect(screen.getByText('13.41')).toBeInTheDocument()
    expect(screen.getAllByText('0.00').length).toBeGreaterThanOrEqual(2)
  })

  it('renders incremental delta values when multi-tier liquidity is present', () => {
    render(
      <LiquidityVerdictCard
        markets={[
          market({
            depth: {
              available: true,
              stale: false,
              observedAt: '2026-11-18T00:00:00Z',
              value: {
                basis: 'own_working_orders',
                reference_mid: '64960',
                levels: [
                  { offset_bps: 25, bid_usdt: '10.00', ask_usdt: '20.00' },
                  { offset_bps: 50, bid_usdt: '30.00', ask_usdt: '50.00' },
                  { offset_bps: 100, bid_usdt: '60.00', ask_usdt: '90.00' },
                ],
              },
            },
          }),
        ]}
        mode="operator"
      />,
    )
    // Band 0-25: 10.00 / 20.00
    expect(screen.getByText('10.00')).toBeInTheDocument()
    expect(screen.getAllByText('20.00')).toHaveLength(2)
    // Band 25-50 delta: (30-10)=20.00 / (50-20)=30.00
    // Band 50-100 delta: (60-30)=30.00 / (90-50)=40.00
    expect(screen.getAllByText('30.00')).toHaveLength(2)
    expect(screen.getByText('40.00')).toBeInTheDocument()
  })

  it('renders SaaS status timeline with 30m ruler and percentages', () => {
    render(
      <LiquidityVerdictCard
        markets={[
          market({
            twoSided: false,
            why: {
              elapsedSeconds: 1800,
              eligibleSeconds: 158,
              notEligibleSeconds: 509,
              unknownSeconds: 1133,
              quotingAbsentSeconds: 0,
              diagnosis: null,
            },
          }),
        ]}
        mode="operator"
      />,
    )
    expect(screen.getByRole('img', { name: '30-minute status timeline' })).toBeInTheDocument()
    expect(screen.getByText('30m ago')).toBeInTheDocument()
    expect(screen.getByText('15m ago')).toBeInTheDocument()
    expect(screen.getByText('Now')).toBeInTheDocument()
    expect(screen.getByText('(8.8%)')).toBeInTheDocument()
    expect(screen.getByText('(28.3%)')).toBeInTheDocument()
    expect(screen.getByText('(62.9%)')).toBeInTheDocument()
  })

  it('toggles the inline SLA explanation guide when clicking the Info button', () => {
    render(
      <LiquidityVerdictCard
        markets={[
          market({
            twoSided: false,
          }),
        ]}
        mode="operator"
      />,
    )
    expect(screen.queryByText('30-Minute Rolling SLA States')).not.toBeInTheDocument()
    const infoBtn = screen.getByLabelText('Toggle SLA state definitions')
    fireEvent.click(infoBtn)
    expect(screen.getByText('30-Minute Rolling SLA States')).toBeInTheDocument()
    expect(screen.getByText(/Two-sided quoting active within spread and depth targets/)).toBeInTheDocument()
    fireEvent.click(screen.getByText('Close'))
    expect(screen.queryByText('30-Minute Rolling SLA States')).not.toBeInTheDocument()
  })

  it('renders 30m status timeline and breakdown legend in foundation mode as well', () => {
    render(
      <LiquidityVerdictCard
        markets={[
          market({
            twoSided: false,
            why: {
              elapsedSeconds: 1800,
              eligibleSeconds: 900,
              notEligibleSeconds: 600,
              unknownSeconds: 300,
              quotingAbsentSeconds: 0,
              diagnosis: null,
            },
          }),
        ]}
        mode="foundation"
      />,
    )
    expect(screen.getByRole('img', { name: '30-minute status timeline' })).toBeInTheDocument()
    expect(screen.getByText('30m ago')).toBeInTheDocument()
    expect(screen.getByText('Now')).toBeInTheDocument()
    expect(screen.getByText('(50.0%)')).toBeInTheDocument()
    expect(screen.getByText(/proportional estimate · per-minute history unavailable/)).toBeInTheDocument()
  })
})
