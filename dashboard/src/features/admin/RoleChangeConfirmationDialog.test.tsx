import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { RoleChangeConfirmationDialog } from './RoleChangeConfirmationDialog'
import type { components } from '../../core/api/schema'

describe('RoleChangeConfirmationDialog', () => {
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

  const viewerAccount: components['schemas']['AccountResponseOutput'] = {
    id: 'acc-viewer',
    email: 'viewer@example.test',
    role: 'read',
    active: true,
    bound: true,
    version: 1,
    created_at: '2026-11-10T00:00:00Z',
    updated_at: '2026-11-10T00:00:00Z',
  }

  it('renders warning when demoting an admin', () => {
    render(
      <RoleChangeConfirmationDialog
        open={true}
        onOpenChange={vi.fn()}
        account={adminAccount}
        targetRole="write"
        onConfirm={vi.fn()}
      />,
    )

    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(screen.getByText('Confirm Changing Administrator Role (Admin)')).toBeInTheDocument()
    expect(screen.getByText('Warning: Administrator Demotion')).toBeInTheDocument()
    expect(screen.getByText(/Last Bound Admin/)).toBeInTheDocument()
    expect(screen.getByText('admin@example.test')).toBeInTheDocument()
    expect(screen.getByText('Administrator (Admin)')).toBeInTheDocument()
    expect(screen.getByText('Operator')).toBeInTheDocument()
  })

  it('renders notice when promoting to admin', () => {
    render(
      <RoleChangeConfirmationDialog
        open={true}
        onOpenChange={vi.fn()}
        account={viewerAccount}
        targetRole="admin"
        onConfirm={vi.fn()}
      />,
    )

    expect(screen.getByText('Confirm Role Change')).toBeInTheDocument()
    expect(screen.getByText('Grant Full Administrator Privileges (Admin)')).toBeInTheDocument()
    expect(screen.queryByText('Warning: Administrator Demotion')).not.toBeInTheDocument()
  })

  it('calls onConfirm when confirm button is clicked', () => {
    const onConfirm = vi.fn()
    render(
      <RoleChangeConfirmationDialog
        open={true}
        onOpenChange={vi.fn()}
        account={adminAccount}
        targetRole="read"
        onConfirm={onConfirm}
      />,
    )

    const confirmBtn = screen.getByRole('button', { name: 'Confirm change' })
    fireEvent.click(confirmBtn)
    expect(onConfirm).toHaveBeenCalledTimes(1)
  })

  it('calls onOpenChange(false) when cancel button is clicked', () => {
    const onOpenChange = vi.fn()
    render(
      <RoleChangeConfirmationDialog
        open={true}
        onOpenChange={onOpenChange}
        account={adminAccount}
        targetRole="read"
        onConfirm={vi.fn()}
      />,
    )

    const cancelBtn = screen.getByRole('button', { name: 'Cancel' })
    fireEvent.click(cancelBtn)
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it('disables buttons and shows spinner while submitting', () => {
    render(
      <RoleChangeConfirmationDialog
        open={true}
        onOpenChange={vi.fn()}
        account={adminAccount}
        targetRole="read"
        onConfirm={vi.fn()}
        isSubmitting={true}
      />,
    )

    expect(screen.getByText('Updating role...')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled()
    expect(screen.getByRole('button', { name: /Updating role/ })).toBeDisabled()
  })

  it('renders error message when error prop is provided', () => {
    render(
      <RoleChangeConfirmationDialog
        open={true}
        onOpenChange={vi.fn()}
        account={adminAccount}
        targetRole="read"
        onConfirm={vi.fn()}
        error="Last bound Admin must remain active"
      />,
    )

    expect(screen.getByText(/Last bound Admin must remain active/)).toBeInTheDocument()
  })
})
