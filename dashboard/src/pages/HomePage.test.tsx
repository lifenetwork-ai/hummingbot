import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { components } from '../core/api/schema'
import * as hooks from '../core/api/hooks'
import * as opContext from '../features/operational-context/useOperationalContext'
import { HomePage } from './HomePage'

vi.mock('@tanstack/react-router', () => ({
  Link: ({ children, to, className }: { children: React.ReactNode; to?: string; className?: string }) => (
    <a href={to ?? '#'} className={className}>
      {children}
    </a>
  ),
  useNavigate: () => vi.fn(),
}))

vi.mock('../features/session-control/useSessionControl', () => ({
  useSessionControl: () => ({
    stop: vi.fn(),
    stopping: false,
    stopBlocksNew: false,
  }),
}))

describe('HomePage (Overview)', () => {
  it('renders a single Risk Guards entry and opens its detail panel from the KPI', () => {
    vi.spyOn(opContext, 'useOperationalContext').mockReturnValue({
      identity: null,
      botId: 'test-bot',
      controlVersion: 1,
      processEnvironment: 'demo',
      ownerEnvironment: 'demo',
      environmentMismatch: false,
      markets: [],
      observation: null,
      observationState: 'fresh',
      accounts: [],
      statusUpdatedAt: Date.now(),
      actions: {
        start: { allowed: false, reasonCode: 'running', detail: 'Running' },
        switch: { allowed: true, reasonCode: null, detail: 'Switch' },
        stop: { allowed: true, reasonCode: null, detail: 'Stop' },
      },
      status: {
        phase: 'ready',
        error: null,
        refreshing: false,
        data: {
          acknowledged_version: 1,
          bot_id: 'test-bot',
          control_version: 1,
          desired_state: 'running',
          drawdown: null,
          error: null,
          funding_sync: null,
          heartbeat_age_seconds: 1,
          observation: { observed_at: '2026-11-10T00:00:00Z', stale: false, session_matches: true, reason_code: null },
          observed_session: { id: 'session-123', generation: 1, profile_id: 'fixed_tight', status: 'running' } as unknown as components['schemas']['SessionStateOutput'],
          operator: null,
          owner_available: true,
          automatic_recovery: null,
          owner_identity: null,
          phase: 'running',
          process_identity: null,
          recovery: null,
          running: true,
          server_time: '2026-11-10T00:00:00Z',
          service: null,
          session: null,
          session_configuration_url: null,
          spot_outflow: null,
          time_remaining_seconds: 3600,
        },
      },
      balances: {
        phase: 'ready',
        error: null,
        refreshing: false,
        data: {
          accounts: [],
          owner_available: true,
          scope: 'account',
          server_time: '2026-11-10T00:00:00Z',
        },
      },
      refreshStatus: vi.fn(),
      refreshBalances: vi.fn(),
    })

    vi.spyOn(hooks, 'useAdminQuery').mockImplementation((operation: string) => {
      if (operation === 'getLiquidity') {
        return {
          data: {
            coverage_started_at: null,
            ended_at: null,
            generation: 1,
            markets: {
              spot: {
                ask_notional: '1500.00',
                best_ask: '0.045230',
                best_bid: '0.045210',
                bid_notional: '1500.00',
                buy_order_count: 3,
                connector: 'okx',
                elapsed_seconds: 600,
                eligible: true,
                eligible_seconds: 600,
                observed_seconds: 600,
                pair: 'LIFE-USDT',
                quote_activity: null,
                reason_code: null,
                sell_order_count: 3,
                spread_bps: '4.4',
                two_sided: true,
                two_sided_fraction_of_elapsed: 1.0,
                valid: true,
              },
            },
            state: 'observed',
            measurement: 'sampled_owner_observations' as const,
            observed_at: '2026-11-10T00:00:00Z',
            profile_id: 'fixed_tight',
            session_id: 'session-123',
          },
          isLoading: false,
        } as unknown as ReturnType<typeof hooks.useAdminQuery>
      }
      return { data: undefined, isLoading: false } as unknown as ReturnType<typeof hooks.useAdminQuery>
    })

    render(<HomePage />)

    // Layer 1: control bar + 4 KPIs
    expect(screen.getByText('Emergency stop')).toBeInTheDocument()
    expect(screen.getByText('Account USDT equity')).toBeInTheDocument()
    expect(screen.getByText(/Net Position/)).toBeInTheDocument()
    expect(screen.getByText('Two-Sided Coverage (session)')).toBeInTheDocument()
    expect(screen.getByText('Capital Protection')).toBeInTheDocument()

    // Layer 2: operational focus
    expect(screen.getByText('Derivative Positions & Skew')).toBeInTheDocument()

    // Layer 3: diagnostics — composition card complements the KPI, no duplicate headline
    expect(screen.getByText('Coverage Time Composition')).toBeInTheDocument()
    expect(screen.getByText(/Balances/)).toBeInTheDocument()
    expect(screen.queryByText('Rule 1 · Drawdown')).not.toBeInTheDocument()

    const riskGuardsButton = screen.getByRole('button', { name: /view rule details/i })
    riskGuardsButton.focus()
    fireEvent.click(riskGuardsButton)
    expect(screen.getByRole('dialog', { name: 'Capital Protection' })).toBeInTheDocument()
    expect(screen.getByText('Rule 1 · Drawdown')).toBeInTheDocument()
    expect(screen.getByText('Rule 2 · Spot Outflow')).toBeInTheDocument()

    fireEvent.keyDown(window, { key: 'Escape' })
    expect(screen.queryByRole('dialog', { name: 'Capital Protection' })).not.toBeInTheDocument()
    expect(riskGuardsButton).toHaveFocus()

    // Session coverage remains available before 30 minutes; no rolling SLA is inferred.
    expect(screen.getAllByText('SESSION TOTAL').length).toBeGreaterThanOrEqual(1)
  })
})
