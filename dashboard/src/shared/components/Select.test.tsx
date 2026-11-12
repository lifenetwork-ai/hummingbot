import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { Select } from './Select'

describe('Select Component', () => {
  it('renders select with options prop and custom chevron', () => {
    const handleChange = vi.fn()
    render(
      <Select
        label="Market"
        value="spot"
        onChange={handleChange}
        options={[
          { value: 'all', label: 'All' },
          { value: 'spot', label: 'Spot' },
          { value: 'perp', label: 'Perpetual', disabled: true },
        ]}
      />
    )

    const select = screen.getByRole('combobox', { name: 'Market' })
    expect(select).toBeInTheDocument()
    expect(select).toHaveValue('spot')

    // Check options
    const options = screen.getAllByRole('option')
    expect(options).toHaveLength(3)
    expect(options[0]).toHaveTextContent('All')
    expect(options[1]).toHaveTextContent('Spot')
    expect(options[2]).toBeDisabled()

    // Change value
    fireEvent.change(select, { target: { value: 'all' } })
    expect(handleChange).toHaveBeenCalled()
  })

  it('renders with children when options prop is not passed', () => {
    render(
      <Select label="Status">
        <option value="open">Open</option>
        <option value="closed">Closed</option>
      </Select>
    )

    const select = screen.getByRole('combobox', { name: 'Status' })
    expect(select).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Open' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Closed' })).toBeInTheDocument()
  })

  it('renders without label and without container wrapper when label, hint, error are absent', () => {
    const { container } = render(
      <Select
        aria-label="Filter"
        sizeVariant="sm"
        options={[{ value: '1', label: 'Option 1' }]}
      />
    )

    expect(screen.queryByRole('paragraph')).not.toBeInTheDocument()
    const select = screen.getByRole('combobox', { name: 'Filter' })
    expect(select).toHaveClass('h-8')
    expect(container.querySelector('label')).not.toBeInTheDocument()
  })

  it('displays hint text and connects aria-describedby', () => {
    render(
      <Select
        id="demo-select"
        label="Selection"
        hint="Choose appropriate strategy profile"
        options={[{ value: 'p1', label: 'Profile 1' }]}
      />
    )

    const select = screen.getByRole('combobox', { name: 'Selection' })
    expect(select).toHaveAttribute('aria-describedby', 'demo-select-hint')
    expect(screen.getByText('Choose appropriate strategy profile')).toBeInTheDocument()
  })

  it('displays error text and sets aria-invalid to true', () => {
    render(
      <Select
        id="error-select"
        label="Profile"
        error="Invalid profile"
        options={[{ value: '', label: 'Select profile' }]}
      />
    )

    const select = screen.getByRole('combobox', { name: 'Profile' })
    expect(select).toHaveAttribute('aria-invalid', 'true')
    expect(select).toHaveAttribute('aria-describedby', 'error-select-hint')
    expect(screen.getByText('Invalid profile')).toBeInTheDocument()
    expect(select).toHaveClass('border-app-danger')
  })

  it('respects disabled state', () => {
    render(
      <Select
        label="Disabled Select"
        disabled
        options={[{ value: '1', label: 'One' }]}
      />
    )

    const select = screen.getByRole('combobox', { name: 'Disabled Select' })
    expect(select).toBeDisabled()
  })
})
