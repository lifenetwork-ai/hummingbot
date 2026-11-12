import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { ControllerPerformanceView } from './ControllerPerformanceView'
import { completionUnavailablePerformance } from '../../test/mocks/completionFixtures'
import type { PerformancePanelModel } from '../reporting/performance'

describe('ControllerPerformanceView', () => {
  const readyModel: PerformancePanelModel = {
    source: {
      phase: 'ready',
      data: {
        server_time: '2026-11-11T12:00:00Z',
        scope: 'controller_lifetime_and_active_exposure',
        owner_available: true,
        markets: [],
      },
      error: null,
      refreshing: false,
    },
    percentageUnit: 'percentage_points',
    percentageDenominator: 'volume_traded',
    scope: 'controller_lifetime_and_active_exposure',
    observedAt: '2026-11-11T12:00:00Z',
    ownerAvailable: true,
    rows: [
      {
        controllerId: 'ctrl-market-making-1',
        market: 'spot',
        pair: 'LIFE-USDT',
        connector: 'binance',
        observedAt: '2026-11-11T12:00:00Z',
        source: 'v2_controller_report_cache',
        valuationBasis: 'executor_and_position_hold_reports',
        observation: 'fresh',
        fundingStatus: 'not_applicable',
        report: {
          close_type_counts: { TAKE_PROFIT: 12, STOP_LOSS: 2 },
          global_pnl_pct: '2.5',
          global_pnl_quote: '125.50',
          realized_pnl_pct: '2.1',
          realized_pnl_quote: '105.00',
          unrealized_pnl_pct: '0.4',
          unrealized_pnl_quote: '20.50',
          volume_traded: '5000.00',
          positions_summary: [],
        },
      },
      {
        controllerId: 'ctrl-perp-hedge-2',
        market: 'perpetual',
        pair: 'BTC-USDT',
        connector: 'okx_perpetual',
        observedAt: '2026-11-11T11:58:00Z',
        source: 'v2_controller_report_cache',
        valuationBasis: 'executor_and_position_hold_reports',
        observation: 'stale',
        fundingStatus: 'observed_only',
        report: {
          close_type_counts: { EXPIRED: 1 },
          global_pnl_pct: '-1.2',
          global_pnl_quote: '-60.00',
          realized_pnl_pct: '-1.0',
          realized_pnl_quote: '-50.00',
          unrealized_pnl_pct: '-0.2',
          unrealized_pnl_quote: '-10.00',
          volume_traded: '5000.00',
          positions_summary: [],
        },
      },
    ],
    explanation:
      'Controller lifetime and active exposure, not whole-account profit or an investment benchmark. Controller/position reports are not double-counted.',
  }

  it('renders ready rows with controller details, metrics, and disclaimers', () => {
    render(<ControllerPerformanceView model={readyModel} />)

    expect(screen.getByText('Standalone Controller Performance')).toBeInTheDocument()
    expect(screen.getByText('Scope: controller_lifetime_and_active_exposure')).toBeInTheDocument()
    expect(screen.getByText('Runtime Owner: Available')).toBeInTheDocument()

    // Controller 1
    expect(screen.getByText('ctrl-market-making-1')).toBeInTheDocument()
    expect(screen.getByText('LIFE-USDT')).toBeInTheDocument()
    expect(screen.getByText('Connector: binance')).toBeInTheDocument()
    expect(screen.getByText('Fresh')).toBeInTheDocument()
    expect(screen.getByText('TAKE_PROFIT: 12')).toBeInTheDocument()
    expect(screen.getByText('STOP_LOSS: 2')).toBeInTheDocument()

    // Controller 2
    expect(screen.getByText('ctrl-perp-hedge-2')).toBeInTheDocument()
    expect(screen.getByText('BTC-USDT')).toBeInTheDocument()
    expect(screen.getByText('Connector: okx_perpetual')).toBeInTheDocument()
    expect(screen.getByText('Stale')).toBeInTheDocument()
    expect(screen.getByText('EXPIRED: 1')).toBeInTheDocument()

    // Disclaimers
    expect(
      screen.getByText(/Performance metrics are not aggregated across controllers/i),
    ).toBeInTheDocument()
    expect(screen.getByText(/percentage_points/i)).toBeInTheDocument()
    expect(screen.getByText(/volume_traded/i)).toBeInTheDocument()
  })

  it('renders error state independently using completionUnavailablePerformance', () => {
    render(<ControllerPerformanceView model={completionUnavailablePerformance} />)

    expect(screen.getByRole('alert')).toBeInTheDocument()
    expect(screen.getByText(/report unavailable/i)).toBeInTheDocument()
    expect(
      screen.getByText(/This report is a standalone secondary panel; errors here do not interrupt/i),
    ).toBeInTheDocument()
  })

  it('renders loading state when phase is loading and rows are empty', () => {
    const loadingModel: PerformancePanelModel = {
      ...readyModel,
      source: { phase: 'loading', error: null, refreshing: false },
      rows: [],
    }

    render(<ControllerPerformanceView model={loadingModel} />)
    expect(screen.getByText(/Loading controller performance/i)).toBeInTheDocument()
  })

  it('renders empty message when phase is ready but no rows exist', () => {
    const emptyModel: PerformancePanelModel = {
      ...readyModel,
      rows: [],
    }

    render(<ControllerPerformanceView model={emptyModel} />)
    expect(screen.getByText(/No observations from the controller report cache yet/i)).toBeInTheDocument()
  })

  it('triggers onRefresh when refresh button is clicked', () => {
    const onRefresh = vi.fn()
    render(<ControllerPerformanceView model={readyModel} onRefresh={onRefresh} />)

    const refreshBtn = screen.getByRole('button', { name: /Refresh/i })
    fireEvent.click(refreshBtn)
    expect(onRefresh).toHaveBeenCalledTimes(1)
  })
})
