import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import { setupPageApi, pageApiUrl, pageWrapper } from '../test/operationalTest'
import { contextStatus } from '../test/mocks/contextFixtures'
import { credentialReceipt, recoveryCheckReceipt, recoveryResetReceipt } from '../test/mocks/receiptFixtures'
import { server } from '../test/mocks/server'
import { AdminPage } from './AdminPage'
vi.mock('@tanstack/react-router', () => ({ useSearch: () => ({}), useNavigate: () => vi.fn() }))
setupPageApi()
describe('AdminPage integrated controllers', () => {
  it('gates account/recovery by permissions while keeping audit readable', async () => {
    render(<AdminPage />, { wrapper: pageWrapper({ read: true }) })
    expect(screen.queryByRole('button', { name: 'Google Accounts' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Incident Recovery' }))
    expect(await screen.findByText(/contact a system Administrator/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Check recovery' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Security Audit' }))
    expect(await screen.findByRole('heading', { name: /Security & Audit Log/ })).toBeInTheDocument()
  })
  it('submits versioned account changes and displays last-admin protection unchanged', async () => {
    const account = { id: 'last-admin', email: 'admin@example.test', role: 'admin', active: true, bound: true, version: 7, created_at: '', updated_at: '' }
    const requests: unknown[] = []
    server.use(http.get(`${pageApiUrl}/access/accounts`, () => HttpResponse.json({ items: [account], next_cursor: null })),
      http.post(`${pageApiUrl}/access/accounts/:id`, async ({ request }) => { requests.push(await request.json()); return HttpResponse.json({ detail: 'Last bound Admin must remain active', retry: 'refresh_state' }, { status: 409 }) }))
    render(<AdminPage />, { wrapper: pageWrapper({ read: true, manage_accounts: true, manage_recovery: true, manage_credentials: true }) })
    fireEvent.click(screen.getByRole('button', { name: 'Google Accounts' }))
    const role = await screen.findByRole('combobox', { name: 'Role for admin@example.test' })
    fireEvent.change(role, { target: { value: 'read' } })
    const confirmBtn = await screen.findByRole('button', { name: 'Confirm change' })
    fireEvent.click(confirmBtn)
    expect(await screen.findByRole('alert')).toHaveTextContent('Last bound Admin must remain active')
    expect(requests).toEqual([{ role: 'read', active: null, expected_version: 7 }])
    fireEvent.click(screen.getByRole('button', { name: 'Incident Recovery' }))
    expect(await screen.findByText(/No Incident Recovery Required/)).toBeInTheDocument()
  })
  it('uses slot/control versions, clears credential inputs and retries only after explicit same-key review', async () => {
    const runtime = { ...contextStatus, desired_state: 'stopped', control_version: 9, acknowledged_version: 9, operator: { state: 'stopped', label: 'Stopped', detail: '', next_action: 'monitor' } }
    const calls: { body: unknown; key: string | null }[] = []
    const slot = { connector: 'okx', configured: false, status: 'not_configured', detail: '', label: 'Spot', version: 4, changed_at: null, changed_by: null,
      observed_at: null, operation_id: null, operation_version: null, cleanup_operation_id: null, reason_code: null,
      actions: { configure: { allowed: true, detail: 'Configure', reason_code: null } } }
    server.use(http.get(`${pageApiUrl}/status`, () => HttpResponse.json(runtime)),
      http.get(`${pageApiUrl}/credentials`, () => HttpResponse.json({ source: 'postgres_kms', exchanges: [{ exchange: 'okx', environment: 'demo', cleanup_mode: 'managed', disconnect_available: true, site: null, connectors: ['okx'], fields: [
        { name: 'api_key', label: 'Public key', required: true, secret: true }, { name: 'secret_key', label: 'Signing secret', required: true, secret: true },
      ] }] })),
      http.get(`${pageApiUrl}/credentials/okx`, () => HttpResponse.json({ source: 'postgres_kms', environment: 'demo', exchange: 'okx', slots: [slot] })),
      http.get(`${pageApiUrl}/credentials/operations/:id`, ({ params }) => HttpResponse.json({ ...credentialReceipt, id: params.id, action: 'credentials_set', status: 'succeeded', error: null })),
      http.post(`${pageApiUrl}/credentials/okx/okx`, async ({ request }) => { calls.push({ body: await request.json(), key: request.headers.get('Idempotency-Key') }); return calls.length === 1 ? HttpResponse.error() : HttpResponse.json({ ...credentialReceipt, id: 'credential-ui', action: 'credentials_set', status: 'accepted' }, { status: 202 }) }))
    render(<AdminPage />, { wrapper: pageWrapper({ read: true, operate: true, manage_credentials: true }) })
    const open = await screen.findByRole('button', { name: 'Set okx credentials' })
    await waitFor(() => expect(open).toBeEnabled())
    fireEvent.click(open)
    const fill = () => {
      fireEvent.change(screen.getByLabelText('Public key'), { target: { value: 'public-ui' } })
      fireEvent.change(screen.getByLabelText('Signing secret'), { target: { value: 'secret-ui-only' } })
    }
    fill()
    fireEvent.click(screen.getByRole('button', { name: 'Submit set' }))
    await waitFor(() => expect(calls).toHaveLength(1))
    await waitFor(() => expect(screen.getByLabelText('Signing secret')).toHaveValue(''))
    expect(JSON.stringify(localStorage)).not.toContain('secret-ui-only')
    fireEvent.click(await screen.findByRole('button', { name: 'Review latest state for same-key retry' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Submit set' })).toBeEnabled())
    fill()
    fireEvent.click(screen.getByRole('button', { name: 'Submit set' }))
    await waitFor(() => expect(calls).toHaveLength(2))
    expect(calls[0].body).toEqual({ credentials: { api_key: 'public-ui', secret_key: 'secret-ui-only' }, expected_version: 4, expected_control_version: 9 })
    expect(calls[1]).toEqual(calls[0])
  })
  it('checks and confirms a real preview token without exposing it or starting trading', async () => {
    const token = 'private-preview-ui-test'
    const check = { ...recoveryCheckReceipt, id: 'check-ui', can_recover: true, blockers: [], confirmation_token: token, expires_at: new Date(Date.now() + 60000).toISOString() }
    const reset = { ...recoveryResetReceipt, id: 'reset-ui', confirmation_token: null }
    const calls: { path: string; body: unknown; key: string | null }[] = []
    server.use(http.get(`${pageApiUrl}/status`, () => HttpResponse.json({ ...contextStatus, session: null, observed_session: null, desired_state: 'stopped', control_version: 9, acknowledged_version: 9 })),
      http.get(`${pageApiUrl}/recovery`, () => HttpResponse.json({ halted: true, check_available: true, reset_available: true, blockers: [], control_version: 9, operation: null })),
      http.get(`${pageApiUrl}/recovery/operations/:id`, ({ params }) => HttpResponse.json(params.id === 'check-ui' ? check : reset)),
      http.post(`${pageApiUrl}/recovery/:action`, async ({ params, request }) => { calls.push({ path: String(params.action), body: await request.json(), key: request.headers.get('Idempotency-Key') }); return HttpResponse.json(params.action === 'check' ? { ...check, status: 'accepted' } : { ...reset, status: 'accepted' }, { status: 202 }) }))
    render(<AdminPage />, { wrapper: pageWrapper({ read: true, manage_recovery: true }) })
    fireEvent.click(screen.getByRole('button', { name: 'Incident Recovery' }))
    const checkButton = await screen.findByRole('button', { name: 'Check recovery' })
    await waitFor(() => expect(checkButton).toBeEnabled())
    fireEvent.click(checkButton)
    const confirm = await screen.findByRole('button', { name: 'Confirm Reset' })
    await waitFor(() => expect(confirm).toBeEnabled())
    expect(document.body.textContent).not.toContain(token)
    fireEvent.click(confirm)
    const dialog = await screen.findByRole('dialog')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Confirm Reset' }))
    await screen.findByText('Recovery Succeeded')
    expect(calls.map((call) => call.path)).toEqual(['check', 'reset'])
    expect(calls[1].body).toEqual({ expected_version: 9, check_id: 'check-ui', confirmation_token: token })
    expect(calls[1].key).not.toBe(calls[0].key)
  })

  it('keeps backend credential errors and secret clearing scoped to the connector that submitted', async () => {
    const slot = (connector: string) => ({ connector, configured: false, status: 'not_configured', detail: '', label: connector, version: 4, changed_at: null, changed_by: null, observed_at: null, operation_id: null, operation_version: null, cleanup_operation_id: null, reason_code: null, actions: { configure: { allowed: true, detail: 'Configure', reason_code: null } } })
    server.use(http.get(`${pageApiUrl}/status`, () => HttpResponse.json({ ...contextStatus, control_version: 9, acknowledged_version: 9, desired_state: 'stopped', operator: { state: 'stopped', label: 'Stopped', detail: '', next_action: 'monitor' } })),
      http.get(`${pageApiUrl}/credentials`, () => HttpResponse.json({ source: 'postgres_kms', exchanges: [{ exchange: 'okx', environment: 'demo', cleanup_mode: 'managed', disconnect_available: true, site: null, connectors: ['okx', 'okx_perpetual'], fields: [{ name: 'api_key', label: 'Public key', secret: true, required: true }, { name: 'secret_key', label: 'Signing secret', secret: true, required: true }] }] })),
      http.get(`${pageApiUrl}/credentials/okx`, () => HttpResponse.json({ source: 'postgres_kms', environment: 'demo', exchange: 'okx', slots: [slot('okx'), slot('okx_perpetual')] })),
      http.post(`${pageApiUrl}/credentials/okx/okx`, () => HttpResponse.json({ detail: 'Invalid connector credentials', code: 'invalid', request_id: 'request-slot-A', retry: 'fix_request', fields: [{ path: 'credentials.secret_key', code: 'invalid_credential' }] }, { status: 422 })))
    render(<AdminPage />, { wrapper: pageWrapper({ read: true, operate: true, manage_credentials: true }) })
    const buttons = await screen.findAllByRole('button', { name: 'Set okx credentials' })
    await waitFor(() => expect(buttons[0]).toBeEnabled())
    fireEvent.click(buttons[0]); fireEvent.click(buttons[1])
    const keys = screen.getAllByLabelText('Public key')
    const secrets = screen.getAllByLabelText('Signing secret')
    fireEvent.change(keys[0], { target: { value: 'key-A' } }); fireEvent.change(secrets[0], { target: { value: 'secret-A' } })
    fireEvent.change(keys[1], { target: { value: 'key-B' } }); fireEvent.change(secrets[1], { target: { value: 'secret-B' } })
    fireEvent.click(screen.getAllByRole('button', { name: 'Submit set' })[0])
    expect(await screen.findByText('invalid_credential')).toBeInTheDocument()
    expect(secrets[0]).toHaveAttribute('aria-invalid', 'true')
    expect(secrets[1]).not.toHaveAttribute('aria-invalid', 'true')
    expect(secrets[0]).toHaveValue('')
    expect(secrets[1]).toHaveValue('secret-B')
    fireEvent.click(screen.getByRole('button', { name: 'Show diagnostics details' }))
    expect(await screen.findByTitle('request-slot-A')).toBeInTheDocument()
    expect(localStorage.length).toBe(0)
  })
  it('binds the complete audit filters and blocks date-only instants without an unauthorized recovery read', async () => {
    const requests: URL[] = []
    let recoveryReads = 0
    server.use(http.get(`${pageApiUrl}/audit`, ({ request }) => { requests.push(new URL(request.url)); return HttpResponse.json({ items: [], next_cursor: null }) }),
      http.get(`${pageApiUrl}/recovery`, () => { recoveryReads++; return HttpResponse.json({}) }))
    render(<AdminPage />, { wrapper: pageWrapper({ read: true }) })
    fireEvent.click(screen.getByRole('button', { name: 'Security Audit' }))
    await waitFor(() => expect(requests).toHaveLength(1))
    fireEvent.change(screen.getByLabelText('Actor ID (actor_id)'), { target: { value: 'actor-A' } })
    fireEvent.change(screen.getByLabelText('Target ID (target_id)'), { target: { value: 'target-A' } })
    fireEvent.change(screen.getByLabelText('Connector'), { target: { value: 'okx_perpetual' } })
    fireEvent.change(screen.getByLabelText('Environment'), { target: { value: 'demo' } })
    await waitFor(() => expect(requests.some((url) => url.searchParams.get('actor_id') === 'actor-A' && url.searchParams.get('target_id') === 'target-A' && url.searchParams.get('connector') === 'okx_perpetual' && url.searchParams.get('environment') === 'demo')).toBe(true))
    const before = requests.length
    fireEvent.change(screen.getByLabelText('From time (UTC)'), { target: { value: '2026-11-10' } })
    expect(await screen.findByText('utc_instant_required')).toBeInTheDocument()
    expect(requests).toHaveLength(before)
    expect(recoveryReads).toBe(0)
  })

})
