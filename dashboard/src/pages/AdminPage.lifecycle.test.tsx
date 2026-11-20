import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { pageApiUrl, pageWrapper, setupPageApi } from '../test/operationalTest'
import { server } from '../test/mocks/server'
import { AdminPage } from './AdminPage'
import { useOperationalContext } from '../features/operational-context/useOperationalContext'
import { contextStatus } from '../test/mocks/contextFixtures'

const route = vi.hoisted(() => ({ search: {} as { tab?: string } }))
vi.mock('@tanstack/react-router', () => ({ useSearch: () => route.search, useNavigate: () => vi.fn() }))
setupPageApi()
beforeEach(() => { route.search = {} })

function observeReads() {
  const reads = { credentials: 0, recovery: 0, accounts: 0, bootstrap: 0, audit: 0 }
  server.use(
    http.get(`${pageApiUrl}/credentials`, () => { reads.credentials++; return HttpResponse.json({ source: 'environment', exchanges: [] }) }),
    http.get(`${pageApiUrl}/recovery`, () => { reads.recovery++; return HttpResponse.json({ halted: false, check_available: false, reset_available: false, blockers: [], operation: null, control_version: 2 }) }),
    http.get(`${pageApiUrl}/access/accounts`, () => { reads.accounts++; return HttpResponse.json({ items: [], next_cursor: null }) }),
    http.get(`${pageApiUrl}/access/bootstrap`, () => { reads.bootstrap++; return HttpResponse.json({ google_auth_enabled: true, active_google_admins: 1 }) }),
    http.get(`${pageApiUrl}/audit`, () => { reads.audit++; return HttpResponse.json({ items: [], next_cursor: null }) }),
  )
  return reads
}

const permissions = { read: true, operate: true, manage_credentials: true, manage_recovery: true, manage_accounts: true }

function OtherRoute() {
  const context = useOperationalContext()
  return <p>Another protected route: {context.status.phase}</p>
}

describe('Admin feature query activation', () => {
  it('loads Connections without starting Recovery, Accounts, Bootstrap or Audit', async () => {
    const reads = observeReads()
    render(<AdminPage />, { wrapper: pageWrapper(permissions) })
    await waitFor(() => expect(reads.credentials).toBeGreaterThan(0))
    expect(reads).toEqual({ credentials: 1, recovery: 0, accounts: 0, bootstrap: 0, audit: 0 })
    fireEvent.click(screen.getByRole('button', { name: 'Incident Recovery' }))
    await waitFor(() => expect(reads.recovery).toBeGreaterThan(0))
    expect(reads.accounts).toBe(0)
    expect(reads.bootstrap).toBe(0)
    expect(reads.audit).toBe(0)
  })

  it('opens the Accounts deep link without loading the other feature controllers', async () => {
    route.search = { tab: 'accounts' }
    const reads = observeReads()
    render(<AdminPage />, { wrapper: pageWrapper(permissions) })
    await waitFor(() => expect(reads.accounts).toBeGreaterThan(0))
    await waitFor(() => expect(reads.bootstrap).toBeGreaterThan(0))
    expect(reads.credentials).toBe(0)
    expect(reads.recovery).toBe(0)
    expect(reads.audit).toBe(0)
  })

  it('keeps a denied Accounts deep link from starting feature reads or mutations', async () => {
    route.search = { tab: 'accounts' }
    const reads = observeReads()
    let writes = 0
    server.use(
      http.post(`${pageApiUrl}/access/accounts`, () => { writes++; return HttpResponse.json({}, { status: 201 }) }),
      http.post(`${pageApiUrl}/access/accounts/:id`, () => { writes++; return HttpResponse.json({}, { status: 200 }) }),
    )
    render(<AdminPage />, { wrapper: pageWrapper({ read: true }) })
    expect(await screen.findByText(/Account administration requires manage_accounts permission/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Google Accounts' })).not.toBeInTheDocument()
    expect(reads).toEqual({ credentials: 0, recovery: 0, accounts: 0, bootstrap: 0, audit: 0 })
    expect(writes).toBe(0)
  })

  it('keeps an Accounts read failure isolated while another feature remains usable', async () => {
    let accountReads = 0
    let bootstrapReads = 0
    let connectionReads = 0
    server.use(
      http.get(`${pageApiUrl}/access/accounts`, () => {
        accountReads++
        return HttpResponse.json({ detail: 'Accounts unavailable' }, { status: 403 })
      }),
      http.get(`${pageApiUrl}/access/bootstrap`, () => {
        bootstrapReads++
        return HttpResponse.json({ google_auth_enabled: true, active_google_admins: 1 })
      }),
      http.get(`${pageApiUrl}/credentials`, () => {
        connectionReads++
        return HttpResponse.json({ source: 'environment', exchanges: [] })
      }),
    )
    route.search = { tab: 'accounts' }
    render(<AdminPage />, { wrapper: pageWrapper(permissions) })
    expect(await screen.findByRole('alert')).toHaveTextContent('Accounts unavailable')
    expect(accountReads).toBe(1)
    expect(bootstrapReads).toBeGreaterThan(0)
    fireEvent.click(screen.getByRole('button', { name: 'Exchange Connections' }))
    await waitFor(() => expect(connectionReads).toBeGreaterThan(0))
    expect(screen.queryByText('Accounts unavailable')).not.toBeInTheDocument()
  })

  it('keeps a timed out Connections request retryable across tab switches when storage is unavailable', async () => {
    const attempts: Array<{ body: unknown; key: string | null }> = []
    const setItem = vi.spyOn(window.localStorage, 'setItem').mockImplementation(() => { throw new Error('storage unavailable') })
    const slot = {
      connector: 'okx', configured: false, status: 'not_configured', detail: '', label: 'Spot', version: 4,
      changed_at: null, changed_by: null, observed_at: null, operation_id: null, operation_version: null,
      cleanup_operation_id: null, reason_code: null,
      actions: { configure: { allowed: true, detail: 'Configure', reason_code: null } },
    }
    server.use(
      http.get(`${pageApiUrl}/status`, () => HttpResponse.json({ ...contextStatus, control_version: 9, acknowledged_version: 9,
        desired_state: 'stopped', operator: { state: 'stopped', label: 'Stopped', detail: '', next_action: 'monitor' } })),
      http.get(`${pageApiUrl}/credentials`, () => HttpResponse.json({ source: 'postgres_kms', exchanges: [{ exchange: 'okx', environment: 'demo',
        cleanup_mode: 'managed', disconnect_available: true, site: null, connectors: ['okx'], fields: [
          { name: 'api_key', label: 'Public key', required: true, secret: true },
          { name: 'secret_key', label: 'Signing secret', required: true, secret: true },
        ] }] })),
      http.get(`${pageApiUrl}/credentials/okx`, () => HttpResponse.json({ source: 'postgres_kms', environment: 'demo', exchange: 'okx', slots: [slot] })),
      http.post(`${pageApiUrl}/credentials/okx/okx`, async ({ request }) => {
        attempts.push({ body: await request.json(), key: request.headers.get('Idempotency-Key') })
        return attempts.length === 1 ? HttpResponse.json({ detail: 'upstream timed out', retry: 'same_request' }, { status: 503 })
          : HttpResponse.json({ id: 'credential-route-retry', status: 'accepted', action: 'credentials_set' }, { status: 202 })
      }),
      http.get(`${pageApiUrl}/credentials/operations/credential-route-retry`, () => HttpResponse.json({ id: 'credential-route-retry', status: 'applying', action: 'credentials_set',
        created_at: '', updated_at: '', effective_version: 4, expected_version: 4, error: null, session_id: null,
        payload: { action: 'credentials_set', actor_id: 'admin-1', actor_kind: 'user', bot_id: 'bot-a', cleanup_operation_id: null,
          connector: 'okx', environment: 'demo', exchange: 'okx', expected_control_version: 9, expected_operation_version: null, expected_version: 4 },
        result: { stage: 'loading', blocked_stage: null, reason_code: null, next_retry_at: null, cleanup_operation_id: null,
          credential_version: 4, operation_version: 1, operator_action_required: false, orders_terminal_verified: null,
          positions_flat_verified: null, ledger_confirmed: null, consumers_detached: null, account_verified: null,
          position_checkpoints_verified: null, owner_generation: null, owner_uuid: null, observed_at: null,
          remaining_order_count: 0, remaining_position_count: 0 } })),
    )
    try {
      render(<AdminPage />, { wrapper: pageWrapper(permissions) })
      fireEvent.click(await screen.findByRole('button', { name: 'Set okx credentials' }))
      fireEvent.change(screen.getByLabelText('Public key'), { target: { value: 'public-route-key' } })
      fireEvent.change(screen.getByLabelText('Signing secret'), { target: { value: 'private-route-secret' } })
      fireEvent.click(screen.getByRole('button', { name: 'Submit set' }))
      await waitFor(() => expect(attempts).toHaveLength(1))
      await screen.findByRole('button', { name: 'Review latest state for same-key retry' })
      expect(setItem).not.toHaveBeenCalled()

      fireEvent.click(screen.getByRole('button', { name: 'Incident Recovery' }))
      await screen.findByRole('button', { name: 'Check recovery' })
      expect(attempts).toHaveLength(1)
      fireEvent.click(screen.getByRole('button', { name: 'Exchange Connections' }))
      fireEvent.click(await screen.findByRole('button', { name: 'Review latest state for same-key retry' }))
      fireEvent.change(await screen.findByLabelText('Public key'), { target: { value: 'public-route-key' } })
      fireEvent.change(await screen.findByLabelText('Signing secret'), { target: { value: 'private-route-secret' } })
      fireEvent.click(screen.getByRole('button', { name: 'Submit set' }))
      await waitFor(() => expect(attempts).toHaveLength(2))
      expect(attempts[1]).toEqual(attempts[0])
      expect(JSON.stringify(localStorage)).not.toContain('private-route-secret')
    } finally {
      setItem.mockRestore()
    }
  })

  it('does not activate admin reads for a protected route with no feature UI', async () => {
    const reads = observeReads()
    render(<OtherRoute />, { wrapper: pageWrapper(permissions) })
    await screen.findByText('Another protected route: ready')
    expect(reads).toEqual({ credentials: 0, recovery: 0, accounts: 0, bootstrap: 0, audit: 0 })
  })
})
