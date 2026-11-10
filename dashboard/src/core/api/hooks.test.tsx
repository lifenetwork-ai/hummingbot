import { transferableAbortController } from 'node:util'
import { act, renderHook, waitFor } from '@testing-library/react'
import { useQueryClient } from '@tanstack/react-query'
import type { PropsWithChildren } from 'react'
import { afterAll, describe, expect, it, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import { ApiProvider, type ApiSession } from './ApiProvider'
import { useAdminMutation, useAdminQuery, useCommandReceipt } from './hooks'
import { createAdminApi } from './admin'
import { createApiClient } from './client'
import { adminKeys, adminQueryOptions, pollingInterval } from './queries'
import { createDashboardQueryClient } from './queryClient'
import { server } from '../../test/mocks/server'
import { apiFailure, emptyOrders, pendingCommand, recordedOrders } from '../../test/mocks/adminFixtures'

// jsdom's AbortSignal is a different realm from Node fetch; use Node's pair.
const nativeController = transferableAbortController()
vi.stubGlobal('AbortController', nativeController.constructor)
vi.stubGlobal('AbortSignal', nativeController.signal.constructor)
afterAll(() => vi.unstubAllGlobals())

const baseUrl = 'https://query.example.test/api/v1/admin'
const session: ApiSession = { baseUrl, cacheIdentity: 'user-a:login-1', getToken: () => 'token-a' }
function Wrapper({ children }: PropsWithChildren) { return <ApiProvider session={session}>{children}</ApiProvider> }

describe('admin React Query integration', () => {
  it('skipToken prevents even manual fetches when a receipt ID is missing', async () => {
    const { result } = renderHook(() => useCommandReceipt(undefined), { wrapper: Wrapper })
    expect(result.current.fetchStatus).toBe('idle')
    expect(result.current.data).toBeUndefined()
  })

  it.each([409, 422])('returns mutation HTTP %s without retrying or invalidating accepted state', async (status) => {
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
})
