import { transferableAbortController } from 'node:util'
import { act, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react'
import type { PropsWithChildren } from 'react'
import { http, HttpResponse } from 'msw'
import { afterAll, describe, expect, it, vi } from 'vitest'
import { ApiProvider } from '../../core/api/ApiProvider'
import { useQueryClient } from '@tanstack/react-query'
import { adminKeys } from '../../core/api/queries'
import { OperationalContext, type OperationalContextValue } from '../operational-context/useOperationalContext'
import { server } from '../../test/mocks/server'
import { useRecoveryOperationContext } from './RecoveryOperationContext'
import { RecoveryOperationProvider } from './RecoveryOperationProvider'

const authState = vi.hoisted(() => ({ canRecover: true }))
vi.mock('../../core/auth/auth-context', () => ({ useAuth: () => ({ identity: { permissions: { manage_recovery: authState.canRecover } } }) }))
const controller = transferableAbortController()
vi.stubGlobal('AbortController', controller.constructor)
vi.stubGlobal('AbortSignal', controller.signal.constructor)
afterAll(() => vi.unstubAllGlobals())

const baseUrl = 'https://recovery.test/api/v1/admin'
function wrapper({ children }: PropsWithChildren) {
  return <ApiProvider session={{ baseUrl, cacheIdentity: 'recovery-admin:1' }}>
    <OperationalContext.Provider value={operational}><RecoveryOperationProvider>{children}</RecoveryOperationProvider></OperationalContext.Provider>
  </ApiProvider>
}
const status = { bot_id: 'bot-a', control_version: 7, acknowledged_version: 7, desired_state: 'stopped', running: false,
  observed_session: null, session: null, owner_available: true, phase: 'halted', server_time: new Date().toISOString(),
  owner_identity: null, process_identity: null, actions: {}, recovery: null }
const operational = { status: { phase: 'ready', data: status, error: null, refreshing: false },
  refreshStatus: async () => ({ data: status, isSuccess: true }) } as unknown as OperationalContextValue

function RecoveryViewHarness({ show }: { show: boolean }) {
  return <>
    <RecoveryActivityProbe />
    {show ? <RecoveryProbe /> : null}
  </>
}

function RecoveryActivityProbe() {
  const queryClient = useQueryClient()
  const receiptStatus = queryClient.getQueryData<{ status?: string }>(adminKeys.query('getRecoveryOperation', { path: { operation_id: 'check-1' } }))?.status ?? ''
  return <>
    <button onClick={() => { void queryClient.invalidateQueries({ queryKey: adminKeys.operation('getRecovery') }) }}>Invalidate Overview</button>
    <output data-testid="recovery-receipt-status">{receiptStatus}</output>
  </>
}

function RecoveryProbe() {
  const value = useRecoveryOperationContext()
  const queryClient = useQueryClient()
  const queryCacheHasToken = queryClient.getQueryCache().getAll().some((query) => JSON.stringify(query.state.data ?? null).includes('secret-confirmation'))
  const mutationCacheHasToken = queryClient.getMutationCache().getAll().some((mutation) => JSON.stringify(mutation.state.data ?? null).includes('secret-confirmation'))
  const tokenQueryKeys = queryClient.getQueryCache().getAll().filter((query) => JSON.stringify(query.state.data ?? null).includes('secret-confirmation')).map((query) => query.queryKey)
  return <div>
    <button onClick={() => { void value.check().catch(() => undefined) }}>Start Check</button>
    <button onClick={() => { void value.retryCheck().catch(() => undefined) }}>Retry Check</button>
    <button onClick={() => { void value.confirmReset().catch(() => undefined) }}>Start Reset</button>
    <button onClick={() => { void value.retryReset().catch(() => undefined) }}>Retry Reset</button>
    <output data-testid="recovery-probe">{JSON.stringify({ canRetryCheck: value.canRetryCheck,
      canRetryReset: value.canRetryReset, checkAvailable: value.checkAvailable, lock: value.lockSessionAndConnections,
      operationId: value.operation?.id,
      operationToken: value.operation?.confirmation_token,
      queryToken: value.checkReceipt.data?.confirmation_token,
      queryCacheHasToken,
      tokenQueryKeys,
      mutationCacheHasToken,
      feedback: value.feedback,
      active: value.viewModel.summary !== undefined })}</output>
  </div>
}
const checkOperation = { action: 'recovery_check', id: 'check-1', status: 'succeeded', stage: 'review_required',
  can_recover: true, confirmation_token: 'secret-confirmation', expires_at: new Date(Date.now() + 60_000).toISOString(),
  trading_state: 'stopped', created_at: new Date().toISOString(), updated_at: new Date().toISOString(), detail: 'Review',
  label: 'Recovery check', proposed_changes: [], verification: { scope: 'verified_checkpoint', orders_checked: 0, fills_checked: 1 } }
const resetOperation = { ...checkOperation, action: 'recovery_reset', id: 'reset-1', status: 'succeeded', stage: 'recovered',
  confirmation_token: null, proposed_changes: [], repaired_positions: [] }

describe('useRecovery', () => {
  it('does not replay an unknown Check after shutdown verification begins', async () => {
    let waiting = false
    let writes = 0
    server.use(
      http.get(`${baseUrl}/status`, () => HttpResponse.json(status)),
      http.get(`${baseUrl}/recovery`, () => HttpResponse.json({ halted: true, check_available: true, reset_available: false, blockers: [], operation: null, control_version: 7 })),
      http.post(`${baseUrl}/recovery/check`, () => { writes++; return HttpResponse.json({ detail: 'unknown outcome' }, { status: 503 }) }),
    )
    function ScopedWrapper({ children }: PropsWithChildren) {
      const current = { ...operational, status: { ...operational.status, data: { ...operational.status.data!, recovery: waiting ? { halted: true, next_action: 'wait' } : null } } } as OperationalContextValue
      return <ApiProvider session={{ baseUrl, cacheIdentity: 'shutdown-check' }}><OperationalContext.Provider value={current}><RecoveryOperationProvider>{children}</RecoveryOperationProvider></OperationalContext.Provider></ApiProvider>
    }
    const { result, rerender } = renderHook(useRecoveryOperationContext, { wrapper: ScopedWrapper })
    await waitFor(() => expect(result.current.checkAvailable).toBe(true))
    await act(async () => { await expect(result.current.check()).rejects.toThrow() })
    waiting = true
    rerender()
    await act(async () => { await expect(result.current.retryCheck()).rejects.toThrow('shutdown_verification_pending') })
    expect(writes).toBe(1)
  })
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
    const { result } = renderHook(useRecoveryOperationContext, { wrapper })
    await waitFor(() => expect(result.current.checkAvailable).toBe(true))
    await act(async () => { await result.current.check() })
    await waitFor(() => expect(result.current.reviewAvailable).toBe(true))
    expect(result.current.checkReceipt.data?.confirmation_token).toBeNull()
    expect(result.current.operation?.confirmation_token).toBeNull()
    expect(result.current.verification).toMatchObject({ checkpointPreserved: true, ordersChecked: 0, fillsChecked: 1, proposedChanges: [], ordersCheckedMeaning: 'no_historical_per_order_reads' })
    expect(writes[0]).toMatchObject({ url: '/api/v1/admin/recovery/check', body: { expected_version: 7 } })
    await act(async () => { await result.current.confirmReset() })
    await waitFor(() => expect(result.current.completed).toBe(true))
    expect(writes[1]).toMatchObject({ url: '/api/v1/admin/recovery/reset',
      body: { expected_version: 7, check_id: 'check-1', confirmation_token: 'secret-confirmation', confirm_order_changes: false } })
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
    const { result } = renderHook(useRecoveryOperationContext, { wrapper })
    await waitFor(() => expect(result.current.checkAvailable).toBe(true))
    await act(async () => { await expect(result.current.check()).rejects.toThrow() })
    await act(async () => { await result.current.retryCheck() })
    expect(attempts).toHaveLength(2)
    expect(attempts[1]).toEqual(attempts[0])
  })

  it('serializes concurrent Check preflights and retains the first request key after timeout', async () => {
    const attempts: Array<{ body: unknown; key: string | null }> = []
    let releaseStatus!: (value: { data: typeof status; isSuccess: boolean }) => void
    const statusRead = new Promise<{ data: typeof status; isSuccess: boolean }>((resolve) => { releaseStatus = resolve })
    let statusReads = 0
    const preflightWrapper = ({ children }: PropsWithChildren) => <ApiProvider session={{ baseUrl, cacheIdentity: 'recovery-preflight-lock' }}>
      <OperationalContext.Provider value={{ ...operational, refreshStatus: () => { statusReads++; return statusRead } } as OperationalContextValue}>
        <RecoveryOperationProvider>{children}</RecoveryOperationProvider>
      </OperationalContext.Provider>
    </ApiProvider>
    server.use(
      http.get(`${baseUrl}/status`, () => HttpResponse.json(status)),
      http.get(`${baseUrl}/recovery`, () => HttpResponse.json({ halted: true, control_version: 7,
        check_available: true, reset_available: false, blockers: [], operation: null })),
      http.post(`${baseUrl}/recovery/check`, async ({ request }) => {
        attempts.push({ body: await request.json(), key: request.headers.get('Idempotency-Key') })
        return attempts.length === 1
          ? HttpResponse.json({ detail: 'timeout outcome unknown' }, { status: 503 })
          : HttpResponse.json({ ...checkOperation, status: 'accepted', stage: 'queued' }, { status: 202 })
      }),
      http.get(`${baseUrl}/recovery/operations/:id`, () => HttpResponse.json(checkOperation)),
    )
    render(<RecoveryViewHarness show />, { wrapper: preflightWrapper })
    await waitFor(() => expect(screen.getByTestId('recovery-probe').textContent).toContain('"checkAvailable":true'))
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Start Check' }))
      fireEvent.click(screen.getByRole('button', { name: 'Start Check' }))
    })
    expect(statusReads).toBe(1)
    expect(screen.getByTestId('recovery-probe').textContent).toContain('"lock":true')
    await act(async () => { releaseStatus({ data: status, isSuccess: true }) })
    await waitFor(() => expect(screen.getByTestId('recovery-probe').textContent).toContain('"canRetryCheck":true'))
    expect(attempts).toHaveLength(1)
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Retry Check' })) })
    await waitFor(() => expect(attempts).toHaveLength(2))
    expect(attempts[1]).toEqual(attempts[0])
  })

  it('keeps an unknown Check candidate through unit unmount and retries the exact request after remount', async () => {
    const attempts: Array<{ body: unknown; key: string | null }> = []
    let overviewReads = 0
    server.use(
      http.get(`${baseUrl}/status`, () => HttpResponse.json(status)),
      http.get(`${baseUrl}/recovery`, () => { overviewReads++; return HttpResponse.json({ halted: true, control_version: 7,
        check_available: true, reset_available: false, blockers: [], operation: null }) }),
      http.post(`${baseUrl}/recovery/check`, async ({ request }) => {
        attempts.push({ body: await request.json(), key: request.headers.get('Idempotency-Key') })
        return attempts.length === 1
          ? HttpResponse.json({ detail: 'timeout outcome unknown' }, { status: 503 })
          : HttpResponse.json({ ...checkOperation, status: 'accepted', stage: 'queued' }, { status: 202 })
      }),
      http.get(`${baseUrl}/recovery/operations/:id`, () => HttpResponse.json(checkOperation)),
    )
    const { rerender } = render(<RecoveryViewHarness show />, { wrapper })
    await waitFor(() => expect(screen.getByTestId('recovery-probe').textContent).toContain('"active":true'))
    expect(overviewReads).toBeGreaterThan(0)
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Start Check' })) })
    await waitFor(() => expect(screen.getByTestId('recovery-probe').textContent).toContain('"canRetryCheck":true'))
    rerender(<RecoveryViewHarness show={false} />)
    expect(screen.queryByTestId('recovery-probe')).not.toBeInTheDocument()
    const readsWhenInactive = overviewReads
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Invalidate Overview' })) })
    expect(overviewReads).toBe(readsWhenInactive)
    rerender(<RecoveryViewHarness show />)
    await waitFor(() => expect(screen.getByTestId('recovery-probe').textContent).toContain('"canRetryCheck":true'))
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Invalidate Overview' })) })
    await waitFor(() => expect(overviewReads).toBeGreaterThan(readsWhenInactive))
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Retry Check' })) })
    await waitFor(() => expect(screen.getByTestId('recovery-probe').textContent).toContain('"operationId":"check-1"'))
    expect(attempts).toHaveLength(2)
    expect(attempts[1]).toEqual(attempts[0])
    expect(screen.getByTestId('recovery-probe').textContent).not.toContain('secret-confirmation')
    expect(screen.getByTestId('recovery-probe').textContent).toContain('"queryToken":null')
    expect(screen.getByTestId('recovery-probe').textContent).toContain('"queryCacheHasToken":false')
    expect(screen.getByTestId('recovery-probe').textContent).toContain('"mutationCacheHasToken":false')
  })

  it('continues pending receipt polling to terminal state after the Recovery unit unmounts', async () => {
    let receiptReads = 0
    let overviewReads = 0
    server.use(
      http.get(`${baseUrl}/status`, () => HttpResponse.json(status)),
      http.get(`${baseUrl}/recovery`, () => { overviewReads++; return HttpResponse.json({ halted: true, control_version: 7,
        check_available: true, reset_available: false, blockers: [], operation: null }) }),
      http.post(`${baseUrl}/recovery/check`, () => HttpResponse.json({ ...checkOperation, status: 'accepted', stage: 'queued' }, { status: 202 })),
      http.get(`${baseUrl}/recovery/operations/:id`, () => {
        receiptReads++
        return HttpResponse.json(receiptReads < 2
          ? { ...checkOperation, status: 'applying', stage: 'applying', confirmation_token: null }
          : { ...checkOperation, status: 'succeeded', confirmation_token: null })
      }),
    )
    const { rerender } = render(<RecoveryViewHarness show />, { wrapper })
    await waitFor(() => expect(screen.getByTestId('recovery-probe').textContent).toContain('"checkAvailable":true'))
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Start Check' })) })
    await waitFor(() => expect(receiptReads).toBe(1))
    rerender(<RecoveryViewHarness show={false} />)
    const overviewReadsWhenInactive = overviewReads
    await waitFor(() => expect(receiptReads).toBeGreaterThanOrEqual(2), { timeout: 3000 })
    expect(overviewReads).toBe(overviewReadsWhenInactive)
  })

  it('keeps a timed out Reset candidate through unit unmount and retries the exact request and key when storage is unavailable', async () => {
    const attempts: Array<{ body: unknown; key: string | null }> = []
    let overviewReads = 0
    const setItem = vi.spyOn(window.localStorage, 'setItem').mockImplementation(() => { throw new Error('storage unavailable') })
    server.use(
      http.get(`${baseUrl}/status`, () => HttpResponse.json(status)),
      http.get(`${baseUrl}/recovery`, () => { overviewReads++; return HttpResponse.json({ halted: true, control_version: 7,
        check_available: true, reset_available: true, blockers: [], operation: null }) }),
      http.post(`${baseUrl}/recovery/check`, () => HttpResponse.json({ ...checkOperation, status: 'accepted', stage: 'queued' }, { status: 202 })),
      http.get(`${baseUrl}/recovery/operations/:id`, ({ params }) => HttpResponse.json(params.id === 'reset-1' ? resetOperation : checkOperation)),
      http.post(`${baseUrl}/recovery/reset`, async ({ request }) => {
        attempts.push({ body: await request.json(), key: request.headers.get('Idempotency-Key') })
        return attempts.length === 1
          ? HttpResponse.json({ detail: 'timeout outcome unknown' }, { status: 503 })
          : HttpResponse.json({ ...resetOperation, status: 'accepted', stage: 'queued' }, { status: 202 })
      }),
    )
    try {
      const { rerender } = render(<RecoveryViewHarness show />, { wrapper })
      await waitFor(() => expect(screen.getByTestId('recovery-probe').textContent).toContain('"checkAvailable":true'))
      await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Start Check' })) })
      await waitFor(() => expect(screen.getByTestId('recovery-probe').textContent).toContain('"operationId":"check-1"'))
      await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Start Reset' })) })
      await waitFor(() => expect(screen.getByTestId('recovery-probe').textContent).toContain('"canRetryReset":true'))
      rerender(<RecoveryViewHarness show={false} />)
      const readsWhenInactive = overviewReads
      await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Invalidate Overview' })) })
      expect(overviewReads).toBe(readsWhenInactive)
      rerender(<RecoveryViewHarness show />)
      await waitFor(() => expect(screen.getByTestId('recovery-probe').textContent).toContain('"canRetryReset":true'))
      await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Retry Reset' })) })
      await waitFor(() => expect(attempts).toHaveLength(2))
      expect(attempts[1]).toEqual(attempts[0])
      await waitFor(() => expect(screen.getByTestId('recovery-probe').textContent).toContain('"operationId":"reset-1"'))
      expect(screen.getByTestId('recovery-probe').textContent).not.toContain('secret-confirmation')
      expect(screen.getByTestId('recovery-probe').textContent).toContain('"queryCacheHasToken":false')
      expect(screen.getByTestId('recovery-probe').textContent).toContain('"mutationCacheHasToken":false')
    } finally {
      setItem.mockRestore()
    }
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
    const { result, rerender } = renderHook(useRecoveryOperationContext, { wrapper })
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
    const { result } = renderHook(useRecoveryOperationContext, { wrapper })
    await waitFor(() => expect(result.current.checkAvailable).toBe(true))
    await act(async () => { await expect(result.current.check()).rejects.toThrow() })
    await act(async () => { await result.current.check() })
    expect(attempts).toBe(2)
    expect(keys[0]).not.toBe(keys[1])
  })

  it('clears a timed out Reset candidate when recovery permission is revoked', async () => {
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
    const { result, rerender } = renderHook(useRecoveryOperationContext, { wrapper })
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
    await act(async () => { await expect(result.current.retryReset()).rejects.toThrow('No recovery reset with an unknown outcome is available to retry') })
    expect(attempts).toHaveLength(1)
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
      <OperationalContext.Provider value={operational}><RecoveryOperationProvider>{children}</RecoveryOperationProvider></OperationalContext.Provider>
    </ApiProvider>
    const { result, rerender } = renderHook(useRecoveryOperationContext, { wrapper: scopedWrapper })
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
