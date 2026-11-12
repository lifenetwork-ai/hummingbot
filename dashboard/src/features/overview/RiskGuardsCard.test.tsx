import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { contextStatus } from '../../test/mocks/contextFixtures'
import { completionOverview } from '../../test/mocks/completionFixtures'
import { RiskGuardsCard } from './RiskGuardsCard'
import type { components } from '../../core/api/schema'

describe('RiskGuardsCard evidence boundaries', () => {
  it('does not infer safety or fabricate utilization/thresholds when policy observations are absent', () => {
    render(<RiskGuardsCard status={contextStatus} observation="fresh" />)

    // Missing market and policy observations remain unverified.
    expect(screen.getByText('— violations · 0 of 2 rules checked · 2 unverified')).toBeInTheDocument()
    expect(screen.getByText('Rule 1 · Drawdown')).toBeInTheDocument()
    expect(screen.getByText('Rule 2 · Spot Outflow')).toBeInTheDocument()
    expect(screen.queryByText('Safe')).not.toBeInTheDocument()
    expect(screen.queryByText('Allowed')).not.toBeInTheDocument()

    // Do not fabricate threshold / utilization percentage when data is missing
    expect(screen.queryByText('80%')).not.toBeInTheDocument()
    expect(screen.queryByText('8%')).not.toBeInTheDocument()
    expect(screen.queryByText(/Threshold: /)).not.toBeInTheDocument()
  })

  it('shows quote deviation, mode, blocker and missing backend threshold without relying on a tooltip', () => {
    const statusWithQuoteGuard = {
      ...contextStatus,
      markets: [{
        market: 'spot',
        pair: 'LIFE-USDT',
        quote_guard: {
          book_observed_at: null,
          bootstrap: null,
          can_admit: true,
          data_quality: 'known_order_filtered',
          detail: null,
          deviation_bps: '12.5',
          enabled: true,
          evaluated_at: '2026-11-11T00:00:00Z',
          market_mid: null,
          mode: 'strict',
          qualification_pending: false,
          ready: true,
          reason_code: 'deviation_too_high',
          recovery_started_at: null,
          recovery_valid_samples: 0,
        },
      }],
    } as unknown as components['schemas']['StatusResponseOutput']

    render(<RiskGuardsCard status={statusWithQuoteGuard} observation="fresh" />)

    expect(screen.getAllByText(/12\.5000 bps/).length).toBe(2)
    expect(screen.getByText('strict')).toBeInTheDocument()
    expect(screen.getByText('Reported reason: deviation_too_high')).toBeInTheDocument()
    expect(screen.getByText(/Maximum deviation threshold is not provided by the backend/)).toBeInTheDocument()
  })

  it('keeps stale policy snapshots distinguishable from current permission to buy', () => {
    render(<RiskGuardsCard status={contextStatus} observation="stale" />)
    expect(screen.getByText(/0 of 2 rules checked · 2 unverified/)).toBeInTheDocument()
    expect(screen.queryByText('Backend allows buying per policy')).not.toBeInTheDocument()
  })

  it('renders market policies with inventory skew account_cash scope and blocker from completionOverview', () => {
    const policyWithDiagnosis = [
      {
        ...completionOverview.risk.marketPolicies[0]!,
        sideEvidence: {
          BUY: {
            state: 'blocked' as const,
            detail: 'BUY restricted',
            order_count: 0,
            proposed_count: 0,
            pending_create_count: 0,
            pending_cancel_count: 0,
            reason_codes: ['inventory_restricted'],
            diagnosis: {
              stage: 'trading_conditions' as const,
              source: 'controller_observation' as const,
              error_type: null,
              observed_seconds: 25,
              next_action: 'Review inventory; do not relax guards.',
            },
          },
          SELL: {
            state: 'working' as const,
            detail: 'SELL observed',
            order_count: 1,
            proposed_count: null,
            pending_create_count: 0,
            pending_cancel_count: 0,
            reason_codes: [],
            diagnosis: null,
          },
        },
      },
    ]

    render(<RiskGuardsCard marketPolicies={policyWithDiagnosis} observation="fresh" />)

    // Market header (in details accordion)
    expect(screen.getByText(/LIFE-USDT/)).toBeInTheDocument()
    expect(screen.getByText(/\(spot · okx\)/)).toBeInTheDocument()

    // Blocker reason
    expect(screen.getAllByText(/inventory_restricted/)[0]).toBeInTheDocument()

    // Inventory skew details & scope
    expect(screen.getByText(/scope: account_cash/)).toBeInTheDocument()
    expect(screen.getAllByText(/0\.000000001/).length).toBeGreaterThanOrEqual(1)

    // Guidance from backend
    expect(screen.getByText(/Review inventory; do not relax guards/)).toBeInTheDocument()

    // Ensure no fake utilization threshold is present
    expect(screen.queryByText('8%')).not.toBeInTheDocument()
    expect(screen.queryByText('80%')).not.toBeInTheDocument()
  })
})
