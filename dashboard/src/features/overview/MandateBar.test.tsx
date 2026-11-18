import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { contextStatus } from '../../test/mocks/contextFixtures'
import { deriveOverview } from './model'
import { deriveCapitalRisk, deriveMandate } from './supplement'
import { MandateBar } from './MandateBar'

type Status = typeof contextStatus
const loading = { phase: 'loading' as const, data: undefined, error: null, refreshing: false }
function renderStatus(data: Status | undefined, failed = false) {
  const model = deriveOverview({ status: { phase: failed ? 'error' : data ? 'ready' : 'loading', data, error: null, refreshing: false }, liquidity: loading, positions: loading, balances: loading })
  return render(<MandateBar view={deriveMandate(model, null)} guards={deriveCapitalRisk(model, null).guardsSummary} />)
}

describe('mandate operational status', () => {
  it('formats risk amounts by their backend unit and keeps missing exposure unknown', () => {
    const model = deriveOverview({ status: { phase: 'ready', data: contextStatus, error: null, refreshing: false }, liquidity: loading, positions: loading, balances: loading })
    const view = { ...deriveMandate(model, null), riskLimits: [
      { ruleId: 'drawdown', current: '857.669896390764799999999996', limit: '2500', status: 'ok' as const, unit: 'USDT' },
      { ruleId: 'inventory', current: null, limit: '0.05', status: 'unknown' as const, unit: 'BTC' },
    ] }
    render(<MandateBar view={view} guards={deriveCapitalRisk(model, null).guardsSummary} />)
    expect(screen.getByText('857.67 USDT')).toBeInTheDocument()
    expect(screen.getByText('0.05000000 BTC')).toBeInTheDocument()
    expect(screen.queryByText(/857\.669896390/)).not.toBeInTheDocument()
  })

  it.each(['running', 'starting', 'stopping', 'stopped', 'halted'])('renders runtime phase %s independently of requested state', (phase) => {
    renderStatus({ ...contextStatus, phase, running: phase === 'running' })
    expect(screen.getByText(phase.toUpperCase())).toBeInTheDocument()
  })
  it('prioritizes the HALT fence over running phase', () => {
    renderStatus({ ...contextStatus, recovery: { ...contextStatus.recovery!, halted: true, next_action: 'contact_support' } })
    expect(screen.getByText('HALTED')).toBeInTheDocument()
    expect(screen.queryByText('RUNNING')).not.toBeInTheDocument()
  })
  it('shows shutdown verification as stopping', () => {
    renderStatus({ ...contextStatus, phase: 'halted', running: false, recovery: { ...contextStatus.recovery!, halted: true, next_action: 'wait' } })
    expect(screen.getByText('STOPPING')).toBeInTheDocument()
  })
  it('shows missing data as unknown rather than stopped', () => {
    renderStatus(undefined)
    expect(screen.getByText('UNKNOWN')).toBeInTheDocument()
    expect(screen.queryByText('STOPPED')).not.toBeInTheDocument()
  })
  it('does not guess from running=false for an unrecognized phase', () => {
    renderStatus({ ...contextStatus, phase: 'unrecognized', running: false })
    expect(screen.getByText('UNKNOWN')).toBeInTheDocument()
  })
  it.each(['stale', 'mismatch', 'refresh_failure', 'owner_unavailable'])('does not present %s data as a current operational state', (condition) => {
    renderStatus({ ...contextStatus, owner_available: condition !== 'owner_unavailable', observation: { ...contextStatus.observation!, stale: condition === 'stale', session_matches: condition !== 'mismatch' } }, condition === 'refresh_failure')
    expect(screen.getByText(condition === 'mismatch' ? 'UNKNOWN' : 'STALE')).toBeInTheDocument()
    expect(screen.queryByText('RUNNING')).not.toBeInTheDocument()
  })
})


it('labels identical controller rules by market and does not count unknown as a breach', () => {
  const model = deriveOverview({ status: loading, liquidity: loading, positions: loading, balances: loading })
  const view = { ...deriveMandate(model, null), riskLimits: ['spot', 'perpetual'].map(market => ({ ruleId: 'drawdown_controller', market, controllerId: market, unit: 'USDT', limit: '2500', current: null, status: 'unknown' as const })) }
  render(<MandateBar view={view} guards={{ status: 'attention', headline: 'Exposure unverified', violationCount: 0, unknownCount: 2, totalCount: 2, available: false }} />)
  expect(screen.getByText(/Spot · drawdown_controller/)).toBeInTheDocument()
  expect(screen.getByText(/Perp · drawdown_controller/)).toBeInTheDocument()
  expect(screen.getByText('Guards: 0 breaches · 2 unverified / 2 limits')).toBeInTheDocument()
  expect(screen.getByText(/allocated capital not configured/)).toBeInTheDocument()
})
