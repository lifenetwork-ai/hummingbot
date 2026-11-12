import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { SessionEventsTimeline } from './SessionEventsTimeline'
import type { components } from '../../core/api/schema'

describe('SessionEventsTimeline', () => {
  const sampleEvents = [
    {
      id: 1,
      session_id: 'sess-123',
      event_type: 'profile_switched',
      source: 'random_cycle_timer',
      new_profile: 'conservative_spread',
      payload: { note: 'Switched profile successfully on random cycle' },
      created_at: '2026-11-10T12:00:00Z',
    },
  ] as unknown as components['schemas']['MMEventViewOutput'][]

  it('renders timeline with events details', () => {
    render(<SessionEventsTimeline events={sampleEvents} />)

    expect(screen.getByText('Session Events Timeline')).toBeInTheDocument()
    expect(screen.getByText('profile_switched')).toBeInTheDocument()
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
        filters={{ event_type: 'profile_switched' }}
        onFilterChange={onFilterChange}
      />,
    )

    expect(screen.getByText('Events Filter')).toBeInTheDocument()
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
