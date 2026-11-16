import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { components } from '../../core/api/schema'
import { CommandReceiptTracker } from './CommandReceiptTracker'

vi.mock('@tanstack/react-router', () => ({
  Link: ({ children, to, className }: { children: React.ReactNode; to?: string; className?: string }) => (
    <a href={to ?? '#'} className={className}>
      {children}
    </a>
  ),
}))

describe('CommandReceiptTracker', () => {
  const sampleReceipt = {
    id: 'cmd-998877665544332211',
    session_id: 'sess-1234',
    action: 'start',
    status: 'succeeded',
    effective_version: 1,
    created_at: '2026-11-10T00:00:00Z',
    updated_at: '2026-11-10T00:00:00Z',
  } as unknown as components['schemas']['CommandResponseOutput']

  it('renders command ID truncated with title and copy button', () => {
    render(<CommandReceiptTracker receipt={sampleReceipt} />)

    // Truncated display text (12 chars + …)
    expect(screen.getByText('cmd-99887766…')).toBeInTheDocument()
    expect(screen.getByTitle('cmd-998877665544332211')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Copy command ID/ })).toBeInTheDocument()
  })

  it('renders dash when receipt has no id', () => {
    render(<CommandReceiptTracker receipt={null} />)
    expect(screen.getAllByText('—').length).toBeGreaterThan(0)
    expect(screen.getByText('Command ID:').nextSibling).toHaveTextContent('—')
  })

  it('toggles between collapsed and expanded view when succeeded', async () => {
    const user = userEvent.setup()
    render(<CommandReceiptTracker receipt={sampleReceipt} />)

    // Initially collapsed
    expect(screen.getByText('Command start successfully applied')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /View details/ })).toBeInTheDocument()

    // Expand
    await user.click(screen.getByRole('button', { name: /View details/ }))
    expect(screen.getByRole('button', { name: /Collapse/ })).toBeInTheDocument()
    expect(screen.getByText('Submitted at:')).toBeInTheDocument()

    // Collapse again
    await user.click(screen.getByRole('button', { name: /Collapse/ }))
    expect(screen.getByRole('button', { name: /View details/ })).toBeInTheDocument()
  })

  const switchReceipt = {
    id: 'cmd-switch-0001',
    session_id: 'sess-1234',
    action: 'switch',
    status: 'succeeded',
    effective_version: 5,
    expected_version: 5,
    created_at: '2026-11-10T00:10:00Z',
    updated_at: '2026-11-10T00:10:00Z',
    payload: {
      action: 'switch',
      expected_version: 5,
      market_mode: 'spot',
      selection: 'fixed',
      profile_id: 'follow_market',
      duration_mode: 'timed',
      duration_seconds: 3600,
    },
  } as unknown as components['schemas']['CommandResponseOutput']

  const previousSwitchReceipt = {
    id: 'cmd-previous-0001',
    session_id: 'sess-1234',
    action: 'switch',
    status: 'succeeded',
    effective_version: 4,
    expected_version: 4,
    created_at: '2026-11-09T00:00:00Z',
    updated_at: '2026-11-09T00:00:00Z',
    payload: {
      action: 'switch',
      expected_version: 4,
      market_mode: 'both',
      selection: 'fixed',
      profile_id: 'sideways',
      duration_mode: 'until_stopped',
    },
  } as unknown as components['schemas']['CommandResponseOutput']

  it('renders the submitted configuration recorded in the receipt payload', () => {
    render(<CommandReceiptTracker receipt={switchReceipt} defaultCollapsed={false} />)

    expect(screen.getByText('Submitted Configuration')).toBeInTheDocument()
    expect(screen.getByText('Task profile')).toBeInTheDocument()
    expect(screen.getByText('follow_market')).toBeInTheDocument()
    expect(screen.getByText('Market mode')).toBeInTheDocument()
    expect(screen.getByText('spot')).toBeInTheDocument()
    expect(screen.getByText('Duration')).toBeInTheDocument()
    expect(screen.getByText('60 min')).toBeInTheDocument()
  })

  it('shows switched configuration from previous to applied', () => {
    render(
      <CommandReceiptTracker
        receipt={switchReceipt}
        previousReceipt={previousSwitchReceipt}
        defaultCollapsed={false}
      />,
    )

    expect(screen.getByText('Switched Configuration')).toBeInTheDocument()
    expect(screen.getByText('Replaced (previous)')).toBeInTheDocument()
    expect(screen.getByText('sideways')).toBeInTheDocument()
    expect(screen.getAllByText('follow_market').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Changed').length).toBeGreaterThan(0)
  })

  it('explains when no previous configuration receipt exists', () => {
    render(<CommandReceiptTracker receipt={switchReceipt} previousReceipt={null} previousLoading={false} defaultCollapsed={false} />)

    expect(screen.getByText(/No earlier configuration receipt was found/)).toBeInTheDocument()
  })

  it('summarizes the profile change in the collapsed strip', () => {
    render(
      <CommandReceiptTracker
        receipt={switchReceipt}
        previousReceipt={previousSwitchReceipt}
      />,
    )

    expect(screen.getByText('Command switch successfully applied')).toBeInTheDocument()
    expect(screen.getByText('sideways')).toBeInTheDocument()
    expect(screen.getByText('follow_market')).toBeInTheDocument()
  })
})
