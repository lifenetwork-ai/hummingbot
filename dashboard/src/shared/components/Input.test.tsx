import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { Input } from './Input'

describe('Input', () => {
  it('labels the control and associates error text', () => {
    render(<Input label="Display name" error="Required" />)
    const field = screen.getByRole('textbox', { name: 'Display name' })
    expect(field).toHaveAttribute('aria-invalid', 'true')
    expect(field).toHaveAttribute('aria-describedby', `${field.id}-hint`)
    expect(screen.getByText('Required')).toHaveAttribute('id', `${field.id}-hint`)
  })

  it('supports a hint, supplied id and disabled state', () => {
    render(<Input id="account" label="Account" hint="Visible to you only" disabled />)
    const field = screen.getByLabelText('Account')
    expect(field).toHaveAttribute('id', 'account')
    expect(field).toHaveAttribute('aria-describedby', 'account-hint')
    expect(field).toBeDisabled()
    expect(field).toHaveAttribute('aria-invalid', 'false')
  })

  it('does not set description when optional text is absent', () => {
    render(<Input label="Plain" />)
    expect(screen.getByRole('textbox', { name: 'Plain' })).not.toHaveAttribute('aria-describedby')
  })
})
