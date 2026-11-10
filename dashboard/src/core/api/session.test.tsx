import { transferableAbortController } from 'node:util'
import { act, renderHook, waitFor } from '@testing-library/react'
import { useQueryClient } from '@tanstack/react-query'
import type { PropsWithChildren } from 'react'
import { afterAll, describe, expect, it, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import { ApiProvider, type ApiSession } from './ApiProvider'
import { useAdminQuery } from './hooks'
import { useAdminInfiniteQuery } from './infinite'
import { server } from '../../test/mocks/server'
import { emptyOrders, recordedOrders, pendingCommand } from '../../test/mocks/adminFixtures'

const nativeController = transferableAbortController()
vi.stubGlobal('AbortController', nativeController.constructor)
vi.stubGlobal('AbortSignal', nativeController.signal.constructor)
afterAll(() => vi.unstubAllGlobals())
const baseUrl = 'https://session.example.test/api/v1/admin'
const sessionA: ApiSession = { baseUrl, cacheIdentity: 'user-a:session-1', getToken: () => 'token-a' }
function Wrapper({ children }: PropsWithChildren) { return <ApiProvider session={sessionA}>{children}</ApiProvider> }

describe('API session and pagination', () => {
  it('replaces the whole cache on identity/service changes and cancels the old request', async () => {
    let aborted = false
    let started = false
    let release: (() => void) | undefined
    server.use(http.get(`${baseUrl}/orders`, async ({ request }) => {
      if (request.headers.get('Authorization') === 'Bearer token-a') {
        started = true
        request.signal.addEventListener('abort', () => { aborted = true })
        await new Promise<void>((resolve) => { release = resolve })
        return HttpResponse.json(recordedOrders)
      }
      return HttpResponse.json(emptyOrders)
    }))
    let currentSession = sessionA
    const wrapper = ({ children }: PropsWithChildren) => <ApiProvider session={currentSession}>{children}</ApiProvider>
    const { result, rerender } = renderHook(() => ({ query: useAdminQuery('listOrders', {}, { poll: false }), client: useQueryClient() }), { wrapper })
    await waitFor(() => expect(started).toBe(true))
    const oldClient = result.current.client
    oldClient.setQueryData(['private'], 'old-user-data')
    currentSession = { ...sessionA, cacheIdentity: 'user-b:session-2', getToken: () => 'token-b' }
    rerender()
    await waitFor(() => expect(result.current.query.data).toEqual(emptyOrders))
    expect(result.current.client).not.toBe(oldClient)
    expect(oldClient.getQueryCache().getAll()).toHaveLength(0)
    expect(aborted).toBe(true)
    act(() => release?.())
    expect(result.current.query.data).toEqual(emptyOrders)
  })

  it('uses refreshed token callbacks without rebuilding a same-session cache', async () => {
    const seen: (string | null)[] = []
    server.use(http.get(`${baseUrl}/orders`, ({ request }) => {
      seen.push(request.headers.get('Authorization'))
      return HttpResponse.json(emptyOrders)
    }))
    let activeSession = sessionA
    const wrapper = ({ children }: PropsWithChildren) => <ApiProvider session={activeSession}>{children}</ApiProvider>
    const { result, rerender } = renderHook(() => useAdminQuery('listOrders', {}, { poll: false }), { wrapper })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    activeSession = { ...sessionA, getToken: () => 'refreshed-token' }
    rerender()
    await act(async () => { await result.current.refetch() })
    expect(seen).toEqual(['Bearer token-a', 'Bearer refreshed-token'])
  })

  it('follows bound cursors, deduplicates concurrent next-page reads and resets pages with filters', async () => {
    const seen: string[] = []
    server.use(http.get(`${baseUrl}/orders`, ({ request }) => {
      const url = new URL(request.url)
      seen.push(url.search)
      return HttpResponse.json(url.searchParams.get('market') === 'perpetual' || url.searchParams.has('cursor')
        ? emptyOrders : { ...recordedOrders, next_cursor: 'bound:cursor' })
    }))
    const { result, rerender } = renderHook(({ market }: { market: 'spot' | 'perpetual' }) =>
      useAdminInfiniteQuery('listOrders', { query: { market, limit: 20 } }),
    { initialProps: { market: 'spot' }, wrapper: Wrapper })
    await waitFor(() => expect(result.current.hasNextPage).toBe(true))
    await act(async () => {
      await Promise.all([result.current.fetchNextPage({ cancelRefetch: false }), result.current.fetchNextPage({ cancelRefetch: false })])
    })
    await waitFor(() => expect(result.current.data?.pages).toHaveLength(2))
    expect(result.current.hasNextPage).toBe(false)
    expect(seen).toHaveLength(2)
    expect(seen[1]).toContain('market=spot')
    expect(seen[1]).toContain('cursor=bound%3Acursor')
    rerender({ market: 'perpetual' })
    await waitFor(() => expect(result.current.data?.pages).toEqual([emptyOrders]))
  })

  it('can resume following a pending receipt from a retained ID after remount', async () => {
    const seen: string[] = []
    server.use(http.get(`${baseUrl}/commands/:id`, ({ params }) => {
      seen.push(String(params.id))
      return HttpResponse.json({ ...pendingCommand, status: 'succeeded' })
    }))
    const first = renderHook(() => useAdminQuery('getCommand', { path: { command_id: pendingCommand.id } }), { wrapper: Wrapper })
    await waitFor(() => expect(first.result.current.data?.status).toBe('succeeded'))
    first.unmount()
    const next = renderHook(() => useAdminQuery('getCommand', { path: { command_id: pendingCommand.id } }), { wrapper: Wrapper })
    await waitFor(() => expect(next.result.current.data?.status).toBe('succeeded'))
    expect(seen).toEqual([pendingCommand.id, pendingCommand.id])
  })
})
