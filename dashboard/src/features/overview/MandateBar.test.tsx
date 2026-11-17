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
