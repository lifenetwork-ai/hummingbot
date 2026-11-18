import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { HistoryFiltersBar } from './HistoryFiltersBar'
import type { AuditFilters, CommandFilters } from '../history/filters'

describe('HistoryFiltersBar', () => {
  describe('mode: commands', () => {
    it('renders command filter inputs and triggers onChange on field update', () => {
      const onChange = vi.fn()
      const filters: CommandFilters = { action: 'start', status: 'succeeded' }

      render(
        <HistoryFiltersBar
          mode="commands"
          filters={filters}
          onChange={onChange}
        />,
      )

      expect(screen.getByText('Commands Filter')).toBeInTheDocument()

      const sessionInput = screen.getByPlaceholderText('Filter by session_id…')
      fireEvent.change(sessionInput, { target: { value: 'session-xyz' } })

      expect(onChange).toHaveBeenCalledWith({
        action: 'start',
        status: 'succeeded',
        session_id: 'session-xyz',
      })
    })

    it('selects limit via the limit select', () => {
      const onChange = vi.fn()
      render(
        <HistoryFiltersBar
          mode="commands"
          filters={{}}
          onChange={onChange}
        />,
      )

      const limitSelect = screen.getByLabelText(/Limit/i)
      fireEvent.change(limitSelect, { target: { value: '50' } })
      expect(onChange).toHaveBeenCalledWith({ limit: 50 })
    })

    it('shows the active-filter count on the reset button and resets on click', () => {
      const onChange = vi.fn()
      render(
        <HistoryFiltersBar
          mode="commands"
          filters={{ limit: 999 }}
          validationErrors={{ limit: 'Limit cannot exceed 500' }}
          onChange={onChange}
        />,
      )

      expect(screen.getByText('Limit cannot exceed 500')).toBeInTheDocument()

      const resetBtn = screen.getByRole('button', { name: /Reset/i })
      fireEvent.click(resetBtn)
      expect(onChange).toHaveBeenCalledWith({ limit: 10 })
    })

    it('displays the active count badge when narrowing filters are set', () => {
      render(
        <HistoryFiltersBar
          mode="commands"
          filters={{ action: 'start', status: 'succeeded', limit: 50 }}
          onChange={vi.fn()}
        />,
      )

      expect(screen.getByText('2 active')).toBeInTheDocument()
      expect(screen.getByRole('button', { name: 'Reset (2)' })).toBeInTheDocument()
    })
  })

  describe('mode: events', () => {
    it('filters by event type and source selects', () => {
      const onChange = vi.fn()

      render(
        <HistoryFiltersBar
          mode="events"
          filters={{}}
          onChange={onChange}
        />,
      )

      expect(screen.getByText('Events Filter')).toBeInTheDocument()

      const eventTypeSelect = screen.getByLabelText(/Event Type/i)
      fireEvent.change(eventTypeSelect, { target: { value: 'session_created' } })
      expect(onChange).toHaveBeenCalledWith({ event_type: 'session_created' })

      const sourceSelect = screen.getByLabelText(/Source/i)
      fireEvent.change(sourceSelect, { target: { value: 'observer' } })
      expect(onChange).toHaveBeenCalledWith({ source: 'observer' })
    })
  })

  describe('mode: audit', () => {
    it('renders audit filter fields, validation errors, and triggers onChange', () => {
      const onChange = vi.fn()
      const filters: AuditFilters = { actor_id: 'user-1' }

      render(
        <HistoryFiltersBar
          mode="audit"
          filters={filters}
          onChange={onChange}
          validationErrors={{ from_time: 'Invalid UTC format' }}
        />,
      )

      expect(screen.getByText('Audit Filter')).toBeInTheDocument()
      expect(screen.getByText('Invalid UTC format')).toBeInTheDocument()

      // Advanced filters are collapsed by default; expand them first
      fireEvent.click(screen.getByRole('button', { name: /More filters/i }))

      const opInput = screen.getByPlaceholderText('Filter by operation_id…')
      fireEvent.change(opInput, { target: { value: 'op-999' } })

      expect(onChange).toHaveBeenCalledWith({
        actor_id: 'user-1',
        operation_id: 'op-999',
      })

      const resetBtn = screen.getByRole('button', { name: /Reset/i })
      fireEvent.click(resetBtn)
      expect(onChange).toHaveBeenCalledWith({ limit: 10 })
    })

    it('auto-opens advanced filters when one is already set', () => {
      render(
        <HistoryFiltersBar
          mode="audit"
          filters={{ exchange: 'okx' }}
          onChange={vi.fn()}
        />,
      )

      expect(screen.getByLabelText(/Exchange/i)).toBeInTheDocument()
      expect(screen.getByRole('button', { name: /Hide filters/i })).toBeInTheDocument()
    })

    it('shows the advanced-filter count on the disclosure toggle while collapsed', () => {
      render(
        <HistoryFiltersBar
          mode="audit"
          filters={{ environment: 'live', target_id: 't-1' }}
          onChange={vi.fn()}
        />,
      )

      // Auto-opened because advanced filters are set; collapse and check the badge survives
      const toggle = screen.getByRole('button', { name: /Hide filters/i })
      expect(screen.getByText('2')).toBeInTheDocument()
      fireEvent.click(toggle)
      expect(screen.getByRole('button', { name: /More filters \(2\)|More filters/i })).toBeInTheDocument()
      expect(screen.getByText('2')).toBeInTheDocument()
      expect(screen.queryByLabelText(/Environment/i)).not.toBeInTheDocument()
    })

    it('applies quick time presets, highlights the active segment, and clears via All time', () => {
      const onChange = vi.fn()
      const { rerender } = render(
        <HistoryFiltersBar
          mode="audit"
          filters={{}}
          onChange={onChange}
        />,
      )

      // "All time" is the default with no range set
      expect(screen.getByRole('button', { name: 'All time' }).getAttribute('aria-pressed')).toBe('true')

      const preset1h = screen.getByRole('button', { name: '1h' })
      fireEvent.click(preset1h)

      expect(onChange).toHaveBeenCalledTimes(1)
      const lastCall = onChange.mock.calls[0][0]
      expect(lastCall.from_time).toBeDefined()
      expect(lastCall.to_time).toBeDefined()

      // The clicked preset is highlighted when the filters match it
      rerender(
        <HistoryFiltersBar
          mode="audit"
          filters={lastCall}
          onChange={onChange}
        />,
      )
      expect(screen.getByRole('button', { name: '1h' }).getAttribute('aria-pressed')).toBe('true')
      expect(screen.getByRole('button', { name: 'All time' }).getAttribute('aria-pressed')).toBe('false')

      // A fixed range that matches no preset reads as custom
      rerender(
        <HistoryFiltersBar
          mode="audit"
          filters={{ from_time: '2026-11-10T10:00:00Z', to_time: '2026-11-10T11:00:00Z' }}
          onChange={onChange}
        />,
      )
      expect(screen.getByText('Custom range')).toBeInTheDocument()

      // "All time" clears the range
      fireEvent.click(screen.getByRole('button', { name: 'All time' }))
      expect(onChange).toHaveBeenCalledWith({})
    })

    it('supports selecting action, outcome, environment, exchange, connector, and limit via Selects', () => {
      const onChange = vi.fn()
      render(
        <HistoryFiltersBar
          mode="audit"
          filters={{}}
          onChange={onChange}
          exchanges={['okx', 'bybit']}
          connectors={['spot', 'perpetual']}
        />,
      )

      // Select action (primary)
      const actionSelect = screen.getByLabelText(/^Action/i)
      fireEvent.change(actionSelect, { target: { value: 'login' } })
      expect(onChange).toHaveBeenCalledWith({ action: 'login' })

      // Automatic recovery audit actions are filterable when the backend reports them
      fireEvent.change(actionSelect, { target: { value: 'automatic_recovery_order_rejected' } })
      expect(onChange).toHaveBeenCalledWith({ action: 'automatic_recovery_order_rejected' })

      // Select outcome (primary)
      const outcomeSelect = screen.getByLabelText(/Outcome/i)
      fireEvent.change(outcomeSelect, { target: { value: 'succeeded' } })
      expect(onChange).toHaveBeenCalledWith({ outcome: 'succeeded' })

      // Advanced filters
      fireEvent.click(screen.getByRole('button', { name: /More filters/i }))

      const envSelect = screen.getByLabelText(/Environment/i)
      fireEvent.change(envSelect, { target: { value: 'live' } })
      expect(onChange).toHaveBeenCalledWith({ environment: 'live' })

      const exSelect = screen.getByLabelText(/Exchange/i)
      fireEvent.change(exSelect, { target: { value: 'okx' } })
      expect(onChange).toHaveBeenCalledWith({ exchange: 'okx' })

      const connSelect = screen.getByLabelText(/Connector/i)
      fireEvent.change(connSelect, { target: { value: 'spot' } })
      expect(onChange).toHaveBeenCalledWith({ connector: 'spot' })

      const limitSelect = screen.getByLabelText(/Limit/i)
      fireEvent.change(limitSelect, { target: { value: '50' } })
      expect(onChange).toHaveBeenCalledWith({ limit: 50 })
    })

    it('renders actor select when accounts are available, and allows switching to manual input', () => {
      const onChange = vi.fn()
      const accounts = [
        { id: 'usr-123', email: 'alice@example.com', role: 'admin' as const },
        { id: 'usr-456', email: 'bob@example.com', role: 'read' as const },
      ]

      render(
        <HistoryFiltersBar
          mode="audit"
          filters={{}}
          onChange={onChange}
          accounts={accounts}
        />,
      )

      // Actor Select exists
      const actorSelect = screen.getByLabelText(/^Actor$/)
      fireEvent.change(actorSelect, { target: { value: 'usr-123' } })
      expect(onChange).toHaveBeenCalledWith({ actor_id: 'usr-123' })

      // Switch to manual input via the icon toggle
      fireEvent.click(screen.getByRole('button', { name: 'Switch to manual actor ID input' }))

      const actorInput = screen.getByPlaceholderText('Filter by actor_id…')
      expect(actorInput).toBeInTheDocument()

      // Switch back to the list select
      fireEvent.click(screen.getByRole('button', { name: 'Switch to actor list select' }))
      expect(screen.getByLabelText(/^Actor$/)).toBeInTheDocument()
    })
  })
})
