import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { RecoveryResetDialog } from './RecoveryResetDialog'
import type { components } from '../../core/api/schema'

describe('RecoveryResetDialog', () => {
  const mockScopedOrders: components['schemas']['RecoveryUncertainOrderRepairOutput'][] = [
    {
      id: 'Spot001',
      pair: 'LIFE-USDT',
      side: 'BUY',
      amount: '12.5',
      price: '1.20',
      before: 'PendingBuy',
      after: 'OrderFailure',
      connector: 'okx',
      deadline_ms: 1762300000000,
      evidence: 'expired_submission_and_reconciled_exchange',
    },
    {
      id: 'Spot002',
      pair: 'LIFE-USDT',
      side: 'SELL',
      amount: '8.0',
      price: '1.30',
      before: 'PendingSell',
      after: 'OrderFailure',
      connector: 'okx',
      deadline_ms: 1762300000000,
      evidence: 'expired_submission_and_reconciled_exchange',
    },
  ]

  it('renders generic reset dialog without checkbox and submits with undefined options', () => {
    const onConfirmReset = vi.fn()
    const onOpenChange = vi.fn()
    render(
      <RecoveryResetDialog
        open={true}
        onOpenChange={onOpenChange}
        onConfirmReset={onConfirmReset}
        scopedOrders={[]}
      />,
    )

    expect(screen.getByText('Confirm Recovery Reset')).toBeInTheDocument()
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()

    const confirmBtn = screen.getByRole('button', { name: 'Confirm Reset' })
    expect(confirmBtn).not.toBeDisabled()
    fireEvent.click(confirmBtn)
    expect(onConfirmReset).toHaveBeenCalledWith(undefined)
  })

  it('renders scoped reset dialog with order summary and requires explicit confirmation', () => {
    const onConfirmReset = vi.fn()
    const onOpenChange = vi.fn()
    render(
      <RecoveryResetDialog
        open={true}
        onOpenChange={onOpenChange}
        onConfirmReset={onConfirmReset}
        scopedOrders={mockScopedOrders}
      />,
    )

    expect(screen.getByText('Confirm Uncertain Order Recovery Reset')).toBeInTheDocument()
    expect(screen.getByText('Spot001')).toBeInTheDocument()
    expect(screen.getByText('Spot002')).toBeInTheDocument()
    expect(screen.getByText(/Orders to be repaired \(2\):/)).toBeInTheDocument()

    const confirmBtn = screen.getByRole('button', { name: 'Confirm Reset' })
    expect(confirmBtn).toBeDisabled()

    const checkbox = screen.getByRole('checkbox')
    fireEvent.click(checkbox)
    expect(confirmBtn).not.toBeDisabled()

    fireEvent.click(confirmBtn)
    expect(onConfirmReset).toHaveBeenCalledWith({ confirmOrderChanges: true })
  })

  it('disables Confirm Reset when preview is expired', () => {
    render(
      <RecoveryResetDialog
        open={true}
        onOpenChange={vi.fn()}
        onConfirmReset={vi.fn()}
        scopedOrders={mockScopedOrders}
        previewValid={false}
      />,
    )

    expect(screen.getByText(/Preview expired:/)).toBeInTheDocument()
    const confirmBtn = screen.getByRole('button', { name: 'Confirm Reset' })
    expect(confirmBtn).toBeDisabled()
  })

  it('canceling dialog invokes onOpenChange(false) and does not call confirm', () => {
    const onConfirmReset = vi.fn()
    const onOpenChange = vi.fn()
    render(
      <RecoveryResetDialog
        open={true}
        onOpenChange={onOpenChange}
        onConfirmReset={onConfirmReset}
        scopedOrders={mockScopedOrders}
      />,
    )

    const cancelBtn = screen.getByRole('button', { name: 'Cancel' })
    fireEvent.click(cancelBtn)
    expect(onOpenChange).toHaveBeenCalledWith(false)
    expect(onConfirmReset).not.toHaveBeenCalled()
  })

  it('resets confirmation checkbox when re-opening or when scoped orders batch changes', () => {
    const { rerender } = render(
      <RecoveryResetDialog
        open={true}
        onOpenChange={vi.fn()}
        onConfirmReset={vi.fn()}
        scopedOrders={mockScopedOrders}
      />,
    )

    const checkbox = screen.getByRole('checkbox') as HTMLInputElement
    expect(checkbox.checked).toBe(false)
    fireEvent.click(checkbox)
    expect(checkbox.checked).toBe(true)

    // Simulate dialog closing after successful reset of batch A
    rerender(
      <RecoveryResetDialog
        open={false}
        onOpenChange={vi.fn()}
        onConfirmReset={vi.fn()}
        scopedOrders={mockScopedOrders}
      />,
    )

    // Now review B opens with a different batch
    const batchB: components['schemas']['RecoveryUncertainOrderRepairOutput'][] = [
      {
        id: 'Spot003',
        pair: 'BTC-USDT',
        side: 'BUY',
        amount: '0.1',
        price: '65000.0',
        before: 'PendingBuy',
        after: 'OrderFailure',
        connector: 'okx',
        deadline_ms: 1762300000000,
        evidence: 'expired_submission_and_reconciled_exchange',
      },
    ]

    rerender(
      <RecoveryResetDialog
        open={true}
        onOpenChange={vi.fn()}
        onConfirmReset={vi.fn()}
        scopedOrders={batchB}
      />,
    )

    const checkboxB = screen.getByRole('checkbox') as HTMLInputElement
    expect(checkboxB.checked).toBe(false)
    const confirmBtn = screen.getByRole('button', { name: 'Confirm Reset' })
    expect(confirmBtn).toBeDisabled()
  })

  it('renders error message directly inside the dialog when error prop is provided', () => {
    render(
      <RecoveryResetDialog
        open={true}
        onOpenChange={vi.fn()}
        onConfirmReset={vi.fn()}
        scopedOrders={mockScopedOrders}
        error="Server rejected recovery reset token: conflict"
      />,
    )

    expect(screen.getByText('Error applying Reset:')).toBeInTheDocument()
    expect(screen.getByText(/Server rejected recovery reset token: conflict/)).toBeInTheDocument()
  })
})
