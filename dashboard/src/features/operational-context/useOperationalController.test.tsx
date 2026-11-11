import { transferableAbortController } from 'node:util'
import { act, renderHook, waitFor } from '@testing-library/react'
import type { PropsWithChildren } from 'react'
import { http, HttpResponse } from 'msw'
import { afterAll, describe, expect, it, vi } from 'vitest'
import { ApiProvider } from '../../core/api/ApiProvider'
import { contextPrincipal, contextStatus } from '../../test/mocks/contextFixtures'
import { staleBalances } from '../../test/mocks/adminFixtures'
import { server } from '../../test/mocks/server'
import { useOperationalController } from './useOperationalController'

vi.mock('../../core/auth/auth-context', () => ({ useAuth: () => ({ identity: contextPrincipal }) }))
// Match Node fetch's AbortSignal realm, as in the core API integration tests.
const controller = transferableAbortController()
vi.stubGlobal('AbortController', controller.constructor)
vi.stubGlobal('AbortSignal', controller.signal.constructor)
afterAll(() => vi.unstubAllGlobals())
const baseUrl = 'https://context.test/api/v1/admin'
function wrapper({ children }: PropsWithChildren) {
  return <ApiProvider session={{ baseUrl, cacheIdentity: 'context-user:1' }}>{children}</ApiProvider>
}

describe('operational context controller', () => {
  it('loads sources independently and refreshes denied availability from backend', async () => {
    let canStop = true
    server.use(
      http.get(`${baseUrl}/status`, () => HttpResponse.json({ ...contextStatus,
        actions: { stop: { allowed: canStop, reason_code: canStop ? null : 'session_not_active', detail: 'Current availability' } } })),
      http.get(`${baseUrl}/balances`, () => HttpResponse.json(staleBalances)),
    )
    const { result } = renderHook(useOperationalController, { wrapper })
    expect(result.current.status.phase).toBe('loading')
    await waitFor(() => expect(result.current.balances.phase).toBe('ready'))
    await waitFor(() => expect(result.current.actions.stop.allowed).toBe(true))
    expect(result.current.observationState).toBe('fresh')
    expect(result.current.accounts[0].observation).toBe('stale')
    canStop = false
    await act(async () => { await result.current.refreshStatus() })
    await waitFor(() => expect(result.current.actions.stop).toMatchObject({ allowed: false, reasonCode: 'session_not_active' }))
  })

  it('retains last-good data on a persistent status error without granting cached availability', async () => {
    let fail = false
    server.use(
      http.get(`${baseUrl}/status`, () => fail ? HttpResponse.json({ detail: 'Unavailable' }, { status: 500 }) : HttpResponse.json(contextStatus)),
      http.get(`${baseUrl}/balances`, () => HttpResponse.json(staleBalances)),
    )
    const { result } = renderHook(useOperationalController, { wrapper })
    await waitFor(() => expect(result.current.actions.stop.allowed).toBe(true))
    fail = true
    await act(async () => { await result.current.refreshStatus() })
    expect(result.current.botId).toBe('bot-a')
    await waitFor(() => expect(result.current.status.phase).toBe('error'))
    expect(result.current.observationState).toBe('stale')
    expect(result.current.actions.stop.allowed).toBe(false)
    expect(result.current.balances.phase).toBe('ready')
    fail = false
    await act(async () => { await result.current.refreshStatus() })
    await waitFor(() => expect(result.current.actions.stop.allowed).toBe(true))
    expect(result.current.observationState).toBe('fresh')
  })
})
