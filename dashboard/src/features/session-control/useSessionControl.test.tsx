import type { PropsWithChildren } from 'react'
import { act, renderHook, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { afterAll, describe, expect, it, vi } from 'vitest'
import { transferableAbortController } from 'node:util'
import { ApiProvider } from '../../core/api/ApiProvider'
import { AuthContext } from '../../core/auth/auth-context'
import { OperationalProvider } from '../operational-context/OperationalProvider'
import { contextPrincipal, contextStatus } from '../../test/mocks/contextFixtures'
import { server } from '../../test/mocks/server'
import { useSessionControl } from './useSessionControl'

const nativeController = transferableAbortController()
vi.stubGlobal('AbortController', nativeController.constructor)
vi.stubGlobal('AbortSignal', nativeController.signal.constructor)
afterAll(() => vi.unstubAllGlobals())

const baseUrl = 'https://session-control.example.test/api/v1/admin'
const apiSession = { baseUrl, cacheIdentity: 'operator:test', principalId: 'google:operator', getToken: () => 'token' }
const sessionStatus = {
  ...contextStatus,
  session: { id: 'session-1', anchors: {}, config_hash: 'cfg', expires_at: null, generation: 1, profile_id: 'follow_market',
    request: { duration_mode: 'timed', duration_seconds: null, market_mode: 'spot', profile_id: 'follow_market', profile_ids: null, references: null, selection: 'fixed', step_duration_seconds: null },
    started_at: '2026-11-11T00:00:00Z', status: 'running', step_expires_at: null, step_index: 0 },
  control_version: 4,
  actions: { ...contextStatus.actions, switch: { allowed: true, detail: 'Replace session', reason_code: null }, stop: { allowed: true, detail: 'Stop session', reason_code: null } },
}
const catalogue = {
  catalogue_hash: 'catalogue-a', market_modes: ['spot'], duration_modes: ['timed'], reporting_timezone: 'UTC', process_identity: null,
  catalogue: { allow_until_stopped: false, default_duration_seconds: 3600, max_duration_seconds: 86400, min_duration_seconds: 60,
    markets: {}, max_total_amount_quote: '100', max_controller_drawdown_quote: null, max_global_drawdown_quote: null,
    profiles: { follow_market: {} }, random: { profile_ids: ['follow_market'], step_duration_seconds: 600 }, reference_connector: 'okx', spot_outflow: null },
} as const
function Wrapper({ children }: PropsWithChildren) {
  return <ApiProvider session={apiSession}>
    <AuthContext.Provider value={{ status: 'authenticated', identity: contextPrincipal, error: null, login: async () => undefined, logout: () => undefined, apiSession }}>
      <OperationalProvider>{children}</OperationalProvider>
    </AuthContext.Provider>
  </ApiProvider>
}
function installReads(statusReply = sessionStatus) {
  let statusReads = 0
  server.use(
    http.get(`${baseUrl}/status`, () => { statusReads++; return HttpResponse.json(statusReply) }),
    http.get(`${baseUrl}/balances`, () => HttpResponse.json({ owner_available: true, accounts: [] })),
    http.get(`${baseUrl}/profiles`, () => HttpResponse.json(catalogue)),
    http.get(`${baseUrl}/commands/:id`, ({ params }) => HttpResponse.json({ id: params.id, action: 'switch', status: 'accepted', session_id: 'session-1', error: null })),
  )
  return () => statusReads
}

describe('session control lifecycle', () => {
  it('allows Stop during a pending Switch and refreshes its target version', async () => {
    const statusReads = installReads({ ...sessionStatus, control_version: 4 })
    const posts: Array<{ action: string; version: number }> = []
    let releaseSwitch: (() => void) | undefined
    server.use(http.post(`${baseUrl}/commands`, async ({ request }) => {
      const body = await request.json() as { action: string; expected_version: number }
      posts.push({ action: body.action, version: body.expected_version })
      if (body.action === 'switch') {
        await new Promise<void>((resolve) => { releaseSwitch = resolve })
      }
      return HttpResponse.json({ id: `cmd-${body.action}`, action: body.action, status: 'accepted', session_id: 'session-1', error: null }, { status: 202 })
    }))
    const { result } = renderHook(() => useSessionControl(), { wrapper: Wrapper })
    await waitFor(() => expect(result.current.profiles.isError || result.current.profiles.isSuccess).toBe(true))
    if (result.current.profiles.isError) throw result.current.profiles.error
    await waitFor(() => expect(result.current.status.phase).toBe('ready'))
    const reviewed = result.current.review('switch', { marketMode: 'spot', selection: 'fixed', profileId: 'follow_market', durationMode: 'timed', durationSeconds: 120 })
    expect(reviewed.errors).toEqual({})
    let switchRequest!: Promise<unknown>
    act(() => { switchRequest = result.current.submitReviewed(reviewed.review!) })
    await waitFor(() => expect(posts.some(({ action }) => action === 'switch')).toBe(true))
    await act(async () => { await result.current.stop() })
    expect(posts.find(({ action }) => action === 'stop')?.version).toBe(4)
    expect(statusReads()).toBeGreaterThanOrEqual(2)
    await act(async () => { releaseSwitch?.(); await switchRequest })
    expect(posts.map(({ action }) => action)).toEqual(['switch', 'stop'])
  })

  it('retries a timed-out command with its original body and idempotency key', async () => {
    installReads()
    const requests: Array<{ body: unknown; key: string | null }> = []
    let calls = 0
    server.use(http.post(`${baseUrl}/commands`, async ({ request }) => {
      requests.push({ body: await request.json(), key: request.headers.get('Idempotency-Key') })
      if (++calls === 1) return HttpResponse.error()
      return HttpResponse.json({ id: 'cmd-retried', action: 'switch', status: 'accepted', session_id: 'session-1', error: null }, { status: 202 })
    }))
    const { result } = renderHook(() => useSessionControl(), { wrapper: Wrapper })
    await waitFor(() => expect(result.current.profiles.isError || result.current.profiles.isSuccess).toBe(true))
    if (result.current.profiles.isError) throw result.current.profiles.error
    await waitFor(() => expect(result.current.status.phase).toBe('ready'))
    const reviewed = result.current.review('switch', { marketMode: 'spot', selection: 'fixed', profileId: 'follow_market', durationMode: 'timed', durationSeconds: 120 })
    await act(async () => { await expect(result.current.submitReviewed(reviewed.review!)).rejects.toThrow() })
    await waitFor(() => expect(result.current.pendingRetry).toBeDefined())
    await act(async () => { await result.current.retryPending() })
    expect(requests).toHaveLength(2)
    expect(requests[1]).toEqual(requests[0])
  })

  it('restores an accepted command reference after remount and does not replay or allow a new decision while pending', async () => {
    installReads()
    let posts = 0
    server.use(http.post(`${baseUrl}/commands`, async ({ request }) => {
      posts++
      const body = await request.json() as { action: string }
      return HttpResponse.json({ id: 'cmd-persisted', action: body.action, status: 'accepted', session_id: 'session-1', error: null }, { status: 202 })
    }))
    const first = renderHook(() => useSessionControl(), { wrapper: Wrapper })
    await waitFor(() => expect(first.result.current.profiles.isSuccess).toBe(true))
    const reviewed = first.result.current.review('switch', { marketMode: 'spot', selection: 'fixed', profileId: 'follow_market', durationMode: 'timed', durationSeconds: 120 })
    await act(async () => { await first.result.current.submitReviewed(reviewed.review!) })
    expect(posts).toBe(1)
    first.unmount()

    const restored = renderHook(() => useSessionControl(), { wrapper: Wrapper })
    await waitFor(() => expect(restored.result.current.trackedReceipts).toContainEqual({ kind: 'command', id: 'cmd-persisted' }))
    await waitFor(() => expect(restored.result.current.commandReceipt?.status).toBe('accepted'))
    expect(restored.result.current.commandBlocksNew).toBe(true)
    const nextReview = restored.result.current.review('switch', { marketMode: 'spot', selection: 'fixed', profileId: 'follow_market', durationMode: 'timed', durationSeconds: 120 })
    expect(() => restored.result.current.submitReviewed(nextReview.review!)).toThrow('prior session command is unresolved')
    expect(posts).toBe(1)
  })

})
