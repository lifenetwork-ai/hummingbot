import { transferableAbortController } from 'node:util'
import { act, renderHook, waitFor } from '@testing-library/react'
import type { PropsWithChildren } from 'react'
import { afterAll, describe, expect, it, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import { ApiProvider, type ApiSession } from '../../core/api/ApiProvider'
import { AuthContext, type AuthContextValue } from '../../core/auth/auth-context'
import { server } from '../../test/mocks/server'
import { useAccountsController } from './useAccountsController'

const nativeController = transferableAbortController()
vi.stubGlobal('AbortController', nativeController.constructor)
vi.stubGlobal('AbortSignal', nativeController.signal.constructor)
afterAll(() => vi.unstubAllGlobals())

const baseUrl = 'https://accounts.example.test/api/v1/admin'
const session: ApiSession = { baseUrl, cacheIdentity: 'accounts-test', getToken: () => 'token' }
function wrapperFor(permissions: Record<string, boolean>) {
  const auth: AuthContextValue = { status: 'authenticated', identity: { id: 'admin-1', email: 'admin@example.test', kind: 'google', role: 'admin', role_label: 'Admin', permissions }, error: null, login: async () => {}, logout: () => {}, apiSession: session }
  return function Wrapper({ children }: PropsWithChildren) { return <AuthContext.Provider value={auth}><ApiProvider session={session}>{children}</ApiProvider></AuthContext.Provider> }
}

describe('accounts controller', () => {
  it('requires manage_accounts permission before reading or mutating accounts', async () => {
    let reads = 0
    let writes = 0
    let bootstrapReads = 0
    server.use(http.get(`${baseUrl}/access/accounts`, () => { reads++; return HttpResponse.json({ items: [], next_cursor: null }) }),
      http.post(`${baseUrl}/access/accounts`, () => { writes++; return HttpResponse.json({}, { status: 201 }) }), http.get(`${baseUrl}/access/bootstrap`, () => { bootstrapReads++; return HttpResponse.json({}) }))
    const { result } = renderHook(() => useAccountsController(), { wrapper: wrapperFor({ read: true }) })
    expect(result.current.access).toMatchObject({ allowed: false, reasonCode: 'permission_required' })
    expect(() => result.current.invite({ email: 'new@example.test', role: 'read', active: true })).toThrow('manage_accounts')
    expect(reads).toBe(0)
    expect(bootstrapReads).toBe(0)
    expect(writes).toBe(0)
  })

  it('sends explicit account fields and expected version, surfaces last-admin conflict without retry', async () => {
    const calls: { url: string; body: unknown }[] = []
    server.use(
      http.get(`${baseUrl}/access/accounts`, () => HttpResponse.json({ items: [], next_cursor: null })),
      http.get(`${baseUrl}/access/bootstrap`, () => HttpResponse.json({ active_google_admins: 1, basic_auth_enabled: false, detail: 'ready', google_admin_ready: true, google_auth_enabled: true })),
      http.post(`${baseUrl}/access/accounts`, async ({ request }) => { calls.push({ url: new URL(request.url).pathname, body: await request.json() }); return HttpResponse.json({ id: 'invite-1', email: 'new@example.test', role: 'read', active: true, bound: false, version: 1, created_at: '', updated_at: '' }, { status: 201 }) }),
      http.post(`${baseUrl}/access/accounts/:id`, async ({ request }) => { calls.push({ url: new URL(request.url).pathname, body: await request.json() }); return HttpResponse.json({ error: 'last_active_admin_required', code: 'last_active_admin_required', request_id: 'request-1' }, { status: 409 }) }),
    )
    const { result } = renderHook(() => useAccountsController(), { wrapper: wrapperFor({ manage_accounts: true }) })
    await waitFor(() => expect(result.current.accounts.isSuccess && result.current.bootstrap.isSuccess).toBe(true))
    await act(async () => { await result.current.invite({ email: 'new@example.test', role: 'read', active: true }) })
    await act(async () => { await expect(result.current.change('admin-1', { active: false, role: null, expected_version: 7 })).rejects.toMatchObject({ status: 409 }) })
    expect(calls).toEqual([
      { url: '/api/v1/admin/access/accounts', body: { email: 'new@example.test', role: 'read', active: true } },
      { url: '/api/v1/admin/access/accounts/admin-1', body: { active: false, role: null, expected_version: 7 } },
    ])
  })


  it('rejects invalid invitation and stale-version input before sending a request', async () => {
    let writes = 0
    server.use(http.get(`${baseUrl}/access/accounts`, () => HttpResponse.json({ items: [], next_cursor: null })),
      http.get(`${baseUrl}/access/bootstrap`, () => HttpResponse.json({})),
      http.post(`${baseUrl}/access/accounts`, () => { writes++; return HttpResponse.json({}, { status: 201 }) }),
      http.post(`${baseUrl}/access/accounts/:id`, () => { writes++; return HttpResponse.json({}, { status: 200 }) }))
    const { result } = renderHook(() => useAccountsController(), { wrapper: wrapperFor({ manage_accounts: true }) })
    expect(() => result.current.invite({ email: 'bad', role: 'read', active: true })).toThrow('valid account email')
    expect(() => result.current.invite({ email: 'new@example.test', role: 'owner' as 'read', active: true })).toThrow('valid account role')
    expect(() => result.current.change('admin-1', { active: true, role: null, expected_version: -1 })).toThrow('non-negative integer')
    expect(() => result.current.change('admin-1', { active: true, role: 'owner' as 'read', expected_version: 1 })).toThrow('valid account role')
    expect(writes).toBe(0)
  })
})
