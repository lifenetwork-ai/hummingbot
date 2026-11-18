import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { SessionEventsTimeline } from './SessionEventsTimeline'
import type { components } from '../../core/api/schema'

describe('SessionEventsTimeline', () => {
  const sampleEvents = [
    {
      id: 1,
      session_id: 'sess-123',
      event_type: 'step_changed',
      source: 'random_cycle_timer',
      new_profile: 'conservative_spread',
      payload: { note: 'Switched profile successfully on random cycle' },
      created_at: '2026-11-10T12:00:00Z',
    },
  ] as unknown as components['schemas']['MMEventViewOutput'][]

  it('renders timeline with events details', () => {
    render(<SessionEventsTimeline events={sampleEvents} />)

    expect(screen.getByText('Session Events Timeline')).toBeInTheDocument()
    expect(screen.getByText('step_changed')).toBeInTheDocument()
    expect(screen.getByText('source: random_cycle_timer')).toBeInTheDocument()
    expect(screen.getByText('conservative_spread')).toBeInTheDocument()
    expect(
      screen.getByText(/Switched profile successfully on random cycle/),
    ).toBeInTheDocument()
  })

  it('renders loading state when isLoading is true', () => {
    render(<SessionEventsTimeline isLoading={true} />)
    expect(screen.getByText('Loading session events…')).toBeInTheDocument()
  })

  it('renders empty message when no events exist', () => {
    render(<SessionEventsTimeline events={[]} />)
    expect(screen.getByText('No session events recorded yet.')).toBeInTheDocument()
  })

  it('renders filter bar when filters and onFilterChange are provided', () => {
    const onFilterChange = vi.fn()
    render(
      <SessionEventsTimeline
        events={sampleEvents}
        filters={{ event_type: 'step_changed' }}
        onFilterChange={onFilterChange}
      />,
    )

    expect(screen.getByText('Events Filter')).toBeInTheDocument()
  })

  it('renders payload collapsed by default and expands on toggle', () => {
    const longEvents = [
      {
        ...sampleEvents[0],
        event_type: 'session_created',
        payload: { configuration: { catalogue: 'x'.repeat(500), request: 'y'.repeat(500) } },
      },
    ] as unknown as components['schemas']['MMEventViewOutput'][]
    render(<SessionEventsTimeline events={longEvents} />)

    const summary = screen.getByText(/payload \(1 keys\)/i)
    const details = summary.closest('details')
    expect(details).not.toBeNull()
    expect(details).not.toHaveAttribute('open')

    fireEvent.click(summary)
    expect(details).toHaveAttribute('open')
    const block = screen.getByText(/"configuration"/, { exact: false }).closest('pre')
    expect(block).toHaveClass('whitespace-pre-wrap', 'break-all', 'overflow-auto')
  })

  it('triggers onLoadMore when pagination button is clicked', () => {
    const onLoadMore = vi.fn()
    render(
      <SessionEventsTimeline
        events={sampleEvents}
        hasMore={true}
        onLoadMore={onLoadMore}
      />,
    )

    const loadMoreBtn = screen.getByRole('button', { name: /Load more events/i })
    fireEvent.click(loadMoreBtn)
    expect(onLoadMore).toHaveBeenCalledTimes(1)
  })
})
