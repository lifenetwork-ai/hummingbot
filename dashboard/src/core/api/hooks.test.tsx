import { transferableAbortController } from 'node:util'
import { act, renderHook, waitFor } from '@testing-library/react'
import { useQueryClient } from '@tanstack/react-query'
import type { PropsWithChildren } from 'react'
import { afterAll, describe, expect, it, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import { ApiProvider, type ApiSession } from './ApiProvider'
import { useAdminMutation, useAdminQuery, useCommandReceipt, useRecoveryReceipt, useReceiptInspection } from './hooks'
import { createAdminApi } from './admin'
import { createApiClient } from './client'
import { adminKeys, adminQueryOptions, pollingInterval } from './queries'
import { readTrackedReceipts, receiptStorageKey, rememberAcceptedReceipt, type ReceiptReference } from './receipt-tracking'
import { useTrackedReceipts } from './useTrackedReceipts'
import type { ReceiptScope } from './receipt-tracking'
import type { ReadResponse } from './admin'
import { createDashboardQueryClient } from './queryClient'
import { server } from '../../test/mocks/server'
import { apiFailure, emptyOrders, pendingCommand, recordedOrders } from '../../test/mocks/adminFixtures'
import { commandReceipt, credentialReceipt, reconciliationReceipt, recoveryCheckReceipt, recoveryResetReceipt } from '../../test/mocks/receiptFixtures'

// jsdom's AbortSignal is a different realm from Node fetch; use Node's pair.
const nativeController = transferableAbortController()
vi.stubGlobal('AbortController', nativeController.constructor)
vi.stubGlobal('AbortSignal', nativeController.signal.constructor)
afterAll(() => vi.unstubAllGlobals())

const baseUrl = 'https://query.example.test/api/v1/admin'
const session: ApiSession = { baseUrl, cacheIdentity: 'user-a:login-1', principalId: 'google:user-a', getToken: () => 'token-a' }
function Wrapper({ children }: PropsWithChildren) { return <ApiProvider session={session}>{children}</ApiProvider> }

describe('admin React Query integration', () => {
  it('captures the successful bot scope before awaiting the write response', async () => {
    let release: (() => void) | undefined
    let submitted = false
    server.use(http.post(`${baseUrl}/commands`, async () => {
      submitted = true
      await new Promise<void>((resolve) => { release = resolve })
      return HttpResponse.json(pendingCommand, { status: 202 })
    }))
    const { result } = renderHook(() => ({ mutation: useAdminMutation('submitCommand'), client: useQueryClient() }), { wrapper: Wrapper })
    result.current.client.setQueryData(adminKeys.query('getRuntimeStatus', {}), { bot_id: 'bot-original' } as ReadResponse<'getRuntimeStatus'>)
    const pending = result.current.mutation.mutateAsync({ body: pendingCommand.payload, idempotencyKey: 'scope-key' })
    await waitFor(() => expect(submitted).toBe(true))
    result.current.client.setQueryData(adminKeys.query('getRuntimeStatus', {}), { bot_id: 'bot-after-switch' } as ReadResponse<'getRuntimeStatus'>)
    await act(async () => { release?.(); await pending })
    const originalScope: ReceiptScope = { apiBaseUrl: baseUrl, principalId: 'google:user-a', botId: 'bot-original' }
    const changedScope: ReceiptScope = { ...originalScope, botId: 'bot-after-switch' }
    expect(readTrackedReceipts(originalScope)).toEqual([{ kind: 'command', id: pendingCommand.id }])
    expect(readTrackedReceipts(changedScope)).toEqual([])
  })

  it('ignores successful receipt responses when no trusted bot status has been observed', async () => {
    server.use(http.post(`${baseUrl}/commands`, () => HttpResponse.json(pendingCommand, { status: 202 })))
    const { result } = renderHook(() => useAdminMutation('submitCommand'), { wrapper: Wrapper })
    await act(async () => { await result.current.mutateAsync({ body: pendingCommand.payload, idempotencyKey: 'no-scope-key' }) })
    expect(window.localStorage.length).toBe(0)
  })

  it('does not use invalidated bot context to scope an accepted receipt', async () => {
    server.use(http.post(`${baseUrl}/commands`, () => HttpResponse.json(pendingCommand, { status: 202 })))
    const { result } = renderHook(() => ({ mutation: useAdminMutation('submitCommand'), client: useQueryClient() }), { wrapper: Wrapper })
    const key = adminKeys.query('getRuntimeStatus', {})
    result.current.client.setQueryData(key, { bot_id: 'possibly-old-bot' } as ReadResponse<'getRuntimeStatus'>)
    await result.current.client.invalidateQueries({ queryKey: key })
    await act(async () => { await result.current.mutation.mutateAsync({ body: pendingCommand.payload, idempotencyKey: 'stale-scope-key' }) })
    expect(window.localStorage.length).toBe(0)
  })

  it('resumes current-scope receipt references after status identity is confirmed', async () => {
    const scope: ReceiptScope = { apiBaseUrl: baseUrl, principalId: 'google:user-a', botId: 'bot-reloaded' }
    window.localStorage.setItem(receiptStorageKey(scope),
      JSON.stringify({ version: 1, receipts: [{ kind: 'command', id: 'cmd-1', acceptedAt: Date.now() }] }))
    server.use(http.get(`${baseUrl}/status`, () => HttpResponse.json({ bot_id: 'bot-reloaded' })))
    const { result } = renderHook(() => useTrackedReceipts(), { wrapper: Wrapper })
    await waitFor(() => expect(result.current).toEqual([{ kind: 'command', id: 'cmd-1' }]))
    expect(readTrackedReceipts(scope)).toEqual(result.current)
  })

  it('updates scoped references after same-tab acceptance and another tab clears browser storage', async () => {
    const scope: ReceiptScope = { apiBaseUrl: baseUrl, principalId: 'google:user-a', botId: 'bot-reloaded' }
    server.use(http.get(`${baseUrl}/status`, () => HttpResponse.json({ bot_id: scope.botId })))
    const { result } = renderHook(() => ({ refs: useTrackedReceipts(), status: useAdminQuery('getRuntimeStatus', {}, { poll: false }) }), { wrapper: Wrapper })
    await waitFor(() => expect(result.current.status.isSuccess).toBe(true))
    act(() => { rememberAcceptedReceipt(scope, { kind: 'command', id: 'cmd-new' }) })
    await waitFor(() => expect(result.current.refs).toEqual([{ kind: 'command', id: 'cmd-new' }]))
    act(() => {
      window.localStorage.clear()
      window.dispatchEvent(new StorageEvent('storage', { key: null }))
    })
    await waitFor(() => expect(result.current.refs).toEqual([]))
  })

  it('skipToken prevents even manual fetches when a receipt ID is missing', async () => {
    const { result } = renderHook(() => useCommandReceipt(undefined), { wrapper: Wrapper })
    expect(result.current.fetchStatus).toBe('idle')
    expect(result.current.data).toBeUndefined()
  })

  it.each([401, 409, 422])('returns mutation HTTP %s without replaying or invalidating accepted state', async (status) => {
    let calls = 0
    server.use(http.post(`${baseUrl}/commands`, () => {
      calls++
      return HttpResponse.json(apiFailure('control_version_changed', 'refresh_state'), { status })
    }))
    const { result } = renderHook(() => ({ mutation: useAdminMutation('submitCommand'), client: useQueryClient() }), { wrapper: Wrapper })
    result.current.client.setQueryData(adminKeys.query('listCommands', {}), {})
    await act(async () => { await expect(result.current.mutation.mutateAsync({ body: pendingCommand.payload, idempotencyKey: 'conflict-key' }))
      .rejects.toMatchObject({ status }) })
    expect(calls).toBe(1)
    expect(result.current.client.getQueryState(adminKeys.query('listCommands', {}))?.isInvalidated).toBe(false)
  })

  it('shares matching reads, preserves decimals, and isolates filter caches', async () => {
    let calls = 0
    server.use(http.get(`${baseUrl}/orders`, ({ request }) => {
      calls++
      return HttpResponse.json(new URL(request.url).searchParams.get('market') === 'spot' ? recordedOrders : emptyOrders)
    }))
    const { result } = renderHook(() => ({
      one: useAdminQuery('listOrders', { query: { market: 'spot' } }, { poll: false }),
      same: useAdminQuery('listOrders', { query: { market: 'spot' } }, { poll: false }),
      other: useAdminQuery('listOrders', { query: { market: 'perpetual' } }, { poll: false }),
    }), { wrapper: Wrapper })
    await waitFor(() => expect(result.current.one.isSuccess && result.current.other.isSuccess).toBe(true))
    expect(calls).toBe(2)
    expect(result.current.same.data).toEqual(recordedOrders)
    expect(result.current.other.data).toEqual(emptyOrders)
  })

  it('does not let an obsolete filter response replace the current page', async () => {
    let release: (() => void) | undefined
    let started = false
    server.use(http.get(`${baseUrl}/orders`, async ({ request }) => {
      if (new URL(request.url).searchParams.get('market') === 'spot') {
        started = true
        await new Promise<void>((resolve) => { release = resolve })
        return HttpResponse.json(recordedOrders)
      }
      return HttpResponse.json(emptyOrders)
    }))
    const { result, rerender } = renderHook(({ market }: { market: 'spot' | 'perpetual' }) =>
      useAdminQuery('listOrders', { query: { market } }, { poll: false }),
    { initialProps: { market: 'spot' }, wrapper: Wrapper })
    await waitFor(() => expect(started).toBe(true))
    rerender({ market: 'perpetual' })
    await waitFor(() => expect(result.current.data).toEqual(emptyOrders))
    act(() => release?.())
    expect(result.current.data).toEqual(emptyOrders)
  })

  it('disables dependent receipts until an ID is available', async () => {
    let calls = 0
    server.use(http.get(`${baseUrl}/commands/:id`, () => { calls++; return HttpResponse.json(pendingCommand) }))
    const { result, rerender } = renderHook(({ enabled }) => useAdminQuery('getCommand',
      { path: { command_id: 'fixture-command' } }, { enabled, poll: false }),
    { initialProps: { enabled: false }, wrapper: Wrapper })
    expect(result.current.fetchStatus).toBe('idle')
    expect(calls).toBe(0)
    rerender({ enabled: true })
    await waitFor(() => expect(result.current.data?.id).toBe('fixture-command'))
    expect(calls).toBe(1)
  })

  it('polls the requested receipt, stops at terminal failure and invalidates observed status', async () => {
    let receipts = 0
    let statusReads = 0
    server.use(
      http.get(`${baseUrl}/commands/:id`, () => HttpResponse.json({ ...pendingCommand,
        status: ++receipts === 1 ? 'accepted' : 'failed', error: receipts > 1 ? 'fixture_failure' : null })),
      http.get(`${baseUrl}/status`, () => { statusReads++; return HttpResponse.json({ marker: statusReads }) }),
    )
    const { result } = renderHook(() => ({
      receipt: useAdminQuery('getCommand', { path: { command_id: 'fixture-command' } }),
      status: useAdminQuery('getRuntimeStatus', {}, { poll: false }),
    }), { wrapper: Wrapper })
    await waitFor(() => expect(result.current.receipt.data?.status).toBe('failed'), { timeout: 2500 })
    await waitFor(() => expect(statusReads).toBe(2))
    expect(receipts).toBe(2)
    expect(pollingInterval('getCommand', result.current.receipt.data, null, 0)).toBe(false)
  })

  it.each([401, 403, 409, 422])('does not retry HTTP %s validation/auth/conflict errors', async (status) => {
    let calls = 0
    server.use(http.get(`${baseUrl}/orders`, () => { calls++; return HttpResponse.json(apiFailure('blocked', 'fix_request'), { status }) }))
    const client = createDashboardQueryClient()
    try {
      await expect(client.fetchQuery({ ...adminQueryOptions(createAdminApi(createApiClient({ baseUrl })), 'listOrders', {}), retryDelay: 0 })).rejects.toMatchObject({ status })
      expect(calls).toBe(1)
    } finally { client.clear() }
  })

  it('bounds transient retry and recovers without replacing exact amounts', async () => {
    let calls = 0
    server.use(http.get(`${baseUrl}/orders`, () => ++calls < 3
      ? HttpResponse.json(apiFailure('report_unavailable', 'wait'), { status: 503 })
      : HttpResponse.json(recordedOrders)))
    const client = createDashboardQueryClient()
    try {
      const data = await client.fetchQuery({ ...adminQueryOptions(createAdminApi(createApiClient({ baseUrl })), 'listOrders', {}), retryDelay: 0 })
      expect(data).toEqual(recordedOrders)
      expect(calls).toBe(3)
    } finally { client.clear() }
  })

  it('retains last-good data after persistent reporting failures and leaves other reads usable', async () => {
    let failed = false
    let calls = 0
    server.use(http.get(`${baseUrl}/orders`, () => {
      calls++
      return failed ? HttpResponse.json(apiFailure('unavailable', 'wait'), { status: 503 }) : HttpResponse.json(recordedOrders)
    }), http.get(`${baseUrl}/commands/:id`, () => HttpResponse.json(pendingCommand)))
    const client = createDashboardQueryClient()
    const options = { ...adminQueryOptions(createAdminApi(createApiClient({ baseUrl })), 'listOrders', {}), retryDelay: 0 }
    try {
      await client.fetchQuery(options)
      failed = true
      await expect(client.fetchQuery(options)).rejects.toMatchObject({ status: 503 })
      expect(calls).toBe(4)
      expect(client.getQueryData(adminKeys.query('listOrders', {}))).toEqual(recordedOrders)
      await expect(client.fetchQuery(adminQueryOptions(createAdminApi(createApiClient({ baseUrl })), 'getCommand', { path: { command_id: 'fixture-command' } }))).resolves.toEqual(pendingCommand)
    } finally { client.clear() }
  })

  it('prevents duplicate in-flight writes, keeps idempotency keys and invalidates on acceptance', async () => {
    let posts = 0
    let release: (() => void) | undefined
    server.use(http.post(`${baseUrl}/commands`, async ({ request }) => {
      posts++
      expect(request.headers.get('Idempotency-Key')).toBe('stable-key')
      await new Promise<void>((resolve) => { release = resolve })
      return HttpResponse.json(pendingCommand, { status: 202 })
    }))
    const { result } = renderHook(() => ({ mutation: useAdminMutation('submitCommand'), client: useQueryClient() }), { wrapper: Wrapper })
    result.current.client.setQueryData(adminKeys.query('listCommands', {}), { items: [], next_cursor: null })
    const input = { body: pendingCommand.payload, idempotencyKey: 'stable-key' }
    let first: Promise<unknown>
    act(() => { first = result.current.mutation.mutateAsync(input) })
    await waitFor(() => expect(posts).toBe(1))
    await expect(result.current.mutation.mutateAsync(input)).rejects.toThrow('already pending')
    await act(async () => { release?.(); await first })
    await waitFor(() => expect(result.current.mutation.data?.status).toBe('accepted'))
    expect(result.current.client.getQueryState(adminKeys.query('listCommands', {}))?.isInvalidated).toBe(true)
    expect(posts).toBe(1)
  })

  it('never automatically retries writes; explicit recovery reuses the original key', async () => {
    const keys: (string | null)[] = []
    server.use(http.post(`${baseUrl}/commands`, ({ request }) => {
      keys.push(request.headers.get('Idempotency-Key'))
      return keys.length === 1 ? HttpResponse.json(apiFailure('unknown_outcome', 'same_request'), { status: 503 }) : HttpResponse.json(pendingCommand, { status: 202 })
    }))
    const { result } = renderHook(() => useAdminMutation('submitCommand'), { wrapper: Wrapper })
    const input = { body: pendingCommand.payload, idempotencyKey: 'retry-key' }
    await act(async () => { await expect(result.current.mutateAsync(input)).rejects.toMatchObject({ status: 503 }) })
    expect(keys).toEqual(['retry-key'])
    await act(async () => { await result.current.mutateAsync(input) })
    expect(keys).toEqual(['retry-key', 'retry-key'])
  })

  it('keeps credential bodies out of mutation variables and cached state', async () => {
    server.use(http.post(`${baseUrl}/credentials/okx/okx`, () => HttpResponse.json({ id: 'credential-receipt', status: 'accepted' }, { status: 202 })))
    const { result } = renderHook(() => ({ mutation: useAdminMutation('setExchangeCredentials'), client: useQueryClient() }), { wrapper: Wrapper })
    await act(async () => { await result.current.mutation.mutateAsync({ path: { exchange: 'okx', connector: 'okx' },
      body: { credentials: { api_key: 'SECRET-FIXTURE-KEY' }, expected_version: 0, expected_control_version: 0 }, idempotencyKey: 'secret-operation' }) })
    expect(JSON.stringify(result.current.client.getMutationCache().getAll().map((item) => item.state))).not.toContain('SECRET-FIXTURE-KEY')
  })

  it('inspects read-only receipts without replay and isolates cross-reference queries', async () => {
    let commandGets = 0
    let credentialGets = 0
    let posts = 0
    server.use(
      http.get(`${baseUrl}/commands/:id`, ({ params }) => {
        commandGets++
        return HttpResponse.json({ ...commandReceipt, id: String(params.id), status: 'accepted' })
      }),
      http.get(`${baseUrl}/credentials/operations/:id`, ({ params }) => {
        credentialGets++
        return HttpResponse.json({ ...credentialReceipt, id: String(params.id) })
      }),
      http.post(`${baseUrl}/*`, () => {
        posts++
        return HttpResponse.json({}, { status: 400 })
      }),
    )

    // Initially null reference -> no calls, returns null
    const { result, rerender } = renderHook(
      ({ ref }: { ref: ReceiptReference | null }) => useReceiptInspection(ref),
      { initialProps: { ref: null as ReceiptReference | null }, wrapper: Wrapper }
    )
    expect(result.current).toBeNull()
    expect(commandGets).toBe(0)
    expect(credentialGets).toBe(0)

    // Select command reference
    rerender({ ref: { kind: 'command', id: 'cmd-inspection-1' } })
    await waitFor(() => expect(result.current?.phase).toBe('ready'))
    expect(result.current?.status).toBe('accepted')
    expect(result.current?.outcome).toEqual({ kind: 'command', action: 'stop', sessionId: 'fixture-session', error: null })
    expect(commandGets).toBe(1)
    expect(credentialGets).toBe(0)

    // Switch to credential reference -> does not leak previous command data
    rerender({ ref: { kind: 'credential', id: 'cred-inspection-2' } })
    await waitFor(() => expect(result.current?.reference.kind).toBe('credential'))
    await waitFor(() => expect(result.current?.phase).toBe('ready'))
    expect(result.current?.status).toBe('failed')
    expect(result.current?.detail).toBe('cleanup_pending')
    expect(result.current?.outcome).toMatchObject({ kind: 'credential', action: 'credentials_disconnect', stage: 'exchange_cleanup', operatorActionRequired: true })
    expect(credentialGets).toBe(1)

    // Deselect -> returns null
    rerender({ ref: null })
    expect(result.current).toBeNull()

    // No POST operations were ever called
    expect(posts).toBe(0)
  })

  it('projects reconciliation state and recovery check/reset outcome without confirmation tokens', async () => {
    const { result: idleRecovery } = renderHook(() => useRecoveryReceipt(undefined), { wrapper: Wrapper })
    expect(idleRecovery.current.fetchStatus).toBe('idle')

    server.use(
      http.get(`${baseUrl}/reconciliation/:id`, () => HttpResponse.json(reconciliationReceipt)),
      http.get(`${baseUrl}/recovery/operations/:id`, ({ params }) => HttpResponse.json(
        params.id === 'recovered' ? recoveryResetReceipt : recoveryCheckReceipt,
      )),
    )

    const { result, rerender } = renderHook(
      ({ ref }: { ref: ReceiptReference | null }) => useReceiptInspection(ref),
      { initialProps: { ref: { kind: 'reconciliation', id: 'rec-1' } as ReceiptReference | null }, wrapper: Wrapper }
    )
    await waitFor(() => expect(result.current?.phase).toBe('ready'))
    expect(result.current?.status).toBe('succeeded')
    expect(result.current?.outcome).toEqual({ kind: 'reconciliation', state: 'mismatch', reasons: ['exchange_order_missing_locally'] })

    rerender({ ref: { kind: 'recovery', id: 'recov-1' } })
    await waitFor(() => expect(result.current?.reference.kind).toBe('recovery'))
    await waitFor(() => expect(result.current?.phase).toBe('ready'))
    expect(result.current?.status).toBe('succeeded')
    expect(result.current?.outcome).toEqual({ kind: 'recovery', action: 'recovery_check', stage: 'review_required', canRecover: false,
      blockers: [{ code: 'review_required', detail: 'Review proposed repair', next_action: 'review' }] })
    expect(JSON.stringify(result.current?.outcome)).not.toContain('SECRET-FIXTURE-CONFIRMATION-TOKEN')

    rerender({ ref: { kind: 'recovery', id: 'recovered' } })
    await waitFor(() => expect(result.current?.outcome).toEqual({ kind: 'recovery', action: 'recovery_reset', stage: 'recovered', canRecover: true, blockers: [] }))
    expect(JSON.stringify(result.current?.outcome)).not.toContain('SECRET-FIXTURE-RESET-TOKEN')
  })

  it('preserves incomplete reconciliation outcome separately from successful processing status', async () => {
    const incomplete = { ...reconciliationReceipt, report: { ...reconciliationReceipt.report, state: 'incomplete' as const,
      markets: [{ ...reconciliationReceipt.report.markets![0], state: 'incomplete' as const, complete: false, reasons: ['exchange_page_limit_reached'] }] } }
    server.use(http.get(`${baseUrl}/reconciliation/:id`, () => HttpResponse.json(incomplete)))
    const { result } = renderHook(() => useReceiptInspection({ kind: 'reconciliation', id: 'incomplete-check' }), { wrapper: Wrapper })
    await waitFor(() => expect(result.current?.phase).toBe('ready'))
    expect(result.current?.status).toBe('succeeded')
    expect(result.current?.outcome).toEqual({ kind: 'reconciliation', state: 'incomplete', reasons: ['exchange_page_limit_reached'] })
  })

  it('keeps the selected receipt isolated when an older inspection response arrives late', async () => {
    let releaseCommand!: () => void
    let commandStarted = false
    server.use(
      http.get(`${baseUrl}/commands/:id`, async () => {
        commandStarted = true
        await new Promise<void>((resolve) => { releaseCommand = resolve })
        return HttpResponse.json(commandReceipt)
      }),
      http.get(`${baseUrl}/credentials/operations/:id`, () => HttpResponse.json(credentialReceipt)),
    )
    const { result, rerender } = renderHook(
      ({ ref }: { ref: ReceiptReference }) => useReceiptInspection(ref),
      { initialProps: { ref: { kind: 'command', id: 'slow-command' } as ReceiptReference }, wrapper: Wrapper },
    )
    await waitFor(() => expect(commandStarted).toBe(true))
    rerender({ ref: { kind: 'credential', id: 'selected-credential' } })
    await waitFor(() => expect(result.current?.outcome?.kind).toBe('credential'))
    act(() => releaseCommand())
    await waitFor(() => expect(result.current?.reference).toEqual({ kind: 'credential', id: 'selected-credential' }))
    expect(result.current?.status).toBe('failed')
    expect(result.current?.outcome).toMatchObject({ kind: 'credential', action: 'credentials_disconnect' })
  })
})
