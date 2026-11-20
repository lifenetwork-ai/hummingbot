import { transferableAbortController } from 'node:util'
import { useState, type PropsWithChildren } from 'react'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterAll, describe, expect, it, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import { ApiProvider, type ApiSession } from '../../core/api/ApiProvider'
import { AuthContext, type AuthContextValue } from '../../core/auth/auth-context'
import { server } from '../../test/mocks/server'
import { AccountsUnit } from './AccountsUnit'
import { AccountsOperationProvider } from './AccountsOperationProvider'

const controller = transferableAbortController()
vi.stubGlobal('AbortController', controller.constructor)
vi.stubGlobal('AbortSignal', controller.signal.constructor)
afterAll(() => vi.unstubAllGlobals())

const baseUrl = 'https://accounts-unit.example.test/api/v1/admin'
const session: ApiSession = { baseUrl, cacheIdentity: 'accounts-unit-test', principalId: 'admin-1', getToken: () => 'token' }
function wrapper() {
  const auth: AuthContextValue = {
    status: 'authenticated',
    identity: { id: 'admin-1', email: 'admin@example.test', kind: 'google', role: 'admin', role_label: 'Admin', permissions: { manage_accounts: true } },
    error: null, login: async () => {}, logout: () => {}, apiSession: session,
  }
  return function Wrapper({ children }: PropsWithChildren) {
    return <AuthContext.Provider value={auth}><ApiProvider session={session}><AccountsOperationProvider>{children}</AccountsOperationProvider></ApiProvider></AuthContext.Provider>
  }
}

function SwitchableAccounts() {
  const [visible, setVisible] = useState(true)
  return <>
    <button type="button" onClick={() => setVisible((value) => !value)}>Toggle Accounts</button>
    {visible && <AccountsUnit />}
  </>
}

function PermissionSwitchableAccounts() {
  const [allowed, setAllowed] = useState(true)
  const auth: AuthContextValue = {
    status: 'authenticated',
    identity: { id: 'admin-1', email: 'admin@example.test', kind: 'google', role: 'admin', role_label: 'Admin', permissions: { manage_accounts: allowed } },
    error: null, login: async () => {}, logout: () => {}, apiSession: session,
  }
  return <AuthContext.Provider value={auth}>
    <button type="button" onClick={() => setAllowed((value) => !value)}>Toggle Permission</button>
    <AccountsOperationProvider><AccountsUnit /></AccountsOperationProvider>
  </AuthContext.Provider>
}

describe('AccountsUnit lifecycle', () => {
  it('shows feedback for the latest account action only when changing from invite to account change', async () => {
    const user = userEvent.setup()
    server.use(
      http.get(`${baseUrl}/access/accounts`, () => HttpResponse.json({ items: [{ id: 'acc-1', email: 'viewer@example.test', role: 'read', active: true, bound: false, version: 1, created_at: '', updated_at: '' }], next_cursor: null })),
      http.get(`${baseUrl}/access/bootstrap`, () => HttpResponse.json({ active_google_admins: 1 })),
      http.post(`${baseUrl}/access/accounts`, () => HttpResponse.json({ code: 'invite_conflict', request_id: 'invite-request' }, { status: 409 })),
      http.post(`${baseUrl}/access/accounts/:id`, () => HttpResponse.json({ code: 'change_conflict', request_id: 'change-request' }, { status: 409 })),
    )
    render(<AccountsUnit />, { wrapper: wrapper() })
    await screen.findByRole('combobox', { name: 'Role for viewer@example.test' })
    await user.click(screen.getByRole('button', { name: 'Add Google Account' }))
    await user.type(screen.getByLabelText('Google Email'), 'new@example.test')
    await user.click(screen.getByRole('button', { name: 'Confirm Authorization' }))
    await screen.findByText('Code: invite_conflict')

    await user.selectOptions(screen.getByRole('combobox', { name: 'Role for viewer@example.test' }), 'write')
    await user.click(screen.getByRole('button', { name: 'Confirm change' }))
    await screen.findByText('Code: change_conflict')
    expect(screen.queryByText('Code: invite_conflict')).not.toBeInTheDocument()
  })

  it('shows feedback for the latest account action only when changing from account change to invite', async () => {
    const user = userEvent.setup()
    server.use(
      http.get(`${baseUrl}/access/accounts`, () => HttpResponse.json({ items: [{ id: 'acc-1', email: 'viewer@example.test', role: 'read', active: true, bound: false, version: 1, created_at: '', updated_at: '' }], next_cursor: null })),
      http.get(`${baseUrl}/access/bootstrap`, () => HttpResponse.json({ active_google_admins: 1 })),
      http.post(`${baseUrl}/access/accounts`, () => HttpResponse.json({ code: 'invite_conflict', request_id: 'invite-request' }, { status: 409 })),
      http.post(`${baseUrl}/access/accounts/:id`, () => HttpResponse.json({ code: 'change_conflict', request_id: 'change-request' }, { status: 409 })),
    )
    render(<AccountsUnit />, { wrapper: wrapper() })
    await screen.findByRole('combobox', { name: 'Role for viewer@example.test' })
    await user.selectOptions(screen.getByRole('combobox', { name: 'Role for viewer@example.test' }), 'write')
    await user.click(screen.getByRole('button', { name: 'Confirm change' }))
    await screen.findByText('Code: change_conflict')

    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel' }))
    await user.click(screen.getByRole('button', { name: 'Add Google Account' }))
    await user.type(screen.getByLabelText('Google Email'), 'new@example.test')
    await user.click(screen.getByRole('button', { name: 'Confirm Authorization' }))
    await screen.findByText('Code: invite_conflict')
    expect(screen.queryByText('Code: change_conflict')).not.toBeInTheDocument()
  })

  it('keeps an in-flight mutation owner alive when the view unmounts and remounts', async () => {
    const user = userEvent.setup()
    let finishInvite!: () => void
    let writeCount = 0
    server.use(
      http.get(`${baseUrl}/access/accounts`, () => HttpResponse.json({ items: [], next_cursor: null })),
      http.get(`${baseUrl}/access/bootstrap`, () => HttpResponse.json({ active_google_admins: 1, basic_auth_enabled: false, detail: 'ready', google_admin_ready: true, google_auth_enabled: true })),
      http.post(`${baseUrl}/access/accounts`, () => {
        writeCount++
        return new Promise((resolve) => {
          finishInvite = () => resolve(HttpResponse.json({ id: 'invite-1', email: 'new@example.test', role: 'read', active: true, bound: false, version: 1, created_at: '', updated_at: '' }, { status: 201 }))
        })
      }),
    )
    render(<SwitchableAccounts />, { wrapper: wrapper() })
    await user.click(screen.getByRole('button', { name: 'Add Google Account' }))
    await user.type(screen.getByLabelText('Google Email'), 'new@example.test')
    await user.click(screen.getByRole('button', { name: 'Confirm Authorization' }))
    await waitFor(() => expect(writeCount).toBe(1))

    await user.click(screen.getByRole('button', { name: 'Toggle Accounts' }))
    expect(screen.queryByLabelText('Google Email')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Toggle Accounts' }))
    await user.click(screen.getByRole('button', { name: 'Add Google Account' }))
    expect(screen.getByRole('button', { name: 'Adding...' })).toBeDisabled()
    expect(screen.getByLabelText('Google Email')).toHaveValue('')
    expect(writeCount).toBe(1)

    finishInvite()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Confirm Authorization' })).toBeEnabled())
  })

  it('drops a stale mutation result when account permission changes during the request', async () => {
    const user = userEvent.setup()
    let finishInvite!: () => void
    server.use(
      http.get(`${baseUrl}/access/accounts`, () => HttpResponse.json({ items: [], next_cursor: null })),
      http.get(`${baseUrl}/access/bootstrap`, () => HttpResponse.json({ active_google_admins: 1 })),
      http.post(`${baseUrl}/access/accounts`, () => new Promise((resolve) => {
        finishInvite = () => resolve(HttpResponse.json({ error: 'last_active_admin_required', code: 'last_active_admin_required', request_id: 'request-1' }, { status: 409 }))
      })),
    )
    render(<PermissionSwitchableAccounts />, { wrapper: ({ children }) => <ApiProvider session={session}>{children}</ApiProvider> })
    await user.click(screen.getByRole('button', { name: 'Add Google Account' }))
    await user.type(screen.getByLabelText('Google Email'), 'new@example.test')
    await user.click(screen.getByRole('button', { name: 'Confirm Authorization' }))
    await waitFor(() => expect(finishInvite).toBeTypeOf('function'))

    await user.click(screen.getByRole('button', { name: 'Toggle Permission' }))
    expect(screen.getByRole('status')).toHaveTextContent('requires manage_accounts permission')
    finishInvite()
    await user.click(screen.getByRole('button', { name: 'Toggle Permission' }))
    await waitFor(() => expect(screen.getByText('Google Accounts Management')).toBeInTheDocument())
    expect(screen.queryByText(/last_active_admin_required/)).not.toBeInTheDocument()
  })
})
