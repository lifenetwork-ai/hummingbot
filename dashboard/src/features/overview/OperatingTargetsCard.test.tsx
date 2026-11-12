import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { completionOverview } from '../../test/mocks/completionFixtures'
import { OperatingTargetsCard } from './OperatingTargetsCard'

describe('OperatingTargetsCard coverage and thresholds', () => {
  it('labels session coverage as cumulative and shows NO DATA placeholder for rolling per-minute history', () => {
    render(<OperatingTargetsCard markets={completionOverview.markets} />)

    expect(screen.getByText(/Cumulative session/i)).toBeInTheDocument()
    // Barcode 30 minutes: placeholder NO DATA — does not simulate per-minute data
    expect(screen.getByText('Rolling 30-minute SLA and per-minute history are not provided by this API.')).toBeInTheDocument()
    expect(screen.queryByText('CALCULATING')).not.toBeInTheDocument()
    expect(screen.queryByText('SYNCING')).not.toBeInTheDocument()
    expect(screen.queryByText('Target ≥ 99%')).not.toBeInTheDocument()
  })

  it('shows disabled deployment warning separately from the 20s and 60s operating targets', () => {
    const markets = completionOverview.markets.map((market) => ({
      ...market,
      warningAfterSeconds: 0,
      warningThresholdStatus: 'deployment_threshold_differs' as const,
      noQuoteSeconds: 61,
      activity: market.activity ? { ...market.activity, warning_active: false } : null,
    }))
    render(<OperatingTargetsCard markets={markets} />)

    // Deployment thresholds & warnings in technical accordion
    expect(screen.getByText(/Deployment warning threshold: disabled/)).toBeInTheDocument()
    expect(screen.getByText(/Backend warning is currently disabled/)).toBeInTheDocument()
  })

  it('reports a one-sided absence duration even while the opposite side has orders', () => {
    const markets = completionOverview.markets.map((market) => ({
      ...market,
      noQuoteSeconds: 0,
      sides: market.sides.map((side) => side.side === 'buy'
        ? { ...side, orderCount: 0, diagnosis: { stage: 'trading_conditions' as const, source: 'controller_observation' as const, error_type: null, observed_seconds: 25, next_action: 'Review inventory.' } }
        : side),
    }))
    render(<OperatingTargetsCard markets={markets} />)

    expect(screen.getByText(/Side BUY quoting absent for 00:25: exceeds target threshold \(> 20s\)\. Review inventory\./)).toBeInTheDocument()
  })
})
