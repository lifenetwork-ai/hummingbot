import { render, screen, fireEvent } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { AccountsManagementView } from './AccountsManagementView'
import type { FormFeedbackModel } from '../../core/api/operation-feedback'

describe('AccountsManagementView', () => {
  const sampleAccounts = [
    {
      id: 'acc-1',
      email: 'admin@example.test',
      role: 'admin' as const,
      active: true,
      bound: true,
      created_at: '2026-11-10T00:00:00Z',
      updated_at: '2026-11-10T00:00:00Z',
      version: 1,
    },
    {
      id: 'acc-2',
      email: 'viewer@example.test',
      role: 'read' as const,
      active: true,
      bound: false,
      created_at: '2026-11-10T00:00:00Z',
      updated_at: '2026-11-10T00:00:00Z',
      version: 1,
    },
  ]

  it('renders accounts list and last bound admin protection notice', () => {
    render(
      <AccountsManagementView
        accounts={sampleAccounts}
        bootstrap={{ active_google_admins: 1, basic_auth_enabled: true, detail: '', google_admin_ready: true, google_auth_enabled: true }}
      />,
    )

    expect(screen.getByText('admin@example.test')).toBeInTheDocument()
    expect(screen.getByText('viewer@example.test')).toBeInTheDocument()
    expect(screen.getByText(/Last Bound Admin Protection/)).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'Created At' })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'Updated At' })).toBeInTheDocument()
  })

  it('renders role column as select dropdown without badge label', () => {
    render(
      <AccountsManagementView
        accounts={sampleAccounts}
        onChangeAccount={vi.fn()}
      />,
    )

    const adminSelect = screen.getByRole('combobox', { name: 'Role for admin@example.test' })
    expect(adminSelect).toBeInTheDocument()
    expect(adminSelect).toHaveValue('admin')

    const viewerSelect = screen.getByRole('combobox', { name: 'Role for viewer@example.test' })
    expect(viewerSelect).toBeInTheDocument()
    expect(viewerSelect).toHaveValue('read')

    // Ensure there are no badges containing raw role text in the role cells
    const cell = adminSelect.closest('td')
    expect(cell?.querySelector('.rounded-pill')).toBeNull()
  })

  it('opens confirmation popup when changing role and allows cancellation', () => {
    const onChangeAccount = vi.fn()
    render(
      <AccountsManagementView
        accounts={sampleAccounts}
        onChangeAccount={onChangeAccount}
      />,
    )

    const adminSelect = screen.getByRole('combobox', { name: 'Role for admin@example.test' })
    fireEvent.change(adminSelect, { target: { value: 'write' } })

    // Confirmation dialog opens
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(screen.getByText('Confirm Changing Administrator Role (Admin)')).toBeInTheDocument()
    expect(screen.getByText('Warning: Administrator Demotion')).toBeInTheDocument()

    // Click cancel
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))

    // Dialog is closed and onChangeAccount was not called
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(onChangeAccount).not.toHaveBeenCalled()
  })

  it('submits change when confirmed in popup', async () => {
    const onChangeAccount = vi.fn().mockResolvedValue({})
    render(
      <AccountsManagementView
        accounts={sampleAccounts}
        onChangeAccount={onChangeAccount}
      />,
    )

    const adminSelect = screen.getByRole('combobox', { name: 'Role for admin@example.test' })
    fireEvent.change(adminSelect, { target: { value: 'read' } })

    expect(screen.getByRole('dialog')).toBeInTheDocument()
    const confirmBtn = screen.getByRole('button', { name: 'Confirm change' })
    fireEvent.click(confirmBtn)

    expect(onChangeAccount).toHaveBeenCalledWith('acc-1', {
      role: 'read',
      active: null,
      expected_version: 1,
    })
  })

  it('renders temporary lock in a separate column and toggles active status with confirmation popup', () => {
    const onChangeAccount = vi.fn().mockResolvedValue({})
    render(
      <AccountsManagementView
        accounts={sampleAccounts}
        onChangeAccount={onChangeAccount}
      />,
    )

    // Table header has separate column for Suspend
    expect(screen.getByRole('columnheader', { name: 'Suspend' })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'Status' })).toBeInTheDocument()

    // Status column contains the badge
    expect(screen.getByText(/Active · Bound to Google/)).toBeInTheDocument()

    // Separate column has Suspend button
    const lockButtons = screen.getAllByRole('button', { name: 'Suspend' })
    expect(lockButtons).toHaveLength(2)

    // Clicking Suspend opens confirmation dialog
    fireEvent.click(lockButtons[0])
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(screen.getByText('Confirm Suspending Administrator (Admin)')).toBeInTheDocument()
    expect(screen.getByText('Warning: Suspending Administrator')).toBeInTheDocument()

    // Cancel doesn't submit
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(onChangeAccount).not.toHaveBeenCalled()

    // Click again and confirm
    fireEvent.click(lockButtons[0])
    const confirmBtn = screen.getByRole('button', { name: 'Confirm suspend' })
    fireEvent.click(confirmBtn)
    expect(onChangeAccount).toHaveBeenCalledWith('acc-1', {
      role: null,
      active: false,
      expected_version: 1,
    })
  })

  it('renders inline error on email input from feedbackModel', () => {
    const feedbackModel: FormFeedbackModel = {
      feedback: {
        surface: 'inline',
        code: 'account_conflict',
        requestId: 'req-acc-555',
        fields: [],
        retry: 'fix_request',
        retryAt: null,
        outcomeUnknown: false,
      },
      inlineErrors: {
        email: ['email_already_exists'],
      },
      globalFields: [],
    }

    render(
      <AccountsManagementView
        accounts={sampleAccounts}
        feedbackModel={feedbackModel}
      />,
    )

    // Open add account form
    const addBtn = screen.getByRole('button', { name: /Add Google Account/ })
    fireEvent.click(addBtn)

    expect(screen.getByText('email_already_exists')).toBeInTheDocument()
    expect(screen.getByText('Code: account_conflict')).toBeInTheDocument()
  })
})
