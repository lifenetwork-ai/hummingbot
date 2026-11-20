import { transferableAbortController } from 'node:util'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider } from '@tanstack/react-router'
import { afterAll, describe, expect, it, vi } from 'vitest'
import { ConnectionsUnit } from '../features/connections/ConnectionsUnit'
import { contextStatus } from '../test/mocks/contextFixtures'
import { pageApiUrl, pageWrapper, setupPageApi } from '../test/operationalTest'
import { server } from '../test/mocks/server'

const abort = transferableAbortController()
vi.stubGlobal('AbortController', abort.constructor)
vi.stubGlobal('AbortSignal', abort.signal.constructor)
afterAll(() => vi.unstubAllGlobals())
setupPageApi()

const permissions = { read: true, operate: true, manage_credentials: true, manage_recovery: true, manage_accounts: true }

function ProtectedRouteSwitch() {
  const root = createRootRoute({ component: () => <><button type="button" onClick={() => { router.history.push('/other') }}>Leave Connections</button><Outlet /></> })
  const connections = createRoute({ getParentRoute: () => root, path: '/', component: () => <ConnectionsUnit /> })
  const other = createRoute({ getParentRoute: () => root, path: '/other', component: () => <><p>Other protected route</p><button type="button" onClick={() => { router.history.push('/') }}>Return to Connections</button></> })
  const router: ReturnType<typeof createRouter> = createRouter({ routeTree: root.addChildren([connections, other]), history: createMemoryHistory({ initialEntries: ['/'] }) })
  return <RouterProvider router={router} />
}

describe('FeatureOperationsBoundary route lifetime', () => {
  it('retains an unknown Connections request for same-body retry across route unmount when storage fails', async () => {
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
      render(<ProtectedRouteSwitch />, { wrapper: pageWrapper(permissions) })
      fireEvent.click(await screen.findByRole('button', { name: 'Set okx credentials' }))
      fireEvent.change(await screen.findByLabelText('Public key'), { target: { value: 'public-route-key' } })
      fireEvent.change(await screen.findByLabelText('Signing secret'), { target: { value: 'private-route-secret' } })
      fireEvent.click(screen.getByRole('button', { name: 'Submit set' }))
      await waitFor(() => expect(attempts).toHaveLength(1))
      await screen.findByRole('button', { name: 'Review latest state for same-key retry' })
      expect(setItem).not.toHaveBeenCalled()

      fireEvent.click(screen.getByRole('button', { name: 'Leave Connections' }))
      expect(await screen.findByText('Other protected route')).toBeInTheDocument()
      expect(attempts).toHaveLength(1)
      fireEvent.click(screen.getByRole('button', { name: 'Return to Connections' }))
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
})
