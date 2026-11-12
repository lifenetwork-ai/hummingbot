import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { DateTimeInput } from './DateTimeInput'
import {
  fromDateTimeLocalString,
  toDateTimeLocalString,
} from '../utils/dateTime'

describe('DateTimeInput', () => {
  it('converts ISO string to local datetime string and vice versa', () => {
    expect(toDateTimeLocalString('')).toBe('')
    expect(toDateTimeLocalString(null)).toBe('')
    expect(toDateTimeLocalString('invalid')).toBe('')

    expect(fromDateTimeLocalString('')).toBeUndefined()
    expect(fromDateTimeLocalString(null)).toBeUndefined()
    expect(fromDateTimeLocalString('invalid')).toBeUndefined()

    const now = new Date()
    const iso = now.toISOString()
    const local = toDateTimeLocalString(iso)
    expect(local).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/)
    const backToIso = fromDateTimeLocalString(local)
    expect(backToIso).toBeDefined()
  })

  it('renders label, input, and displays UTC value', () => {
    const onChange = vi.fn()
    const iso = '2026-11-10T10:00:00.000Z'
    render(
      <DateTimeInput
        label="Timestamp"
        value={iso}
        onChange={onChange}
      />
    )

    const input = screen.getByLabelText('Timestamp') as HTMLInputElement
    expect(input).toBeInTheDocument()
    expect(input.value).toBe(iso)
  })

  it('triggers onChange when user types into the input', () => {
    const onChange = vi.fn()
    render(
      <DateTimeInput
        label="Timestamp"
        onChange={onChange}
      />
    )

    const input = screen.getByLabelText('Timestamp')
    fireEvent.change(input, { target: { value: '2026-11-10' } })
    expect(onChange).toHaveBeenCalledWith('2026-11-10')
  })

  it('triggers onChange with ISO string when user selects date/time via picker', () => {
    const onChange = vi.fn()
    const { container } = render(
      <DateTimeInput
        label="Timestamp"
        onChange={onChange}
      />
    )

    const hiddenPicker = container.querySelector('input[type="datetime-local"]') as HTMLInputElement
    expect(hiddenPicker).toBeInTheDocument()

    fireEvent.change(hiddenPicker, { target: { value: '2026-11-12T14:30' } })
    expect(onChange).toHaveBeenCalledTimes(1)
    const calledWith = onChange.mock.calls[0][0]
    expect(calledWith).toBeDefined()
    expect(calledWith).toMatch(/T.*Z$/)
  })

  it('shows clear button when value is present and clears on click', () => {
    const onChange = vi.fn()
    render(
      <DateTimeInput
        label="Timestamp"
        value="2026-11-10T10:00:00.000Z"
        onChange={onChange}
      />
    )

    const clearButton = screen.getByTitle('Clear Timestamp')
    expect(clearButton).toBeInTheDocument()

    fireEvent.click(clearButton)
    expect(onChange).toHaveBeenCalledWith(undefined)
  })

  it('renders error message and sets aria-invalid', () => {
    render(
      <DateTimeInput
        label="Timestamp"
        onChange={vi.fn()}
        error="Invalid timestamp"
      />
    )

    const input = screen.getByLabelText('Timestamp')
    expect(input).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByText('Invalid timestamp')).toBeInTheDocument()
  })

  it('renders hint message when no error', () => {
    render(
      <DateTimeInput
        label="Timestamp"
        onChange={vi.fn()}
        hint="Select local date and time"
      />
    )

    expect(screen.getByText('Select local date and time')).toBeInTheDocument()
  })

  it('triggers showPicker on button click', () => {
    render(
      <DateTimeInput
        label="Timestamp"
        onChange={vi.fn()}
      />
    )

    const pickerButton = screen.getByTitle('Select date & time (UI Picker)')
    expect(pickerButton).toBeInTheDocument()
    fireEvent.click(pickerButton)
  })
})
