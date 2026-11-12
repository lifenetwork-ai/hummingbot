import { transferableAbortController } from 'node:util'
import { act, renderHook, waitFor } from '@testing-library/react'
import type { PropsWithChildren } from 'react'
import { http, HttpResponse } from 'msw'
import { afterAll, describe, expect, it, vi } from 'vitest'
import { ApiProvider } from '../../core/api/ApiProvider'
import { OperationalContext, type OperationalContextValue } from '../operational-context/useOperationalContext'
import { server } from '../../test/mocks/server'
import { useRecovery } from './useRecovery'

const authState = vi.hoisted(() => ({ canRecover: true }))
vi.mock('../../core/auth/auth-context', () => ({ useAuth: () => ({ identity: { permissions: { manage_recovery: authState.canRecover } } }) }))
const controller = transferableAbortController()
vi.stubGlobal('AbortController', controller.constructor)
vi.stubGlobal('AbortSignal', controller.signal.constructor)
afterAll(() => vi.unstubAllGlobals())

const baseUrl = 'https://recovery.test/api/v1/admin'
function wrapper({ children }: PropsWithChildren) {
  return <ApiProvider session={{ baseUrl, cacheIdentity: 'recovery-admin:1' }}>
    <OperationalContext.Provider value={operational}>{children}</OperationalContext.Provider>
  </ApiProvider>
}
const status = { bot_id: 'bot-a', control_version: 7, acknowledged_version: 7, desired_state: 'stopped', running: false,
  observed_session: null, session: null, owner_available: true, phase: 'halted', server_time: new Date().toISOString(),
  owner_identity: null, process_identity: null, actions: {}, recovery: null }
const operational = { status: { phase: 'ready', data: status, error: null, refreshing: false },
  refreshStatus: async () => ({ data: status, isSuccess: true }) } as unknown as OperationalContextValue
const checkOperation = { action: 'recovery_check', id: 'check-1', status: 'succeeded', stage: 'review_required',
  can_recover: true, confirmation_token: 'secret-confirmation', expires_at: new Date(Date.now() + 60_000).toISOString(),
  trading_state: 'stopped', created_at: new Date().toISOString(), updated_at: new Date().toISOString(), detail: 'Review',
  label: 'Recovery check', proposed_changes: [], verification: { orders: 1 } }
const resetOperation = { ...checkOperation, action: 'recovery_reset', id: 'reset-1', status: 'succeeded', stage: 'recovered',
  confirmation_token: null, proposed_changes: [], repaired_positions: [] }

describe('useRecovery', () => {
  it('checks, polls the exact receipt, confirms the same version, and reports recovered without starting', async () => {
    const writes: Array<{ url: string; body: unknown; key: string | null }> = []
    server.use(
      http.get(`${baseUrl}/status`, () => HttpResponse.json(status)),
      http.get(`${baseUrl}/recovery`, () => HttpResponse.json({ halted: true, control_version: 7,
        check_available: true, reset_available: true, blockers: [], operation: null })),
      http.get(`${baseUrl}/recovery/operations/:id`, ({ params }) => HttpResponse.json(
        params.id === 'check-1' ? checkOperation : resetOperation)),
      http.post(`${baseUrl}/recovery/check`, async ({ request }) => {
        writes.push({ url: new URL(request.url).pathname, body: await request.json(), key: request.headers.get('Idempotency-Key') })
        return HttpResponse.json({ ...checkOperation, status: 'accepted', stage: 'queued' }, { status: 202 })
      }),
      http.post(`${baseUrl}/recovery/reset`, async ({ request }) => {
        writes.push({ url: new URL(request.url).pathname, body: await request.json(), key: request.headers.get('Idempotency-Key') })
        return HttpResponse.json({ ...resetOperation, status: 'accepted', stage: 'queued' }, { status: 202 })
      }),
    )
    const { result } = renderHook(useRecovery, { wrapper })
    await waitFor(() => expect(result.current.checkAvailable).toBe(true))
    await act(async () => { await result.current.check() })
    await waitFor(() => expect(result.current.reviewAvailable).toBe(true))
    expect(result.current.checkReceipt.data?.confirmation_token).toBeNull()
    expect(result.current.operation?.confirmation_token).toBeNull()
    expect(writes[0]).toMatchObject({ url: '/api/v1/admin/recovery/check', body: { expected_version: 7 } })
    await act(async () => { await result.current.confirmReset() })
    await waitFor(() => expect(result.current.completed).toBe(true))
    expect(writes[1]).toMatchObject({ url: '/api/v1/admin/recovery/reset',
      body: { expected_version: 7, check_id: 'check-1', confirmation_token: 'secret-confirmation' } })
    expect(result.current.completed).toBe(true)
    expect(writes.map(({ url }) => url)).toEqual(['/api/v1/admin/recovery/check', '/api/v1/admin/recovery/reset'])
  })

  it('retries a timed out check with the same body and idempotency key', async () => {
    const attempts: Array<{ body: unknown; key: string | null }> = []
    server.use(
      http.get(`${baseUrl}/status`, () => HttpResponse.json(status)),
      http.get(`${baseUrl}/recovery`, () => HttpResponse.json({ halted: true, control_version: 7,
        check_available: true, reset_available: false, blockers: [], operation: null })),
      http.post(`${baseUrl}/recovery/check`, async ({ request }) => {
        attempts.push({ body: await request.json(), key: request.headers.get('Idempotency-Key') })
        return attempts.length === 1
          ? HttpResponse.json({ detail: 'temporary' }, { status: 503 })
          : HttpResponse.json({ ...checkOperation, status: 'accepted', stage: 'queued' }, { status: 202 })
      }),
      http.get(`${baseUrl}/recovery/operations/:id`, () => HttpResponse.json(checkOperation)),
    )
    const { result } = renderHook(useRecovery, { wrapper })
    await waitFor(() => expect(result.current.checkAvailable).toBe(true))
    await act(async () => { await expect(result.current.check()).rejects.toThrow() })
    await act(async () => { await result.current.retryCheck() })
    expect(attempts).toHaveLength(2)
    expect(attempts[1]).toEqual(attempts[0])
  })

  it('rejects a retry after recovery permission is revoked', async () => {
    authState.canRecover = true
    let attempts = 0
    server.use(
      http.get(`${baseUrl}/status`, () => HttpResponse.json(status)),
      http.get(`${baseUrl}/recovery`, () => HttpResponse.json({ halted: true, control_version: 7,
        check_available: true, reset_available: false, blockers: [], operation: null })),
      http.post(`${baseUrl}/recovery/check`, () => {
        attempts += 1
        return HttpResponse.json({ detail: 'temporary' }, { status: 503 })
      }),
    )
    const { result, rerender } = renderHook(useRecovery, { wrapper })
    await waitFor(() => expect(result.current.checkAvailable).toBe(true))
    await act(async () => { await expect(result.current.check()).rejects.toThrow() })
    authState.canRecover = false
    rerender()
    await act(async () => { await expect(result.current.retryCheck()).rejects.toThrow('permission_required') })
    expect(attempts).toBe(1)
    authState.canRecover = true
  })

  it('clears a definitive refresh_state rejection so a new check uses a new key', async () => {
    const keys: Array<string | null> = []
    let attempts = 0
    server.use(
      http.get(`${baseUrl}/status`, () => HttpResponse.json(status)),
      http.get(`${baseUrl}/recovery`, () => HttpResponse.json({ halted: true, control_version: 7,
        check_available: true, reset_available: false, blockers: [], operation: null })),
      http.post(`${baseUrl}/recovery/check`, async ({ request }) => {
        keys.push(request.headers.get('Idempotency-Key'))
        attempts += 1
        if (attempts === 1) return HttpResponse.json({ retry: 'refresh_state', code: 'control_version_changed' }, { status: 409 })
        return HttpResponse.json({ ...checkOperation, status: 'accepted', stage: 'queued' }, { status: 202 })
      }),
      http.get(`${baseUrl}/recovery/operations/:id`, () => HttpResponse.json(checkOperation)),
    )
    const { result } = renderHook(useRecovery, { wrapper })
    await waitFor(() => expect(result.current.checkAvailable).toBe(true))
    await act(async () => { await expect(result.current.check()).rejects.toThrow() })
    await act(async () => { await result.current.check() })
    expect(attempts).toBe(2)
    expect(keys[0]).not.toBe(keys[1])
  })

  it('requires permission to retry a timed out reset and reuses its exact body and key', async () => {
    authState.canRecover = true
    const attempts: Array<{ body: unknown; key: string | null }> = []
    let resetAttempts = 0
    server.use(
      http.get(`${baseUrl}/status`, () => HttpResponse.json(status)),
      http.get(`${baseUrl}/recovery`, () => HttpResponse.json({ halted: true, control_version: 7,
        check_available: true, reset_available: true, blockers: [], operation: null })),
      http.post(`${baseUrl}/recovery/check`, () => HttpResponse.json({ ...checkOperation, status: 'accepted', stage: 'queued' }, { status: 202 })),
      http.get(`${baseUrl}/recovery/operations/:id`, () => HttpResponse.json(checkOperation)),
      http.post(`${baseUrl}/recovery/reset`, async ({ request }) => {
        attempts.push({ body: await request.json(), key: request.headers.get('Idempotency-Key') })
        resetAttempts += 1
        return resetAttempts === 1
          ? HttpResponse.json({ detail: 'temporary' }, { status: 503 })
          : HttpResponse.json({ ...resetOperation, status: 'accepted', stage: 'queued' }, { status: 202 })
      }),
    )
    const { result, rerender } = renderHook(useRecovery, { wrapper })
    await waitFor(() => expect(result.current.checkAvailable).toBe(true))
    await act(async () => { await result.current.check() })
    await waitFor(() => expect(result.current.reviewAvailable).toBe(true))
    await act(async () => { await expect(result.current.confirmReset()).rejects.toThrow() })
    authState.canRecover = false
    rerender()
    await act(async () => { await expect(result.current.retryReset()).rejects.toThrow('permission_required') })
    expect(resetAttempts).toBe(1)
    authState.canRecover = true
    rerender()
    await act(async () => { await result.current.retryReset() })
    expect(attempts).toHaveLength(2)
    expect(attempts[1]).toEqual(attempts[0])
  })

  it('ignores a late accepted receipt after the API session scope changes', async () => {
    authState.canRecover = true
    let currentUrl = baseUrl
    let enteredPost!: () => void
    let releasePost!: () => void
    const posted = new Promise<void>((resolve) => { enteredPost = resolve })
    const pendingResponse = new Promise<Response>((resolve) => { releasePost = () => resolve(HttpResponse.json(
      { ...checkOperation, status: 'accepted', stage: 'queued' }, { status: 202 })) })
    server.use(
      http.get(`${baseUrl}/status`, () => HttpResponse.json(status)),
      http.get(`${baseUrl}/recovery`, () => HttpResponse.json({ halted: true, control_version: 7,
        check_available: true, reset_available: false, blockers: [], operation: null })),
      http.post(`${baseUrl}/recovery/check`, async () => { enteredPost(); return pendingResponse }),
      http.get('https://recovery-next.test/api/v1/admin/status', () => HttpResponse.json(status)),
      http.get('https://recovery-next.test/api/v1/admin/recovery', () => HttpResponse.json({ halted: true,
        control_version: 7, check_available: true, reset_available: false, blockers: [], operation: null })),
    )
    const scopedWrapper = ({ children }: PropsWithChildren) => <ApiProvider
      session={{ baseUrl: currentUrl, cacheIdentity: `recovery-admin:${currentUrl}` }}>
      <OperationalContext.Provider value={operational}>{children}</OperationalContext.Provider>
    </ApiProvider>
    const { result, rerender } = renderHook(useRecovery, { wrapper: scopedWrapper })
    await waitFor(() => expect(result.current.checkAvailable).toBe(true))
    let pendingCheckResult!: Promise<unknown>
    act(() => { pendingCheckResult = result.current.check() })
    await posted
    currentUrl = 'https://recovery-next.test/api/v1/admin'
    rerender()
    releasePost()
    await act(async () => { await expect(pendingCheckResult).rejects.toThrow('recovery_session_changed') })
    expect(result.current.operation?.id).not.toBe('check-1')
    expect(result.current.trackedReceipts).toEqual([])
  })
})
