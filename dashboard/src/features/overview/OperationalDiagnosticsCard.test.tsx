import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

vi.mock('@tanstack/react-router', () => ({
  Link: ({ children, to, className, 'aria-label': ariaLabel }: { children?: React.ReactNode; to?: string; className?: string; 'aria-label'?: string }) => (
    <a href={to ?? '#'} className={className} aria-label={ariaLabel}>
      {children}
    </a>
  ),
}))
import type { ReadResponse } from '../../core/api/admin'
import { ApiError } from '../../core/api/client'
import { contextStatus } from '../../test/mocks/contextFixtures'
import { deriveOperationalDiagnostics, deriveProcessDiagnostics } from './diagnostics'
import { OperationalDiagnosticsCard } from './OperationalDiagnosticsCard'

const NOW = Date.parse('2026-11-16T00:00:05Z')

function withStatus(patch: Partial<ReadResponse<'getRuntimeStatus'>>): ReadResponse<'getRuntimeStatus'> {
  return { ...contextStatus, ...patch }
}

describe('OperationalDiagnosticsCard', () => {
  it('shows the service phase with friendly update-waiting labels and incidents', () => {
    const status = withStatus({
      service: {
        state_source: 'postgres', persisted_state_available: true, phase: 'standby', reason_code: 'migration_pending',
        stale: false, observed_at: '2026-11-16T00:00:03Z', observer_age_seconds: 2, incidents: [
          { reason_code: 'controller_read_retry', scope: 'controller', severity: 'warning', detail: 'Retrying controller read.', first_seen_at: '2026-11-16T00:00:00Z', last_seen_at: '2026-11-16T00:00:02Z', last_healthy_at: null, next_retry_at: null, operator_action_required: false, retry_count: 1, active_fallback: null },
        ],
      },
    })
    render(<OperationalDiagnosticsCard diagnostics={deriveOperationalDiagnostics(status, true, NOW)} />)
    expect(screen.getByText('Update waiting')).toBeInTheDocument()
    expect(screen.getByText(/the previous process must finish before the update continues/)).toBeInTheDocument()
    expect(screen.getByText('controller_read_retry')).toBeInTheDocument()
  })

  it('labels process-reported diagnostics when persisted state is unavailable', () => {
    const status = withStatus({
      service: { state_source: 'process', persisted_state_available: false, phase: 'degraded', reason_code: 'control_db_unavailable', stale: false, observed_at: '2026-11-16T00:00:03Z', observer_age_seconds: 1, incidents: [] },
    })
    render(<OperationalDiagnosticsCard diagnostics={deriveOperationalDiagnostics(status, true, NOW)} />)
    expect(screen.getByText('Process-reported')).toBeInTheDocument()
    expect(screen.getByText(/process-reported only; commands are not accepted without durable storage/)).toBeInTheDocument()
  })

  it('shows a current process-only 503 separately from the retained stale service snapshot', () => {
    const retained = withStatus({ service: {
      state_source: 'postgres', persisted_state_available: true, phase: 'running', reason_code: null,
      stale: false, observed_at: '2026-11-16T00:00:03Z', observer_age_seconds: 2, incidents: [],
    } })
    const processDiagnostics = deriveProcessDiagnostics(new ApiError(503, { request_id: 'req-503', service: {
      state_source: 'process', persisted_state_available: false, phase: 'unavailable',
      reason_code: 'control_db_unavailable', observed_at: '2026-11-16T00:00:04Z',
    } }))
    render(<OperationalDiagnosticsCard
      diagnostics={deriveOperationalDiagnostics(retained, false, NOW)}
      processDiagnostics={processDiagnostics}
    />)
    expect(screen.getByText('Process-reported')).toBeInTheDocument()
    expect(screen.getByText(/The service process is responding/)).toBeInTheDocument()
    expect(screen.getByText(/Persisted control state is unavailable/)).toBeInTheDocument()
    expect(screen.getByText(/request req-503/)).toBeInTheDocument()
    expect(screen.getByText('Stale')).toBeInTheDocument()
    expect(screen.queryByText(/owner (available|unavailable)/i)).not.toBeInTheDocument()
  })

  it('renders perpetual reduction per market and side with friendly reasons and stale marking', () => {
    const status = withStatus({
      markets: [
        { market: 'perpetual', pair: 'BTC-USDT-SWAP', base_asset: null, quote_asset: null, connector: 'okx_perpetual', admitted: true, ready: true, reason: null, reference_price: '42000', inventory_skew: null, quote_guard: null, spread_policy: null,
          inventory_reduction: {
            mode: 'close_first', price_mode: 'breakeven', stale: false, fee_ready: false, observed_at: '2026-11-15T23:59:58Z',
            sides: {
              BUY: { effect: 'CLOSE', target_leg: 'SHORT', owned_base: '0.5', venue_base: '0.5', reserved_base: '0.1', available_base: '0.4', reason_code: 'reduction_price_not_profitable' },
              SELL: { effect: null, target_leg: 'LONG', owned_base: null, venue_base: '0', reserved_base: '0', available_base: '0', reason_code: 'reduction_dust' },
            },
          } },
      ],
    })
    render(<OperationalDiagnosticsCard diagnostics={deriveOperationalDiagnostics(status, true, NOW)} />)
    expect(screen.getByText(/Perpetual inventory reduction/)).toBeInTheDocument()
    expect(screen.getByText(/Waiting for a maker price that covers the allocated opening and closing fees/)).toBeInTheDocument()
    expect(screen.getByText(/Below the venue minimum order size/)).toBeInTheDocument()
    // observed 7s before NOW → evaluation expired per the 5s window
    expect(screen.getAllByText('Stale').length).toBeGreaterThan(0)
    expect(screen.getByText(/evaluation expired or not current/)).toBeInTheDocument()
    expect(screen.getByText('Fees pending')).toBeInTheDocument()
  })

  it('reports the Spot BUY terminal verification wait without claiming the cap is exhausted', () => {
    const status = withStatus({
      spot_outflow: { ready: true, stale: false, observed_at: '2026-11-16T00:00:03Z', pending_terminal_orders: 3, buy_allowed: false, reason_code: 'spot_outflow_blocked', fill_count: 0, coverage_start_ms: null, window_start_ms: null, window_end_ms: null, last_buy_denial: null, non_usdt_fees: {}, recorded_net_outflow_quote: null, remaining_quote: null, reserved_quote: null, binding: null, policy: { window_seconds: 3600, max_net_outflow_quote: '1000', fee_allowance_rate: '0.001', trading_pair: 'BTC-USDT' } },
      service: null,
    })
    render(<OperationalDiagnosticsCard diagnostics={deriveOperationalDiagnostics(status, true, NOW)} />)
    expect(screen.getByText(/3 Spot BUY orders are waiting for completion verification/)).toBeInTheDocument()
    expect(screen.getByText(/not proof that the spending cap is exhausted/)).toBeInTheDocument()
  })

  it('shows the terminal verification stage from a scoped service incident', () => {
    const status = withStatus({
      service: {
        state_source: 'postgres', persisted_state_available: true, phase: 'running', reason_code: null, stale: false, observed_at: '2026-11-16T00:00:03Z', observer_age_seconds: 1,
        incidents: [{ reason_code: 'spot_outflow_terminal_fill_coverage', scope: 'spot_outflow', severity: 'warning', detail: 'Verifying fill coverage.', first_seen_at: '2026-11-16T00:00:00Z', last_seen_at: '2026-11-16T00:00:02Z', last_healthy_at: null, next_retry_at: '2026-11-16T00:00:10Z', operator_action_required: true, retry_count: 2, active_fallback: null }],
      },
    })
    render(<OperationalDiagnosticsCard diagnostics={deriveOperationalDiagnostics(status, true, NOW)} />)
    expect(screen.getByText('spot_outflow_terminal_fill_coverage')).toBeInTheDocument()
    expect(screen.getByText(/checking fill coverage/)).toBeInTheDocument()
    expect(screen.getByText(/operator action required/)).toBeInTheDocument()
  })

  it('distinguishes zero from unknown pending terminal orders', () => {
    const zero = withStatus({ spot_outflow: { ready: true, stale: false, observed_at: '2026-11-16T00:00:03Z', pending_terminal_orders: 0, buy_allowed: true, reason_code: null, fill_count: 0, coverage_start_ms: null, window_start_ms: null, window_end_ms: null, last_buy_denial: null, non_usdt_fees: {}, recorded_net_outflow_quote: null, remaining_quote: null, reserved_quote: null, binding: null, policy: { window_seconds: 3600, max_net_outflow_quote: '1000', fee_allowance_rate: '0.001', trading_pair: 'BTC-USDT' } } })
    const { rerender } = render(<OperationalDiagnosticsCard diagnostics={deriveOperationalDiagnostics(zero, true, NOW)} />)
    expect(screen.getByText(/No Spot BUY orders are waiting for completion verification/)).toBeInTheDocument()

    const absent = withStatus({})
    rerender(<OperationalDiagnosticsCard diagnostics={deriveOperationalDiagnostics(absent, true, NOW)} />)
    expect(screen.getByText(/Spot outflow policy is not reported/)).toBeInTheDocument()
  })

  it('does not call absent reduction data disabled', () => {
    render(<OperationalDiagnosticsCard diagnostics={deriveOperationalDiagnostics(withStatus({ markets: [] }), true, NOW)} />)
    expect(screen.getByText('Not reported')).toBeInTheDocument()
    expect(screen.getByText(/policy state cannot be determined from this observation/)).toBeInTheDocument()
    expect(screen.queryByText('Not enabled')).not.toBeInTheDocument()
  })

  it.each([
    ['stale', { ready: true, stale: true, observed_at: '2026-11-16T00:00:03Z', pending_terminal_orders: 0 }],
    ['unknown', { ready: true, stale: false, observed_at: '2026-11-16T00:00:03Z', pending_terminal_orders: 0 }],
  ] as const)('does not claim zero means no orders when outflow evidence is %s', (label, outflow) => {
    const status = withStatus({
      observation: label === 'unknown'
        ? null
        : { ...contextStatus.observation!, stale: false, session_matches: true },
      spot_outflow: { ...contextStatus.spot_outflow!, ...outflow },
    })
    render(<OperationalDiagnosticsCard diagnostics={deriveOperationalDiagnostics(status, label === 'stale', NOW)} />)
    expect(screen.getByText(/last recorded pending terminal orders: 0.*current count is unverified/i)).toBeInTheDocument()
    expect(screen.queryByText(/No Spot BUY orders are waiting/)).not.toBeInTheDocument()
  })

  it('does not duplicate known terminal incidents in the other-incidents details', () => {
    const first = {
      reason_code: 'spot_outflow_terminal_fill_coverage', scope: 'spot_outflow', severity: 'warning' as const,
      detail: 'Terminal verification detail.', first_seen_at: '2026-11-16T00:00:00Z', last_seen_at: '2026-11-16T00:00:02Z',
      last_healthy_at: null, next_retry_at: null, operator_action_required: false, retry_count: 1, active_fallback: null,
    }
    const differentScope = { ...first, scope: 'spot' }
    const ordinary = { ...first, reason_code: 'controller_read_retry', scope: 'controller', detail: 'Ordinary service detail.' }
    const status = withStatus({ service: {
      state_source: 'postgres', persisted_state_available: true, phase: 'running', reason_code: null, stale: false,
      observed_at: '2026-11-16T00:00:03Z', observer_age_seconds: 1, incidents: [first, differentScope, ordinary],
    } })
    render(<OperationalDiagnosticsCard diagnostics={deriveOperationalDiagnostics(status, true, NOW)} />)
    expect(screen.getAllByText('spot_outflow_terminal_fill_coverage')).toHaveLength(2)
    expect(screen.getByText('Other service incidents (1)')).toBeInTheDocument()
    expect(screen.getAllByText('Terminal verification detail.')).toHaveLength(2)
    expect(screen.getByText(/controller_read_retry/)).toBeInTheDocument()
  })
})
