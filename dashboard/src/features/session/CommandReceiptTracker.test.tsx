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
})
