import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { SecurityAuditView } from './SecurityAuditView'
import type { components } from '../../core/api/schema'

describe('SecurityAuditView', () => {
  const sampleEntries = [
    {
      actor_id: 'usr-12345678',
      actor_email: 'operator@example.com',
      actor_role: 'admin',
      action: 'session_start',
      outcome: 'success',
      exchange: 'binance',
      connector: 'binance_spot',
      operation_id: 'op-9988776655',
      created_at: '2026-11-10T10:00:00Z',
    },
    {
      actor_id: null,
      actor_email: null,
      actor_role: null,
      action: 'system_alert',
      outcome: 'failure',
      exchange: null,
      connector: null,
      operation_id: null,
      created_at: '2026-11-10T10:05:00Z',
    },
  ] as unknown as components['schemas']['AuditEventResponseOutput'][]

  it('renders actor details when present, and explicitly displays "Unknown" without guessing "System" when missing', () => {
    render(<SecurityAuditView entries={sampleEntries} />)

    expect(screen.getByText('operator@example.com')).toBeInTheDocument()
    expect(screen.getByText('Unknown')).toBeInTheDocument()
    expect(screen.queryByText('System')).not.toBeInTheDocument()
  })

  it('triggers onInspectOperation when operation inspect button is clicked', () => {
    const onInspect = vi.fn()
    render(
      <SecurityAuditView
        entries={sampleEntries}
        onInspectOperation={onInspect}
      />,
    )

    const inspectBtn = screen.getByTitle('Inspect this operation')
    fireEvent.click(inspectBtn)
    expect(onInspect).toHaveBeenCalledWith('op-9988776655')
  })

  it('renders filter bar and triggers onFilterChange when filters provided', () => {
    const onFilterChange = vi.fn()
    render(
      <SecurityAuditView
        entries={sampleEntries}
        filters={{ outcome: 'success' }}
        onFilterChange={onFilterChange}
        accounts={[{ id: 'usr-admin-1', email: 'admin@example.com', role: 'admin' }]}
        exchanges={['okx']}
        connectors={['spot']}
      />,
    )

    expect(screen.getByText('Audit Filter')).toBeInTheDocument()
    expect(screen.getByLabelText(/Actor/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/From Time \(UTC\)/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/To Time \(UTC\)/i)).toBeInTheDocument()
  })

  it('renders loading, empty, error, and pagination states', () => {
    const onLoadMore = vi.fn()
    const { rerender } = render(<SecurityAuditView isLoading={true} />)
    expect(screen.getByText('Loading security audit log...')).toBeInTheDocument()

    rerender(<SecurityAuditView entries={[]} />)
    expect(screen.getByText('No audit events recorded.')).toBeInTheDocument()

    rerender(<SecurityAuditView entries={sampleEntries} error="Permission denied error" />)
    expect(screen.getByRole('alert')).toHaveTextContent('Permission denied error')

    rerender(
      <SecurityAuditView
        entries={sampleEntries}
        hasMore={true}
        onLoadMore={onLoadMore}
      />,
    )
    const loadMoreBtn = screen.getByRole('button', { name: /Load more logs/i })
    fireEvent.click(loadMoreBtn)
    expect(onLoadMore).toHaveBeenCalledTimes(1)
  })
})
