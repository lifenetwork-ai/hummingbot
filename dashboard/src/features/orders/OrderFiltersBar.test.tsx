import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { OrderFiltersBar } from './OrderFiltersBar'

describe('OrderFiltersBar', () => {
  it('renders market buttons and state Select component', () => {
    const onFiltersChange = vi.fn()
    render(
      <OrderFiltersBar
        filters={{ market: 'all', state: 'all' }}
        onFiltersChange={onFiltersChange}
      />
    )

    expect(screen.getByRole('button', { name: 'All' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Spot' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Perpetual' })).toBeInTheDocument()

    const stateSelect = screen.getByRole('combobox', { name: 'State filter' })
    expect(stateSelect).toBeInTheDocument()
    expect(stateSelect).toHaveValue('all')

    fireEvent.change(stateSelect, { target: { value: 'open' } })
    expect(onFiltersChange).toHaveBeenCalledWith({ market: 'all', state: 'open' })
  })
})
