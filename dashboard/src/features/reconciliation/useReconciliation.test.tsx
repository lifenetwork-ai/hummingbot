import { transferableAbortController } from 'node:util'
import { act, renderHook, waitFor } from '@testing-library/react'
import type { PropsWithChildren } from 'react'
import { afterAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import { ApiProvider } from '../../core/api/ApiProvider'
import { AuthContext, type AuthContextValue } from '../../core/auth/auth-context'
import { OperationalProvider } from '../operational-context/OperationalProvider'
import { contextPrincipal, contextStatus } from '../../test/mocks/contextFixtures'
import { server } from '../../test/mocks/server'
import { useReconciliation } from './useReconciliation'

const abort = transferableAbortController()
vi.stubGlobal('AbortController', abort.constructor)
vi.stubGlobal('AbortSignal', abort.signal.constructor)
afterAll(() => vi.unstubAllGlobals())
const baseUrl = 'https://reconcile.test/api/v1/admin'
let permitted = true
function Wrapper({ children }: PropsWithChildren) {
  const apiSession = { baseUrl, principalId: contextPrincipal.id, cacheIdentity: 'reconcile-1', getToken: () => 'test' }
  const auth: AuthContextValue = { identity: { ...contextPrincipal, permissions: { read: true, operate: permitted } }, status: 'authenticated',
    error: null, login: async () => {}, logout: () => {}, apiSession }
  return <AuthContext.Provider value={auth}><ApiProvider session={apiSession}><OperationalProvider>{children}</OperationalProvider></ApiProvider></AuthContext.Provider>
}
const body = { market: 'all' as const, pair: null, lookback_seconds: 3600, max_orders: 100, max_pages: 5 }
beforeEach(() => {
  permitted = true
  localStorage.clear()
  server.use(
    http.get(`${baseUrl}/status`, () => HttpResponse.json(contextStatus)),
    http.get(`${baseUrl}/balances`, () => HttpResponse.json({ accounts: [], owner_available: true, server_time: '' })),
    http.get(`${baseUrl}/reconciliation`, () => HttpResponse.json({ check: null, report: { state: 'not_checked' } })),
    http.get(`${baseUrl}/reconciliation/:id`, ({ params }) => HttpResponse.json({ id: params.id, status: 'succeeded', report: { state: 'mismatch', markets: [] } })),
  )
})

async function ready(result: { current: ReturnType<typeof useReconciliation> }) {
  await waitFor(() => expect(result.current.access.allowed && result.current.latest.isSuccess).toBe(true))
}

describe('observational reconciliation controller', () => {
  it('keeps exact receipt success separate from mismatch and prevents immediate new checks', async () => {
    let calls = 0
    server.use(http.post(`${baseUrl}/reconciliation/check`, () => { calls++; return HttpResponse.json({ id: 'check-1', status: 'accepted' }, { status: 202 }) }))
    const { result } = renderHook(useReconciliation, { wrapper: Wrapper })
    await ready(result)
    await act(async () => { await result.current.check(body) })
    await waitFor(() => expect(result.current.receipt?.outcome).toMatchObject({ kind: 'reconciliation', state: 'mismatch' }))
    await act(async () => { await expect(result.current.check(body)).rejects.toThrow('cooldown') })
    expect(calls).toBe(1)
  })
  it('restores same body/key after timeout and never replays on remount', async () => {
    const calls: { body: unknown; key: string | null }[] = []
    server.use(http.post(`${baseUrl}/reconciliation/check`, async ({ request }) => {
      calls.push({ body: await request.json(), key: request.headers.get('Idempotency-Key') })
      return calls.length === 1 ? HttpResponse.error() : HttpResponse.json({ id: 'check-2', status: 'accepted' }, { status: 202 })
    }))
    const first = renderHook(useReconciliation, { wrapper: Wrapper })
    await ready(first.result)
    await act(async () => { await expect(first.result.current.check(body)).rejects.toThrow() })
    first.unmount()
    const next = renderHook(useReconciliation, { wrapper: Wrapper })
    await ready(next.result)
    expect(calls).toHaveLength(1)
    expect(next.result.current.pendingRetry?.body).toEqual(body)
    await act(async () => { await next.result.current.retryPending() })
    expect(calls).toHaveLength(2)
    expect(calls[1]).toEqual(calls[0])
  })
  it('rejects permission loss and invalid bounds before mutation', async () => {
    let calls = 0
    server.use(http.post(`${baseUrl}/reconciliation/check`, () => { calls++; return HttpResponse.json({}) }))
    const { result, rerender } = renderHook(useReconciliation, { wrapper: Wrapper })
    await ready(result)
    await act(async () => { await expect(result.current.check({ ...body, max_orders: 201 })).rejects.toThrow('bounds') })
    permitted = false
    rerender()
    await act(async () => { await expect(result.current.check(body)).rejects.toThrow('permission') })
    expect(calls).toBe(0)
  })
  it('allows a new reviewed request after definitive validation failure without retrying it', async () => {
    const keys: (string | null)[] = []
    server.use(http.post(`${baseUrl}/reconciliation/check`, ({ request }) => {
      keys.push(request.headers.get('Idempotency-Key'))
      return keys.length === 1 ? HttpResponse.json({ retry: 'fix_request', fields: [{ path: 'pair', code: 'invalid' }] }, { status: 422 })
        : HttpResponse.json({ id: 'check-3', status: 'accepted' }, { status: 202 })
    }))
    const { result } = renderHook(useReconciliation, { wrapper: Wrapper })
    await ready(result)
    await act(async () => { await expect(result.current.check(body)).rejects.toMatchObject({ status: 422 }) })
    await waitFor(() => expect(result.current.pendingRetry).toBeUndefined())
    await act(async () => { await result.current.check({ ...body, market: 'spot' }) })
    expect(keys).toHaveLength(2)
    expect(keys[1]).not.toBe(keys[0])
  })
})
