import { transferableAbortController } from 'node:util'
import { act, renderHook, waitFor } from '@testing-library/react'
import type { PropsWithChildren } from 'react'
import { afterAll, describe, expect, it, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import { AuthContext, type AuthContextValue } from '../../core/auth/auth-context'
import { ApiProvider, type ApiSession } from '../../core/api/ApiProvider'
import { server } from '../../test/mocks/server'
import { useAudit, useCommands, useFills, useOrderDetail, useOrders, useSessionConfiguration, useSessionEvents, useSessions } from './useHistoryQueries'

const nativeController = transferableAbortController()
vi.stubGlobal('AbortController', nativeController.constructor)
vi.stubGlobal('AbortSignal', nativeController.signal.constructor)

const baseUrl = 'https://history.example.test/api/v1/admin'
const session: ApiSession = { baseUrl, cacheIdentity: 'history-test', getToken: () => 'token' }
const auth: AuthContextValue = { status: 'authenticated', identity: { id: 'reader', email: null, kind: 'google', role: 'read', role_label: 'Read', permissions: { read: true, manage_accounts: false } }, error: null, login: async () => {}, logout: () => {}, apiSession: session }
function Wrapper({ children }: PropsWithChildren) { return <AuthContext.Provider value={auth}><ApiProvider session={session}>{children}</ApiProvider></AuthContext.Provider> }

afterAll(() => vi.unstubAllGlobals())

describe('history data hooks', () => {
  it('keeps filters on cursor pages and starts a fresh cursor chain when filters change', async () => {
    const requests: URL[] = []
    server.use(http.get(`${baseUrl}/orders`, ({ request }) => {
      const url = new URL(request.url)
      requests.push(url)
      return HttpResponse.json(url.searchParams.has('cursor')
        ? { items: [], next_cursor: null }
        : { items: [], next_cursor: 'opaque-next' })
    }))
    const { result, rerender } = renderHook(({ market }: { market: 'spot' | 'perpetual' }) => useOrders({ query: { market, limit: 10 } }), {
      initialProps: { market: 'spot' }, wrapper: Wrapper,
    })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    await act(async () => { await result.current.fetchNextPage() })
    rerender({ market: 'perpetual' })
    await waitFor(() => expect(result.current.data?.pages[0]).toBeDefined())
    expect(requests.map((url) => [url.searchParams.get('market'), url.searchParams.get('cursor')])).toEqual([
      ['spot', null], ['spot', 'opaque-next'], ['perpetual', null],
    ])
  })

  it('keeps cursor history on demand and scopes session events to the selected session', async () => {
    const paths: URL[] = []
    server.use(
      http.get(`${baseUrl}/fills`, ({ request }) => { const url = new URL(request.url); paths.push(url); return HttpResponse.json({ items: [], next_cursor: null }) }),
      http.get(`${baseUrl}/sessions`, () => HttpResponse.json({ items: [], next_cursor: null })),
      http.get(`${baseUrl}/sessions/:session_id/events`, ({ request }) => { paths.push(new URL(request.url)); return HttpResponse.json({ items: [], next_cursor: null }) }),
      http.get(`${baseUrl}/commands`, () => HttpResponse.json({ items: [], next_cursor: null })),
      http.get(`${baseUrl}/audit`, () => HttpResponse.json({ items: [], next_cursor: null })),
    )
    const { result } = renderHook(() => ({ fills: useFills({ query: { market: 'perpetual' } }),
      sessions: useSessions(), events: useSessionEvents('session/one', { source: 'scheduler' }),
      commands: useCommands({ query: { status: 'failed' } }), audit: useAudit({ query: { outcome: 'failed' } }) }), { wrapper: Wrapper })
    await waitFor(() => expect(result.current.audit.isSuccess && result.current.commands.isSuccess && result.current.events.isSuccess && result.current.sessions.isSuccess && result.current.fills.isSuccess).toBe(true))
    expect(paths.some((url) => url.pathname.includes('/sessions/session%2Fone/events') && url.searchParams.get('source') === 'scheduler')).toBe(true)
    expect(result.current.events.data?.pages).toHaveLength(1)
    expect(result.current.audit.access.allowed).toBe(true)
  })

  it('fetches selected row details only when IDs exist and does not poll them', async () => {
    const calls: string[] = []
    server.use(
      http.get(`${baseUrl}/orders/:id`, ({ params }) => { calls.push(`order:${params.id}`); return HttpResponse.json({ id: params.id }) }),
      http.get(`${baseUrl}/sessions/:session_id/configuration`, ({ params }) => { calls.push(`session:${params.session_id}`); return HttpResponse.json({ session_id: params.session_id }) }),
    )
    const { result, rerender } = renderHook(({ id }: { id?: string }) => ({ order: useOrderDetail(id), config: useSessionConfiguration(id) }), {
      initialProps: { id: undefined as string | undefined }, wrapper: Wrapper,
    })
    expect(result.current.order.fetchStatus).toBe('idle')
    expect(result.current.config.fetchStatus).toBe('idle')
    rerender({ id: 'order-selected' })
    await waitFor(() => expect(result.current.order.data).toEqual({ id: 'order-selected' }))
    await waitFor(() => expect(result.current.config.data).toEqual({ session_id: 'order-selected' }))
    expect(calls).toEqual(['order:order-selected', 'session:order-selected'])
    expect(calls).toHaveLength(2)
  })
})
