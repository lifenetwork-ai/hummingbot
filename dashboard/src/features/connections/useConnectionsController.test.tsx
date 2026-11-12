import { act, renderHook, waitFor } from '@testing-library/react'
import { transferableAbortController } from 'node:util'
import type { PropsWithChildren } from 'react'
import { afterAll, afterEach, describe, expect, it, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import { ApiProvider, type ApiSession } from '../../core/api/ApiProvider'
import { ApiSessionContext } from '../../core/api/ApiSessionContext'
import { credentialReceipt } from '../../test/mocks/receiptFixtures'
import { server } from '../../test/mocks/server'
import { OperationalContext, type OperationalContextValue } from '../operational-context/useOperationalContext'
import { deriveOperationalContext } from '../operational-context/context'
import { useConnectionsController } from './useConnectionsController'

const nativeController = transferableAbortController()
vi.stubGlobal('AbortController', nativeController.constructor)
vi.stubGlobal('AbortSignal', nativeController.signal.constructor)
afterAll(() => vi.unstubAllGlobals())

const baseUrl = 'https://connections.example.test/api/v1/admin'
const session: ApiSession = { baseUrl, cacheIdentity: 'operator-1:login-1', principalId: 'operator-1', getToken: () => 'token' }
let canManage = true
let staleRuntime = false
let recoveryActive = false
const credentialSlot = {
  actions: Object.fromEntries(['configure', 'disconnect', 'retry_cleanup', 'repair_cleanup_key'].map((name) => [name, { allowed: true, detail: name, reason_code: null }])),
  changed_at: null, changed_by: null, cleanup_operation_id: 'root-1', configured: true, connector: 'spot', detail: '', label: 'Spot', observed_at: '2026-11-11T00:00:00Z', operation_id: 'root-1', operation_version: 7, reason_code: null, status: 'cleanup_blocked', version: 3,
}
const statusData = { exchange: 'okx', environment: 'demo', source: 'postgres_kms', slots: [credentialSlot] }
const catalogueData = { source: 'postgres_kms', exchanges: [{ exchange: 'okx', environment: 'demo', site: null, cleanup_mode: 'managed', disconnect_available: true, connectors: ['spot'], fields: [
  { name: 'api_key', label: 'API key', required: true, secret: true }, { name: 'secret_key', label: 'Secret', required: true, secret: true }, { name: 'passphrase', label: 'Passphrase', required: true, secret: true },
] }] }
const runtimeData = { bot_id: 'bot-a', desired_state: 'stopped', control_version: 9, acknowledged_version: 9, owner_available: true, operator: { state: 'stopped', label: 'Stopped', detail: '', next_action: 'monitor' }, observation: { observed_at: '2026-11-11T00:00:00Z', get stale() { return staleRuntime }, session_matches: true, reason_code: null } }
const identity = () => ({ id: 'operator-1', email: null, kind: 'google' as const, role: 'write' as const, role_label: 'Operator', permissions: { manage_credentials: canManage } })
function operational(): OperationalContextValue {
  return { ...deriveOperationalContext({ identity: identity() as never,
    status: { phase: 'ready', data: runtimeData as never, error: null, refreshing: false },
    balances: { phase: 'ready', data: { accounts: [], owner_available: true } as never, error: null, refreshing: false } }),
    statusUpdatedAt: Date.now(),
    ...(recoveryActive ? { recoveryLock: { active: true, reference: { kind: 'recovery' as const, id: 'recovery-running' }, phase: 'pending' as const, detail: 'Recovery running; wait for its receipt.' } } : {}),
    refreshStatus: async () => ({ data: runtimeData, isSuccess: true } as never),
    refreshBalances: async () => ({ data: { accounts: [], owner_available: true }, isSuccess: true } as never),
  } as OperationalContextValue
}
function Wrapper({ children }: PropsWithChildren) {
  return <ApiProvider session={session}><ApiSessionContext.Provider value={{ apiBaseUrl: baseUrl, principalId: 'operator-1' }}>
    <OperationalContext.Provider value={operational()}>{children}</OperationalContext.Provider>
  </ApiSessionContext.Provider></ApiProvider>
}
function installReads() {
  server.use(
    http.get(`${baseUrl}/credentials`, () => HttpResponse.json(catalogueData)),
    http.get(`${baseUrl}/credentials/okx`, () => HttpResponse.json(statusData)),
    http.get(`${baseUrl}/status`, () => HttpResponse.json(runtimeData)),
    http.get(`${baseUrl}/credentials/operations/root-1`, () => HttpResponse.json({ id: 'root-1', status: 'applying', action: 'credentials_disconnect', created_at: '', updated_at: '', effective_version: 3, expected_version: 3, error: null, session_id: null, payload: { action: 'credentials_disconnect', actor_id: 'op', actor_kind: 'user', bot_id: 'bot-a', cleanup_operation_id: null, connector: 'spot', environment: 'demo', exchange: 'okx', expected_control_version: 9, expected_operation_version: null, expected_version: 3 }, result: { stage: 'cancelling_orders', blocked_stage: null, reason_code: null, next_retry_at: null, cleanup_operation_id: null, credential_version: 3, operation_version: 7, operator_action_required: false, orders_terminal_verified: null, positions_flat_verified: null, ledger_confirmed: null, consumers_detached: null, account_verified: null, position_checkpoints_verified: null, owner_generation: null, owner_uuid: null, observed_at: null, remaining_order_count: 1, remaining_position_count: 0 } })),
  )
}

afterEach(() => { canManage = true; staleRuntime = false; recoveryActive = false })

describe('Connections controller', () => {
  it('submits each action with current versions and root cleanup ID, then follows receipts', async () => {
    installReads()
    const bodies = new Map<string, unknown>()
    const handlers = [
      ['set', '/credentials/okx/spot', 'setExchangeCredentials'],
      ['disconnect', '/credentials/okx/spot/revoke', 'disconnectExchangeCredentials'],
      ['retry', '/credentials/operations/root-1/retry', 'retryCredentialCleanup'],
      ['repair', '/credentials/okx/spot/cleanup-credentials', 'repairCleanupCredentials'],
    ] as const
    for (const [name, path] of handlers) server.use(http.post(`${baseUrl}${path}`, async ({ request }) => {
      bodies.set(name, { body: await request.json(), key: request.headers.get('Idempotency-Key') })
      return HttpResponse.json({ id: `${name}-receipt`, status: 'accepted', action: `credentials_${name}`, result: { stage: 'waiting_for_owner' } }, { status: 202 })
    }))
    server.use(http.get(`${baseUrl}/credentials/operations/:id`, ({ params }) => HttpResponse.json({ id: params.id, status: 'applying', action: 'credentials_set', created_at: '', updated_at: '', effective_version: 3, expected_version: 3, error: null, session_id: null, payload: { action: 'credentials_set', actor_id: 'op', actor_kind: 'user', bot_id: 'bot-a', cleanup_operation_id: null, connector: 'spot', environment: 'demo', exchange: 'okx', expected_control_version: 9, expected_operation_version: null, expected_version: 3 }, result: { stage: 'loading', blocked_stage: null, reason_code: null, next_retry_at: null, cleanup_operation_id: null, credential_version: 3, operation_version: 7, operator_action_required: false, orders_terminal_verified: null, positions_flat_verified: null, ledger_confirmed: null, consumers_detached: null, account_verified: null, position_checkpoints_verified: null, owner_generation: null, owner_uuid: null, observed_at: null, remaining_order_count: 0, remaining_position_count: 0 } })))
    const { result } = renderHook(() => useConnectionsController(), { wrapper: Wrapper })
    await waitFor(() => expect(result.current.slots.length, JSON.stringify(result.current.catalogue)).toBeGreaterThan(0))
    expect(result.current.slots[0]?.decisions.disconnect.allowed).toBe(true)
    await act(async () => {
      await result.current.set({ exchange: 'okx', connector: 'spot', values: { api_key: 'a', secret_key: 'b', passphrase: 'c' }, idempotencyKey: 'set-key' })
      await result.current.disconnect({ exchange: 'okx', connector: 'spot', idempotencyKey: 'disconnect-key' })
      await result.current.retryCleanup({ exchange: 'okx', connector: 'spot', idempotencyKey: 'retry-key' })
      await result.current.repairCleanupKey({ exchange: 'okx', connector: 'spot', values: { api_key: 'd', secret_key: 'e', passphrase: 'f' }, idempotencyKey: 'repair-key' })
    })
    expect(bodies.get('set')).toEqual({ body: { credentials: { api_key: 'a', secret_key: 'b', passphrase: 'c' }, expected_version: 3, expected_control_version: 9 }, key: 'set-key' })
    expect(bodies.get('disconnect')).toEqual({ body: { expected_version: 3, expected_control_version: 9 }, key: 'disconnect-key' })
    expect(bodies.get('retry')).toEqual({ body: { expected_version: 3, expected_control_version: 9, expected_operation_version: 7 }, key: 'retry-key' })
    expect(bodies.get('repair')).toEqual({ body: { operation_id: 'root-1', credentials: { api_key: 'd', secret_key: 'e', passphrase: 'f' }, expected_version: 3, expected_control_version: 9, expected_operation_version: 7 }, key: 'repair-key' })
    expect(result.current.slots[0]?.cleanup?.rootReceiptId).toBe('root-1')
  })

  it('blocks submit after permission changes and stale operational observation', async () => {
    installReads()
    let writes = 0
    server.use(http.post(`${baseUrl}/credentials/okx/spot/revoke`, () => { writes++; return HttpResponse.json({}, { status: 202 }) }))
    const { result, rerender } = renderHook(() => useConnectionsController(), { wrapper: Wrapper })
    await waitFor(() => expect(result.current.slots.length, JSON.stringify(result.current.catalogue)).toBeGreaterThan(0))
    canManage = false
    rerender()
    expect(() => result.current.disconnect({ exchange: 'okx', connector: 'spot', idempotencyKey: 'denied' })).toThrow('manage_credentials')
    expect(writes).toBe(0)
    canManage = true
    staleRuntime = true
    rerender()
    await waitFor(() => expect(result.current.slots[0]?.decisions.set.allowed).toBe(false))
    await act(async () => { await expect(result.current.set({ exchange: 'okx', connector: 'spot', values: { api_key: 'x', secret_key: 'y', passphrase: 'z' }, idempotencyKey: 'stale' })).rejects.toThrow('Refresh fresh bot status') })
    expect(writes).toBe(0)
  })

  it('rejects duplicate in-flight Set and keeps timeout unknown until explicit same-key review', async () => {
    installReads()
    let release: (() => void) | undefined
    let calls = 0
    server.use(http.post(`${baseUrl}/credentials/okx/spot`, async () => {
      calls++
      if (calls === 1) {
        await new Promise<void>((resolve) => { release = resolve })
        return HttpResponse.error()
      }
      return HttpResponse.json({ id: 'reviewed-retry', status: 'accepted', action: 'credentials_set' }, { status: 202 })
    }), http.get(`${baseUrl}/credentials/operations/reviewed-retry`, () => HttpResponse.json({ id: 'reviewed-retry', status: 'applying' })))
    const { result } = renderHook(() => useConnectionsController(), { wrapper: Wrapper })
    await waitFor(() => expect(result.current.slots.length, JSON.stringify(result.current.catalogue)).toBeGreaterThan(0))
    expect(result.current.slots[0]?.decisions.set.allowed).toBe(true)
    let first!: Promise<unknown>
    const credentials = { api_key: 'never-store-me', secret_key: 'secret', passphrase: 'pass' }
    act(() => { first = result.current.set({ exchange: 'okx', connector: 'spot', values: credentials, idempotencyKey: 'stable-key' }) })
    await waitFor(() => expect(calls).toBe(1))
    await act(async () => { await expect(result.current.set({ exchange: 'okx', connector: 'spot', values: { api_key: 'other', secret_key: 'secret', passphrase: 'pass' }, idempotencyKey: 'duplicate' })).rejects.toThrow('open or unknown operation') })
    act(() => release?.())
    await act(async () => { await expect(first).rejects.toBeDefined() })
    expect(window.localStorage.length).toBe(0)
    expect(result.current.decisionLocks[0]?.state).toBe('unknown')
    await act(async () => { await expect(result.current.set({ exchange: 'okx', connector: 'spot', values: { api_key: 'fresh', secret_key: 'fresh', passphrase: 'fresh' }, idempotencyKey: 'blocked-unknown' })).rejects.toThrow('unknown operation') })
    let review!: { canRetrySameRequest: boolean; idempotencyKey?: string }
    await act(async () => { review = await result.current.reviewUnknown({ exchange: 'okx', connector: 'spot', action: 'set' }) })
    expect(review).toEqual({ canRetrySameRequest: true, idempotencyKey: 'stable-key' })
    await act(async () => { await result.current.set({ exchange: 'okx', connector: 'spot', values: credentials, idempotencyKey: 'stable-key' }) })
    expect(calls).toBe(2)
  })

  it('keeps an accepted slot action locked until its receipt is terminal, then permits a fresh review', async () => {
    installReads()
    let receiptStatus = 'applying'
    let submissions = 0
    server.use(
      http.post(`${baseUrl}/credentials/okx/spot`, () => {
        submissions++
        return HttpResponse.json({ id: `accepted-${submissions}`, status: 'accepted', action: 'credentials_set' }, { status: 202 })
      }),
      http.get(`${baseUrl}/credentials/operations/:id`, ({ params }) => HttpResponse.json({ id: params.id, status: receiptStatus, action: 'credentials_set', created_at: '', updated_at: receiptStatus, effective_version: 3, expected_version: 3, error: null, session_id: null, payload: { action: 'credentials_set', actor_id: 'op', actor_kind: 'user', bot_id: 'bot-a', cleanup_operation_id: null, connector: 'spot', environment: 'demo', exchange: 'okx', expected_control_version: 9, expected_operation_version: null, expected_version: 3 }, result: { stage: 'loading', blocked_stage: null, reason_code: null, next_retry_at: null, cleanup_operation_id: null, credential_version: 3, operation_version: 7, operator_action_required: false, orders_terminal_verified: null, positions_flat_verified: null, ledger_confirmed: null, consumers_detached: null, account_verified: null, position_checkpoints_verified: null, owner_generation: null, owner_uuid: null, observed_at: null, remaining_order_count: 0, remaining_position_count: 0 } })),
    )
    const { result } = renderHook(() => useConnectionsController(), { wrapper: Wrapper })
    await waitFor(() => expect(result.current.slots[0]?.decisions.set.allowed).toBe(true))
    const request = { exchange: 'okx', connector: 'spot', values: { api_key: 'secret', secret_key: 'secret', passphrase: 'secret' }, idempotencyKey: 'first' }
    await act(async () => { await result.current.set(request) })
    await waitFor(() => expect(result.current.decisionLocks[0]?.state).toBe('accepted'))
    await act(async () => { await expect(result.current.set({ ...request, idempotencyKey: 'duplicate-new-key' })).rejects.toThrow('open or unknown operation') })
    expect(submissions).toBe(1)
    receiptStatus = 'succeeded'
    await waitFor(() => expect(result.current.decisionLocks).toHaveLength(0), { timeout: 3000 })
    await act(async () => { await result.current.set({ ...request, idempotencyKey: 'after-terminal' }) })
    expect(submissions).toBe(2)
  })

  it('keeps credential status attached to its exchange when requests resolve out of order', async () => {
    let releaseOkx: (() => void) | undefined
    server.use(
      http.get(`${baseUrl}/credentials`, () => HttpResponse.json({ ...catalogueData, exchanges: [
        ...catalogueData.exchanges,
        { ...catalogueData.exchanges[0], exchange: 'bybit', connectors: ['perpetual'] },
      ] })),
      http.get(`${baseUrl}/credentials/okx`, async () => {
        await new Promise<void>((resolve) => { releaseOkx = resolve })
        return HttpResponse.json(statusData)
      }),
      http.get(`${baseUrl}/credentials/bybit`, () => HttpResponse.json({ ...statusData, exchange: 'bybit', slots: [{ ...credentialSlot, connector: 'perpetual', label: 'Bybit perpetual' }] })),
      http.get(`${baseUrl}/status`, () => HttpResponse.json(runtimeData)),
      http.get(`${baseUrl}/credentials/operations/root-1`, () => HttpResponse.json({ id: 'root-1', status: 'applying', action: 'credentials_disconnect', created_at: '', updated_at: '', effective_version: 3, expected_version: 3, error: null, session_id: null, payload: { action: 'credentials_disconnect', actor_id: 'op', actor_kind: 'user', bot_id: 'bot-a', cleanup_operation_id: null, connector: 'spot', environment: 'demo', exchange: 'okx', expected_control_version: 9, expected_operation_version: null, expected_version: 3 }, result: { stage: 'cancelling_orders', blocked_stage: null, reason_code: null, next_retry_at: null, cleanup_operation_id: null, credential_version: 3, operation_version: 7, operator_action_required: false, orders_terminal_verified: null, positions_flat_verified: null, ledger_confirmed: null, consumers_detached: null, account_verified: null, position_checkpoints_verified: null, owner_generation: null, owner_uuid: null, observed_at: null, remaining_order_count: 1, remaining_position_count: 0 } })),
    )
    const { result } = renderHook(() => useConnectionsController(), { wrapper: Wrapper })
    await waitFor(() => expect(result.current.slots.map(({ exchange, connector }) => [exchange, connector])).toContainEqual(['bybit', 'perpetual']))
    expect(result.current.slots.map(({ exchange }) => exchange)).toEqual(['bybit'])
    await act(async () => { releaseOkx?.() })
    await waitFor(() => expect(result.current.slots.map(({ exchange, connector }) => [exchange, connector])).toEqual([
      ['okx', 'spot'], ['bybit', 'perpetual'],
    ]))
  })
  it('blocks connection changes while the shared recovery fence is active, even when the slot advertises permission', async () => {
    installReads()
    recoveryActive = true
    let writes = 0
    server.use(http.post(`${baseUrl}/credentials/okx/spot`, () => { writes++; return HttpResponse.json({}) }),
      http.post(`${baseUrl}/credentials/okx/spot/revoke`, () => { writes++; return HttpResponse.json({}) }))
    const { result } = renderHook(() => useConnectionsController(), { wrapper: Wrapper })
    await waitFor(() => expect(result.current.slots.length).toBeGreaterThan(0))
    expect(result.current.slots[0].decisions.set.reasonCode).toBe('recovery_in_progress')
    await act(async () => { await expect(result.current.set({ exchange: 'okx', connector: 'spot', values: { api_key: 'a', secret_key: 'b', passphrase: 'c' }, idempotencyKey: 'blocked' })).rejects.toThrow('Recovery running') })
    expect(() => result.current.disconnect({ exchange: 'okx', connector: 'spot', idempotencyKey: 'blocked' })).toThrow('Recovery running')
    expect(writes).toBe(0)
  })

  it('honors an operation-scoped wait deadline before any same-key credential POST', async () => {
    installReads()
    const calls: string[] = []
    server.use(http.post(`${baseUrl}/credentials/okx/spot`, ({ request }) => {
      calls.push(request.headers.get('Idempotency-Key') ?? '')
      return calls.length === 1 ? HttpResponse.json({ retry: 'wait', retry_after_seconds: 60 }, { status: 503 }) : HttpResponse.json({ ...credentialReceipt, id: 'wait-retry', status: 'accepted' }, { status: 202 })
    }), http.get(`${baseUrl}/credentials/operations/:id`, ({ params }) => HttpResponse.json({ ...credentialReceipt, id: params.id, status: 'applying' })))
    const { result } = renderHook(() => useConnectionsController(), { wrapper: Wrapper })
    await waitFor(() => expect(result.current.slots[0]?.decisions.set.allowed).toBe(true))
    const input = { exchange: 'okx', connector: 'spot', values: { api_key: 'a', secret_key: 'b', passphrase: 'c' }, idempotencyKey: 'wait-key' }
    await act(async () => { await expect(result.current.set(input)).rejects.toMatchObject({ status: 503 }) })
    await act(async () => { await result.current.reviewUnknown({ exchange: 'okx', connector: 'spot', action: 'set' }) })
    await act(async () => { await expect(result.current.set(input)).rejects.toThrow('operation_cooldown') })
    expect(calls).toEqual(['wait-key'])
    const deadline = result.current.decisionLocks[0].retryAt!
    const clock = vi.spyOn(Date, 'now').mockReturnValue(deadline + 1)
    try {
      await act(async () => { await result.current.set(input) })
      expect(calls).toEqual(['wait-key', 'wait-key'])
    } finally { clock.mockRestore() }
  })

})
