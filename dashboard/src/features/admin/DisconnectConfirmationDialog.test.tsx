import { render, screen, fireEvent } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { DisconnectConfirmationDialog } from './DisconnectConfirmationDialog'

const baseProps = () => ({
  open: true,
  onOpenChange: vi.fn(),
  connector: 'okx',
  onConfirmDisconnect: vi.fn(),
})

describe('DisconnectConfirmationDialog', () => {
  it('states the full bot-wide impact before confirming', () => {
    render(<DisconnectConfirmationDialog {...baseProps()} />)

    // Stops the whole bot, not just one market
    expect(screen.getByText(/Stops the whole bot/i)).toBeInTheDocument()
    // Cancel this bot's pending orders (description and warning bullet)
    expect(screen.getAllByText(/Cancel this bot's pending orders/i).length).toBeGreaterThan(0)
    // Managed perpetual positions closed at market with fees/slippage disclosure
    expect(screen.getByText(/close its managed perpetual positions using Market orders/i)).toBeInTheDocument()
    expect(screen.getByText(/fees and slippage/i)).toBeInTheDocument()
    // Spot holdings are not sold
    expect(screen.getByText(/Spot holdings will not be sold/i)).toBeInTheDocument()
    // Only the selected key is removed from this bot
    expect(screen.getByText(/Only the selected key is removed from this bot/i)).toBeInTheDocument()
    // The exchange API key stays valid and must be deleted separately
    expect(screen.getByText(/API key on the exchange remains valid/i)).toBeInTheDocument()
    expect(screen.getByText(/delete it separately/i)).toBeInTheDocument()
  })

  it('keeps the connection when cancelled and calls confirm when confirmed', () => {
    const props = baseProps()
    render(<DisconnectConfirmationDialog {...props} />)

    fireEvent.click(screen.getByRole('button', { name: /Cancel \(Keep connection\)/ }))
    expect(props.onOpenChange).toHaveBeenCalledWith(false)
    expect(props.onConfirmDisconnect).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: /Confirm close positions & disconnect/ }))
    expect(props.onConfirmDisconnect).toHaveBeenCalledTimes(1)
  })

  it('disables actions and shows progress while submitting', () => {
    render(<DisconnectConfirmationDialog {...baseProps()} isSubmitting={true} />)

    const cancel = screen.getByRole('button', { name: /Cancel \(Keep connection\)/ })
    const confirm = screen.getByRole('button', { name: /Closing positions & disconnecting/i })
    expect(cancel).toBeDisabled()
    expect(confirm).toBeDisabled()
  })

  it('surfaces disconnect errors without closing', () => {
    const props = baseProps()
    render(<DisconnectConfirmationDialog {...props} error="bot_must_be_stopped: cleanup incomplete" />)

    expect(screen.getByText(/bot_must_be_stopped: cleanup incomplete/)).toBeInTheDocument()
    expect(props.onOpenChange).not.toHaveBeenCalled()
  })

  it('renders nothing when closed', () => {
    render(<DisconnectConfirmationDialog {...baseProps()} open={false} />)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('shows the lock reason and keeps confirm disabled while disconnect is locked', () => {
    const props = baseProps()
    render(<DisconnectConfirmationDialog {...props} lockReason="Bot must be stopped and acknowledged by the backend." />)

    expect(screen.getByText(/Disconnect is currently locked/i)).toBeInTheDocument()
    expect(screen.getByText(/Bot must be stopped and acknowledged by the backend\./)).toBeInTheDocument()

    const confirm = screen.getByRole('button', { name: /Confirm close positions & disconnect/ })
    expect(confirm).toBeDisabled()
    fireEvent.click(confirm)
    expect(props.onConfirmDisconnect).not.toHaveBeenCalled()

    // Cancel remains available so the operator is never trapped in a locked flow
    expect(screen.getByRole('button', { name: /Cancel \(Keep connection\)/ })).not.toBeDisabled()
  })
})
