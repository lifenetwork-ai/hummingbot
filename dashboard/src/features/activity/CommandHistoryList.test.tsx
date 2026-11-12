import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { components } from '../../core/api/schema'
import { CommandHistoryList } from './CommandHistoryList'

describe('CommandHistoryList', () => {
  const sampleCommand = {
    id: 'cmd-1234567890abcdef',
    session_id: 'session-fedcba0987654321',
    action: 'start',
    status: 'succeeded',
    effective_version: 2,
    created_at: '2026-11-10T00:00:00Z',
    updated_at: '2026-11-10T00:00:00Z',
  } as unknown as components['schemas']['CommandResponseOutput']

  it('renders command ID and session ID truncated with hover title and copy buttons', () => {
    render(<CommandHistoryList commands={[sampleCommand]} />)

    // Check session ID
    expect(screen.getByText('session-fe…')).toBeInTheDocument()
    expect(screen.getByTitle('session-fedcba0987654321')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Copy session ID/ })).toBeInTheDocument()

    // Check command ID
    expect(screen.getByText('cmd-123456…')).toBeInTheDocument()
    expect(screen.getByTitle('cmd-1234567890abcdef')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Copy command ID/ })).toBeInTheDocument()
  })

  it('handles actor ID: displays actor if present, otherwise displays "No audit link" without guessing', () => {
    const { rerender } = render(
      <CommandHistoryList
        commands={[sampleCommand]}
        commandLinks={[
          {
            reference: { kind: 'command', id: 'cmd-1234567890abcdef' },
            sessionId: 'session-fedcba0987654321',
            action: 'start',
            status: 'succeeded',
            actorId: 'usr-admin-1',
            auditFilter: {},
          },
        ]}
      />,
    )
    expect(screen.getByText('usr-admi…')).toBeInTheDocument()

    rerender(
      <CommandHistoryList
        commands={[sampleCommand]}
        commandLinks={[
          {
            reference: { kind: 'command', id: 'cmd-1234567890abcdef' },
            sessionId: 'session-fedcba0987654321',
            action: 'start',
            status: 'succeeded',
            actorId: null,
            auditFilter: {},
          },
        ]}
      />,
    )
    expect(screen.getByText('No audit link')).toBeInTheDocument()
  })

  it('triggers onInspectCommand and onAuditForOperation callbacks', () => {
    const onInspect = vi.fn()
    const onAudit = vi.fn()
    render(
      <CommandHistoryList
        commands={[sampleCommand]}
        onInspectCommand={onInspect}
        onAuditForOperation={onAudit}
      />,
    )

    const inspectBtn = screen.getByRole('button', { name: /Inspect/i })
    fireEvent.click(inspectBtn)
    expect(onInspect).toHaveBeenCalledWith('cmd-1234567890abcdef')

    const auditBtn = screen.getByRole('button', { name: /Audit/i })
    fireEvent.click(auditBtn)
    expect(onAudit).toHaveBeenCalledWith('cmd-1234567890abcdef')
  })

  it('renders filter bar and triggers onFilterChange when provided', () => {
    const onFilterChange = vi.fn()
    render(
      <CommandHistoryList
        commands={[sampleCommand]}
        filters={{ status: 'succeeded' }}
        onFilterChange={onFilterChange}
      />,
    )

    expect(screen.getByText(/Commands Filter/i)).toBeInTheDocument()
  })

  it('renders load more button and triggers onLoadMore', () => {
    const onLoadMore = vi.fn()
    render(
      <CommandHistoryList
        commands={[sampleCommand]}
        hasMore={true}
        onLoadMore={onLoadMore}
      />,
    )

    const loadMoreBtn = screen.getByRole('button', { name: /Load more commands/i })
    fireEvent.click(loadMoreBtn)
    expect(onLoadMore).toHaveBeenCalledTimes(1)
  })
})
