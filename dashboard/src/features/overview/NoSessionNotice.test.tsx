import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { components } from '../../core/api/schema'
import { NoSessionNotice } from './NoSessionNotice'

vi.mock('@tanstack/react-router', () => ({
  Link: ({ children, to }: { children: React.ReactNode; to?: string }) => <a href={to ?? '#'}>{children}</a>,
}))

type StatusData = components['schemas']['StatusResponseOutput']

function status(overrides: Partial<StatusData>): StatusData {
  return {
    acknowledged_version: 1,
    bot_id: 'test-bot',
    control_version: 1,
    desired_state: 'stopped',
    error: null,
    heartbeat_age_seconds: 1,
    owner_available: true,
    phase: 'stopped',
    running: false,
    server_time: '2026-11-12T00:00:00Z',
    session: null,
    observed_session: null,
    ...overrides,
  } as StatusData
}

describe('NoSessionNotice', () => {
  it('renders the onboarding explanation and start CTA when no session exists', () => {
    render(<NoSessionNotice status={status({})} />)
    expect(screen.getByText('No session has been started yet')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Start new session' })).toHaveAttribute('href', '/session')
    expect(screen.getByText(/indicators below will show NO DATA/i)).toBeInTheDocument()
  })

  it('stays hidden while a session is running', () => {
    render(<NoSessionNotice status={status({ running: true, phase: 'running', desired_state: 'running' })} />)
    expect(screen.queryByText('No session has been started yet')).not.toBeInTheDocument()
  })

  it('stays hidden when a session was previously observed, even if currently stopped', () => {
    render(
      <NoSessionNotice
        status={status({ observed_session: { id: 's1', generation: 1, profile_id: 'fixed_tight', status: 'stopped' } as unknown as components['schemas']['SessionStateOutput'] })}
      />,
    )
    expect(screen.queryByText('No session has been started yet')).not.toBeInTheDocument()
  })

  it('stays hidden without a status observation', () => {
    render(<NoSessionNotice status={null} />)
    expect(screen.queryByText('No session has been started yet')).not.toBeInTheDocument()
  })
})
