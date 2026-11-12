import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { HistoryFiltersBar } from './HistoryFiltersBar'
import type { AuditFilters, CommandFilters, EventFilters } from '../history/filters'
import type { components } from '../../core/api/schema'

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

    it('displays validation error when limit is invalid and resets filters on click', () => {
      const onChange = vi.fn()
      render(
        <HistoryFiltersBar
          mode="commands"
          filters={{ limit: 999 }}
          onChange={onChange}
          validationErrors={{ limit: 'Limit cannot exceed 500' }}
        />,
      )

      expect(screen.getByText('Limit cannot exceed 500')).toBeInTheDocument()

      const resetBtn = screen.getByRole('button', { name: /Reset filters/i })
      fireEvent.click(resetBtn)
      expect(onChange).toHaveBeenCalledWith({})
    })
  })

  describe('mode: events', () => {
    it('renders event filters, allows selecting session, and updates filters', () => {
      const onChange = vi.fn()
      const onSelectSession = vi.fn()
      const filters: EventFilters = {}

      render(
        <HistoryFiltersBar
          mode="events"
          filters={filters}
          onChange={onChange}
          sessions={[
            {
              id: 'sess-12345678-abcd',
              profile_id: 'pure_market_making',
            } as unknown as components['schemas']['SessionStateOutput'],
          ]}
          selectedSessionId="sess-12345678-abcd"
          onSelectSession={onSelectSession}
        />,
      )

      expect(screen.getByText('Events Filter')).toBeInTheDocument()

      const eventTypeInput = screen.getByPlaceholderText('e.g. state_changed, step_expired…')
      fireEvent.change(eventTypeInput, { target: { value: 'state_changed' } })
      expect(onChange).toHaveBeenCalledWith({ event_type: 'state_changed' })

      const select = screen.getByLabelText(/Select session to track/i)
      fireEvent.change(select, { target: { value: '' } })
      expect(onSelectSession).toHaveBeenCalledWith(undefined)
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

      const opInput = screen.getByPlaceholderText('Filter by operation_id…')
      fireEvent.change(opInput, { target: { value: 'op-999' } })

      expect(onChange).toHaveBeenCalledWith({
        actor_id: 'user-1',
        operation_id: 'op-999',
      })

      const resetBtn = screen.getByRole('button', { name: /Reset filters/i })
      fireEvent.click(resetBtn)
      expect(onChange).toHaveBeenCalledWith({})
    })

    it('supports quick date/time presets and clearing date/time range', () => {
      const onChange = vi.fn()
      const { rerender } = render(
        <HistoryFiltersBar
          mode="audit"
          filters={{}}
          onChange={onChange}
        />,
      )

      const preset1h = screen.getByRole('button', { name: 'Last 1h' })
      fireEvent.click(preset1h)

      expect(onChange).toHaveBeenCalledTimes(1)
      const lastCall = onChange.mock.calls[0][0]
      expect(lastCall.from_time).toBeDefined()
      expect(lastCall.to_time).toBeDefined()

      // When from_time / to_time is present, "Clear time range" appears
      rerender(
        <HistoryFiltersBar
          mode="audit"
          filters={{ from_time: '2026-11-10T10:00:00Z', to_time: '2026-11-10T11:00:00Z' }}
          onChange={onChange}
        />,
      )

      const clearTimeBtn = screen.getByRole('button', { name: 'Clear time range' })
      fireEvent.click(clearTimeBtn)
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

      // Select action
      const actionSelect = screen.getByLabelText(/Action/i)
      fireEvent.change(actionSelect, { target: { value: 'login' } })
      expect(onChange).toHaveBeenCalledWith({ action: 'login' })

      // Select outcome
      const outcomeSelect = screen.getByLabelText(/Outcome/i)
      fireEvent.change(outcomeSelect, { target: { value: 'succeeded' } })
      expect(onChange).toHaveBeenCalledWith({ outcome: 'succeeded' })

      // Select environment
      const envSelect = screen.getByLabelText(/Environment/i)
      fireEvent.change(envSelect, { target: { value: 'live' } })
      expect(onChange).toHaveBeenCalledWith({ environment: 'live' })

      // Select exchange
      const exSelect = screen.getByLabelText(/Exchange/i)
      fireEvent.change(exSelect, { target: { value: 'okx' } })
      expect(onChange).toHaveBeenCalledWith({ exchange: 'okx' })

      // Select connector
      const connSelect = screen.getByLabelText(/Connector/i)
      fireEvent.change(connSelect, { target: { value: 'spot' } })
      expect(onChange).toHaveBeenCalledWith({ connector: 'spot' })

      // Select limit
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
      const actorSelect = screen.getByLabelText(/Actor ID/i)
      fireEvent.change(actorSelect, { target: { value: 'usr-123' } })
      expect(onChange).toHaveBeenCalledWith({ actor_id: 'usr-123' })

      // Click "Enter ID manually"
      const toggleManualBtn = screen.getByRole('button', { name: 'Enter ID manually' })
      fireEvent.click(toggleManualBtn)

      // Now manual input exists
      const actorInput = screen.getByPlaceholderText('Filter by actor_id…')
      expect(actorInput).toBeInTheDocument()

      // Click "Select from list" to return to select
      const toggleSelectBtn = screen.getByRole('button', { name: 'Select from list' })
      fireEvent.click(toggleSelectBtn)
      expect(screen.getByLabelText(/Actor ID/i)).toBeInTheDocument()
    })
  })
})
