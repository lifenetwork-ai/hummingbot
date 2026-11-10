import { act, renderHook, waitFor } from '@testing-library/react'
import type { PropsWithChildren } from 'react'
import { http, HttpResponse } from 'msw'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AuthProvider } from './AuthProvider'
import { useAuth } from './auth-context'
import { server } from '../../test/mocks/server'

const base = '*/api/v1/admin'
const requestBase = 'http://localhost/api/v1/admin'
const identity = { id: 'google:123', email: 'admin@example.com', kind: 'google', role: 'admin', role_label: 'Admin', permissions: { read: true } }
function wrapper({ children }: PropsWithChildren) { return <AuthProvider>{children}</AuthProvider> }

afterEach(() => vi.useRealTimers())

describe('AuthProvider', () => {
  it('authenticates, exposes a session token only in memory, and logs out', async () => {
    const calls: { path: string; authorization: string | null; credentials: RequestCredentials | undefined }[] = []
    server.use(
      http.post(`${base}/access/login`, ({ request }) => { calls.push({ path: new URL(request.url).pathname, authorization: request.headers.get('authorization'), credentials: request.credentials }); return HttpResponse.json(identity) }),
      http.get(`${base}/access/me`, ({ request }) => { calls.push({ path: new URL(request.url).pathname, authorization: request.headers.get('authorization'), credentials: request.credentials }); return HttpResponse.json(identity) }),
    )
    const { result } = renderHook(() => useAuth(), { wrapper })
    await act(async () => { await result.current.login('google-id-token') })
    expect(result.current.status).toBe('authenticated')
    expect(result.current.identity).toEqual(identity)
    expect(result.current.apiSession?.getToken?.()).toBe('google-id-token')
    expect(calls.map((call) => [call.path, call.authorization])).toEqual([
      ['/api/v1/admin/access/login', 'Bearer google-id-token'], ['/api/v1/admin/access/me', 'Bearer google-id-token'],
    ])
    expect(calls.every((call) => call.credentials === 'omit')).toBe(true)
    act(() => result.current.logout())
    expect(result.current.status).toBe('anonymous')
    expect(result.current.apiSession).toBeUndefined()
  })

  it('shares concurrent login work and presents denied/network errors', async () => {
    let calls = 0
    server.use(http.post(`${base}/access/login`, async () => { calls++; await new Promise((resolve) => setTimeout(resolve, 5)); return HttpResponse.json({ error: 'account_not_allowed' }, { status: 403 }) }))
    const { result } = renderHook(() => useAuth(), { wrapper })
    await act(async () => { await Promise.all([result.current.login('token'), result.current.login('token')]) })
    expect(calls).toBe(1)
    expect(result.current.status).toBe('anonymous')
    expect(result.current.error).toContain('account_not_allowed')
  })

  it('does not restore a late login response after logout', async () => {
    let release!: () => void
    server.use(
      http.post(`${base}/access/login`, async () => { await new Promise<void>((resolve) => { release = resolve }); return HttpResponse.json(identity) }),
      http.get(`${base}/access/me`, () => HttpResponse.json(identity)),
    )
    const { result } = renderHook(() => useAuth(), { wrapper })
    let pending!: Promise<void>
    act(() => { pending = result.current.login('token') })
    await waitFor(() => expect(release).toBeTypeOf('function'))
    act(() => result.current.logout())
    await act(async () => { release(); await pending })
    expect(result.current.status).toBe('anonymous')
    expect(result.current.apiSession).toBeUndefined()
  })

  it('clears the session on 401 and refreshes identity after a permission 403', async () => {
    let permission = false
    const updatedIdentity = { ...identity, role: 'read', role_label: 'Read only' }
    server.use(
      http.post(`${base}/access/login`, () => HttpResponse.json(identity)),
      http.get(`${base}/access/me`, () => HttpResponse.json(permission ? updatedIdentity : identity)),
      http.get(`${base}/private`, () => permission ? HttpResponse.json({ error: 'forbidden' }, { status: 403 }) : HttpResponse.json({ ok: true })),
      http.get(`${base}/expired`, () => HttpResponse.json({ error: 'authentication_required' }, { status: 401 })),
    )
    const { result } = renderHook(() => useAuth(), { wrapper })
    await act(async () => { await result.current.login('token') })
    const firstSession = result.current.apiSession!
    permission = true
    await act(async () => { await firstSession.fetch!(`${base}/private`) })
    await waitFor(() => expect(result.current.identity?.role).toBe('read'))
    expect(result.current.apiSession?.cacheIdentity).not.toBe(firstSession.cacheIdentity)
    const activeSession = result.current.apiSession!
    await act(async () => { await activeSession.fetch!(`${base}/expired`) })
    expect(result.current.status).toBe('anonymous')
  })

  it('allows an explicit retry after transient /me failure and does not retry automatically', async () => {
    let identityCalls = 0
    server.use(
      http.post(`${base}/access/login`, () => HttpResponse.json(identity)),
      http.get(`${base}/access/me`, () => { identityCalls += 1; return identityCalls === 1 ? HttpResponse.json({ error: 'temporary' }, { status: 503 }) : HttpResponse.json(identity) }),
    )
    const { result } = renderHook(() => useAuth(), { wrapper })
    await act(async () => { await result.current.login('token') })
    expect(result.current.status).toBe('anonymous')
    expect(result.current.error).toContain('temporary')
    expect(identityCalls).toBe(1)
    await act(async () => { await result.current.login('token') })
    expect(result.current.status).toBe('authenticated')
    expect(identityCalls).toBe(2)
  })

  it('rejects malformed and backend-expired credentials without creating a session', async () => {
    let calls = 0
    server.use(http.post(`${base}/access/login`, ({ request }) => {
      calls += 1
      return HttpResponse.json({ error: request.headers.get('authorization')?.includes('expired') ? 'token_expired' : 'authentication_required' }, { status: 401 })
    }))
    const { result } = renderHook(() => useAuth(), { wrapper })
    await act(async () => { await result.current.login('malformed') })
    expect(result.current.status).toBe('anonymous')
    expect(result.current.apiSession).toBeUndefined()
    expect(result.current.error).toContain('authentication_required')
    await act(async () => { await result.current.login('expired-credential') })
    expect(result.current.error).toContain('token_expired')
    expect(calls).toBe(2)
  })

  it('ignores a stale API 401 after a new session has been established', async () => {
    let releaseOld!: () => void
    let firstProtectedCall = true
    server.use(
      http.post(`${base}/access/login`, () => HttpResponse.json(identity)),
      http.get(`${base}/access/me`, () => HttpResponse.json(identity)),
      http.get(`${base}/delayed`, async ({ request }) => {
        if (request.headers.get('authorization') === 'Bearer old-token' && firstProtectedCall) {
          firstProtectedCall = false
          await new Promise<void>((resolve) => { releaseOld = resolve })
          return HttpResponse.json({ error: 'authentication_required' }, { status: 401 })
        }
        return HttpResponse.json({ ok: true })
      }),
    )
    const { result } = renderHook(() => useAuth(), { wrapper })
    await act(async () => { await result.current.login('old-token') })
    const oldSession = result.current.apiSession!
    let oldRequest!: Promise<Response>
    act(() => { oldRequest = oldSession.fetch!(`${requestBase}/delayed`, { headers: { Authorization: 'Bearer old-token' } }) })
    await waitFor(() => expect(releaseOld).toBeTypeOf('function'))
    act(() => result.current.logout())
    await act(async () => { await result.current.login('new-token') })
    await act(async () => { releaseOld(); await oldRequest })
    expect(result.current.status).toBe('authenticated')
    expect(result.current.apiSession?.getToken?.()).toBe('new-token')
  })

  it('logs out when /me confirms a disabled account', async () => {
    let disabled = false
    server.use(
      http.post(`${base}/access/login`, () => HttpResponse.json(identity)),
      http.get(`${base}/access/me`, () => disabled ? HttpResponse.json({ error: 'account_disabled' }, { status: 403 }) : HttpResponse.json(identity)),
      http.get(`${base}/private`, () => { disabled = true; return HttpResponse.json({ error: 'forbidden' }, { status: 403 }) }),
    )
    const { result } = renderHook(() => useAuth(), { wrapper })
    await act(async () => { await result.current.login('token') })
    const session = result.current.apiSession!
    await act(async () => { await session.fetch!(`${base}/private`) })
    expect(result.current.status).toBe('anonymous')
  })

  it('expires an authenticated session at the JWT exp time', async () => {
    vi.useFakeTimers()
    const future = Math.floor((Date.now() + 2_000) / 1000)
    const token = `header.${btoa(JSON.stringify({ exp: future }))}.sig`
    server.use(
      http.post(`${base}/access/login`, () => HttpResponse.json(identity)),
      http.get(`${base}/access/me`, () => HttpResponse.json(identity)),
    )
    const { result } = renderHook(() => useAuth(), { wrapper })
    await act(async () => { await result.current.login(token) })
    expect(result.current.status).toBe('authenticated')
    await act(async () => { vi.advanceTimersByTime(2_001) })
    expect(result.current.status).toBe('anonymous')
  })
})
