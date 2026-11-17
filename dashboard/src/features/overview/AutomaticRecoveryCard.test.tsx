import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { ReadResponse } from '../../core/api/admin'
import { contextStatus } from '../../test/mocks/contextFixtures'
import { deriveRecoveryPresentation } from '../recovery/presentation'
import { AutomaticRecoveryCard } from './AutomaticRecoveryCard'

type Automatic = NonNullable<ReadResponse<'getRuntimeStatus'>['automatic_recovery']>

function automaticStatus(automatic: Automatic | null, overrides: Partial<ReadResponse<'getRuntimeStatus'>> = {}): ReadResponse<'getRuntimeStatus'> {
  return { ...contextStatus, automatic_recovery: automatic, ...overrides }
}

const baseAutomatic: Automatic = {
  admission_mode: 'paused',
  cold_recovery_supported: true,
  isolated: false,
  last_progress_at: null,
  next_retry_at: null,
  operator_required: false,
  order_id: null,
  phase: 'idle',
  quarantined_orders: 0,
  reason_code: null,
  reserved_base: undefined,
  reserved_quote_worst_case: '0',
  stale: false,
  started_at: null,
}

describe('AutomaticRecoveryCard', () => {
  it('shows an explicitly zero quote reservation separately from an unreported amount', () => {
    const status = automaticStatus({ ...baseAutomatic, phase: 'reconciling', quarantined_orders: 1, reserved_quote_worst_case: '0' })
    render(<AutomaticRecoveryCard presentation={deriveRecoveryPresentation(status, true)} />)
    expect(screen.getByText(/0\.00 USDT/)).toBeInTheDocument()
  })
  it('does not describe retained scoped uncertainty after Stop as active funded SELL admission', () => {
    const status = automaticStatus({ ...baseAutomatic, phase: 'settled_stopped', isolated: true, admission_mode: 'funded_spot_sell', quarantined_orders: 1 }, { desired_state: 'stopped' })
    render(<AutomaticRecoveryCard presentation={deriveRecoveryPresentation(status, true)} />)
    expect(screen.queryByText(/Only funded Spot cash SELLs may continue/)).not.toBeInTheDocument()
    expect(screen.getByText(/Trading remains stopped/)).toBeInTheDocument()
  })
  it.each(['stale', 'unknown'] as const)('does not grant funded SELL admission from %s scoped evidence', (observation) => {
    const status = automaticStatus({ ...baseAutomatic, phase: 'reconciling', isolated: true, admission_mode: 'funded_spot_sell' },
      observation === 'unknown' ? { observation: null } : {})
    render(<AutomaticRecoveryCard presentation={deriveRecoveryPresentation(status, observation !== 'stale')} />)
    expect(screen.queryByText(/Only funded Spot cash SELLs may continue/)).not.toBeInTheDocument()
    expect(screen.getByText(/Current trading admission is unverified/)).toBeInTheDocument()
  })
  it('renders an active reconciliation with operator-facing phase copy', () => {
    const status = automaticStatus({ ...baseAutomatic, phase: 'reconciling', reason_code: 'order_outcome_unknown', order_id: '123-abc', started_at: 1762300000, last_progress_at: 1762300005 })
    render(<AutomaticRecoveryCard presentation={deriveRecoveryPresentation(status, true)} />)
    expect(screen.getByText('Automatic order recovery')).toBeInTheDocument()
    expect(screen.getByText('Verifying an uncertain order outcome')).toBeInTheDocument()
    expect(screen.getByText(/New shared-account risk is paused while the order result is verified/)).toBeInTheDocument()
    expect(screen.getByText(/123-abc/)).toBeInTheDocument()
  })

  it('marks the observation as stale instead of drawing a live conclusion', () => {
    const status = automaticStatus({ ...baseAutomatic, phase: 'reconciling', stale: true })
    render(<AutomaticRecoveryCard presentation={deriveRecoveryPresentation(status, false)} />)
    expect(screen.getByText('Stale observation')).toBeInTheDocument()
    expect(screen.getByText(/treat it as the last known state, not a live conclusion/)).toBeInTheDocument()
  })

  it('presents scoped recovery with reserved capital and orders as exact decimal strings', () => {
    const status = automaticStatus({
      ...baseAutomatic,
      phase: 'reconciling',
      isolated: true,
      admission_mode: 'funded_spot_sell',
      quarantined_orders: 2,
      reserved_base: { BTC: '0.125' },
      reserved_quote_worst_case: '5400.25',
    })
    render(<AutomaticRecoveryCard presentation={deriveRecoveryPresentation(status, true)} />)
    expect(screen.getByText(/Degraded trading — scoped recovery active/)).toBeInTheDocument()
    expect(screen.getByText(/Only funded Spot cash SELLs may continue/)).toBeInTheDocument()
    expect(screen.getByText('2')).toBeInTheDocument()
    expect(screen.getByText(/0.125 BTC/)).toBeInTheDocument()
    expect(screen.getByText(/5,400\.25 USDT/)).toBeInTheDocument()
  })

  it('keeps showing a reservation retained after stop', () => {
    const status = automaticStatus({ ...baseAutomatic, phase: 'settled_stopped', quarantined_orders: 1, reserved_quote_worst_case: '310.5' })
    render(<AutomaticRecoveryCard presentation={deriveRecoveryPresentation(status, true)} />)
    expect(screen.getByText('Reservation kept after stop')).toBeInTheDocument()
    expect(screen.getByText(/not rejected or erased/)).toBeInTheDocument()
  })

  it('flags operator investigation without discarding the reservation', () => {
    const status = automaticStatus({ ...baseAutomatic, phase: 'waiting_for_readiness', operator_required: true })
    render(<AutomaticRecoveryCard presentation={deriveRecoveryPresentation(status, true)} />)
    expect(screen.getByText(/Needs operator investigation/)).toBeInTheDocument()
    expect(screen.getByText(/Do not discard the reservation/)).toBeInTheDocument()
  })

  it('renders nothing when idle with no reservation', () => {
    const { container } = render(<AutomaticRecoveryCard presentation={deriveRecoveryPresentation(automaticStatus({ ...baseAutomatic }), true)} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('renders nothing when automatic recovery is not reported', () => {
    const { container } = render(<AutomaticRecoveryCard presentation={deriveRecoveryPresentation(automaticStatus(null), true)} />)
    expect(container).toBeEmptyDOMElement()
  })
})
