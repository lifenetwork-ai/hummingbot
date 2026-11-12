import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { AccountLockConfirmationDialog } from './AccountLockConfirmationDialog'
import type { components } from '../../core/api/schema'

describe('AccountLockConfirmationDialog', () => {
  const adminAccount: components['schemas']['AccountResponseOutput'] = {
    id: 'acc-admin',
    email: 'admin@example.test',
    role: 'admin',
    active: true,
    bound: true,
    version: 1,
    created_at: '2026-11-10T00:00:00Z',
    updated_at: '2026-11-10T00:00:00Z',
  }

  const lockedViewerAccount: components['schemas']['AccountResponseOutput'] = {
    id: 'acc-viewer',
    email: 'viewer@example.test',
    role: 'read',
    active: false,
    bound: true,
    version: 2,
    created_at: '2026-11-10T00:00:00Z',
    updated_at: '2026-11-10T00:00:00Z',
  }

  it('renders lock warning when suspending an admin account', () => {
    render(
      <AccountLockConfirmationDialog
        open={true}
        onOpenChange={vi.fn()}
        account={adminAccount}
        targetActive={false}
        onConfirm={vi.fn()}
      />,
    )

    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(screen.getByText('Confirm Suspending Administrator (Admin)')).toBeInTheDocument()
    expect(screen.getByText('Warning: Suspending Administrator')).toBeInTheDocument()
    expect(screen.getByText(/Last Bound Admin/)).toBeInTheDocument()
    expect(screen.getByText('admin@example.test')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Confirm suspend' })).toBeInTheDocument()
  })

  it('renders unlock notice when reactivating an account', () => {
    render(
      <AccountLockConfirmationDialog
        open={true}
        onOpenChange={vi.fn()}
        account={lockedViewerAccount}
        targetActive={true}
        onConfirm={vi.fn()}
      />,
    )

    expect(screen.getByText('Confirm Reactivating Account')).toBeInTheDocument()
    expect(screen.getByText('Reactivate account')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Confirm reactivate' })).toBeInTheDocument()
  })

  it('calls onConfirm when confirm button is clicked', () => {
    const onConfirm = vi.fn()
    render(
      <AccountLockConfirmationDialog
        open={true}
        onOpenChange={vi.fn()}
        account={adminAccount}
        targetActive={false}
        onConfirm={onConfirm}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Confirm suspend' }))
    expect(onConfirm).toHaveBeenCalledTimes(1)
  })

  it('calls onOpenChange(false) when cancel button is clicked', () => {
    const onOpenChange = vi.fn()
    render(
      <AccountLockConfirmationDialog
        open={true}
        onOpenChange={onOpenChange}
        account={adminAccount}
        targetActive={false}
        onConfirm={vi.fn()}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it('disables buttons and shows spinner while submitting', () => {
    render(
      <AccountLockConfirmationDialog
        open={true}
        onOpenChange={vi.fn()}
        account={adminAccount}
        targetActive={false}
        onConfirm={vi.fn()}
        isSubmitting={true}
      />,
    )

    expect(screen.getByText('Suspending...')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled()
    expect(screen.getByRole('button', { name: /Suspending/ })).toBeDisabled()
  })

  it('renders error message when error prop is provided', () => {
    render(
      <AccountLockConfirmationDialog
        open={true}
        onOpenChange={vi.fn()}
        account={adminAccount}
        targetActive={false}
        onConfirm={vi.fn()}
        error="Last bound Admin must remain active"
      />,
    )

    expect(screen.getByText(/Last bound Admin must remain active/)).toBeInTheDocument()
  })
})
