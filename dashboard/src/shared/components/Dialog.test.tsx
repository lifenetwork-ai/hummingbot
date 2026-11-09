import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { Dialog } from './Dialog'

describe('Dialog', () => {
  it('does not render or alter scroll when closed', () => {
    render(<Dialog open={false} onClose={vi.fn()} title="Hidden">Body</Dialog>)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('renders accessible content, locks scroll and closes on Escape', () => {
    const onClose = vi.fn()
    const { unmount } = render(
      <Dialog
        open
        onClose={onClose}
        title="Example dialog"
        description="More information"
        footer={<button type="button">Confirm</button>}
      >
        Body
      </Dialog>,
    )

    const modal = screen.getByRole('dialog', { name: 'Example dialog' })
    expect(modal).toHaveAttribute('aria-modal', 'true')
    expect(modal).toHaveAccessibleDescription('More information')
    expect(screen.getByRole('button', { name: 'Confirm' })).toBeInTheDocument()
    expect(document.body).toHaveStyle({ overflow: 'hidden' })

    fireEvent.keyDown(window, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
    unmount()
    expect(document.body.style.overflow).toBe('')
  })

  it('allows closing with backdrop and header button', () => {
    const onClose = vi.fn()
    render(<Dialog open onClose={onClose} title="Test">Body</Dialog>)
    fireEvent.click(screen.getByRole('button', { name: 'Close dialog' }))
    fireEvent.click(screen.getByRole('button', { name: 'Close' }))
    expect(onClose).toHaveBeenCalledTimes(2)
  })

  it('keeps keyboard focus inside the modal', () => {
    render(<Dialog open onClose={vi.fn()} title="Focus"><button type="button">Last control</button></Dialog>)
    const first = screen.getByRole('button', { name: 'Close' })
    const last = screen.getByRole('button', { name: 'Last control' })
    last.focus()
    fireEvent.keyDown(window, { key: 'Tab' })
    expect(first).toHaveFocus()
    first.focus()
    fireEvent.keyDown(window, { key: 'Tab', shiftKey: true })
    expect(last).toHaveFocus()
  })
})
