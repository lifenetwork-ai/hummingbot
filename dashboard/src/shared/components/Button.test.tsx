import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { Button } from './Button'
import { buttonClassName } from './buttonClassName'

describe('Button', () => {
  it('uses button type by default and delegates events', () => {
    const onClick = vi.fn()
    render(<Button onClick={onClick}>Save</Button>)
    const button = screen.getByRole('button', { name: 'Save' })
    expect(button).toHaveAttribute('type', 'button')
    fireEvent.click(button)
    expect(onClick).toHaveBeenCalledOnce()
  })

  it('supports all visual variants and custom classes', () => {
    expect(buttonClassName()).toContain('bg-app-action')
    expect(buttonClassName('secondary')).toContain('bg-app-surface')
    expect(buttonClassName('ghost')).toContain('bg-transparent')
    expect(buttonClassName('danger', 'extra')).toContain('extra')
    render(<Button variant="danger" type="submit" disabled>Delete</Button>)
    expect(screen.getByRole('button', { name: 'Delete' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Delete' })).toHaveAttribute('type', 'submit')
  })
})
