import { act, renderHook, waitFor } from '@testing-library/react'
import { StrictMode, type PropsWithChildren } from 'react'
import { http, HttpResponse } from 'msw'
import { describe, expect, it, vi } from 'vitest'
import { AuthProvider } from './AuthProvider'
import { useAuth } from './auth-context'
import { authTokenStorageKey } from './auth-persistence'
import { server } from '../../test/mocks/server'

const base = '*/api/v1/admin'
const key = authTokenStorageKey('/api/v1/admin', undefined)
const identity = { id: 'google:persisted', kind: 'google', email: 'operator@example.test', role: 'write', role_label: 'Operator', permissions: { read: true, operate: true } }
const jwt = (subject = 'first', exp = Math.floor(Date.now() / 1000) + 3600) => `header.${btoa(JSON.stringify({ sub: subject, exp }))}.signature`
function wrapper({ children }: PropsWithChildren) { return <AuthProvider>{children}</AuthProvider> }
function storageChanged(value: string | null) {
  if (value === null) localStorage.removeItem(key)
  else localStorage.setItem(key, value)
  window.dispatchEvent(new StorageEvent('storage', { key, newValue: value }))
}

describe('persisted Google sign-in', () => {
  it('stores a verified login and restores a remount through GET /me without POST replay', async () => {
    const token = jwt()
    let posts = 0
    let reads = 0
    server.use(
      http.post(`${base}/access/login`, () => { posts++; return HttpResponse.json(identity) }),
      http.get(`${base}/access/me`, ({ request }) => {
        reads++
        expect(request.headers.get('authorization')).toBe(`Bearer ${token}`)
        return HttpResponse.json(identity)
      }),
    )
    const first = renderHook(useAuth, { wrapper })
    await act(async () => { await first.result.current.login(token) })
    console.log('DEBUG FIRST RESULT:', {
      status: first.result.current.status,
      error: first.result.current.error,
      identity: first.result.current.identity,
      token,
      stored: localStorage.getItem(key),
    })
    expect(localStorage.getItem(key)).toBe(token)
    first.unmount()
    const next = renderHook(useAuth, { wrapper })
    await waitFor(() => expect(next.result.current.status).toBe('authenticated'))
    expect(next.result.current.identity).toEqual(identity)
    expect(next.result.current.apiSession?.getToken?.()).toBe(token)
    expect(posts).toBe(1)
    expect(reads).toBe(2)
  })

  it.each(['malformed', 'expired', 'empty'] as const)('removes %s storage without attempting backend authentication', async (kind) => {
    localStorage.setItem(key, kind === 'expired' ? jwt('expired', Math.floor(Date.now() / 1000) - 1) : kind === 'empty' ? '' : 'not-a-jwt')
    let calls = 0
    server.use(http.get(`${base}/access/me`, () => { calls++; return HttpResponse.json(identity) }))
    const { result } = renderHook(useAuth, { wrapper })
    await waitFor(() => expect(result.current.status).toBe('anonymous'))
    expect(localStorage.getItem(key)).toBeNull()
    expect(calls).toBe(0)
  })

  it('does not publish identity or API access until restoration completes, and logout fences its response', async () => {
    const token = jwt()
    localStorage.setItem(key, token)
    let release!: () => void
    server.use(http.get(`${base}/access/me`, async () => {
      await new Promise<void>((resolve) => { release = resolve })
      return HttpResponse.json(identity)
    }))
    const { result } = renderHook(useAuth, { wrapper })
    await waitFor(() => expect(release).toBeTypeOf('function'))
    expect(result.current.status).toBe('restoring')
    expect(result.current.apiSession).toBeUndefined()
    expect(result.current.identity).toBeNull()
    act(() => { result.current.logout() })
    await act(async () => { release() })
    expect(result.current.status).toBe('anonymous')
    expect(localStorage.getItem(key)).toBeNull()
  })

  it.each([401, 403])('clears persisted credentials rejected with HTTP %s', async (status) => {
    localStorage.setItem(key, jwt())
    server.use(http.get(`${base}/access/me`, () => HttpResponse.json({ detail: 'Access revoked' }, { status })))
    const { result } = renderHook(useAuth, { wrapper })
    await waitFor(() => expect(result.current.status).toBe('anonymous'))
    expect(result.current.error).toContain('Access revoked')
    expect(localStorage.getItem(key)).toBeNull()
  })

  it('keeps a credential after transient restoration failure and retries on focus without login POST', async () => {
    const token = jwt()
    localStorage.setItem(key, token)
    let calls = 0
    server.use(http.get(`${base}/access/me`, () => ++calls === 1
      ? HttpResponse.json({ detail: 'Temporary failure' }, { status: 503 }) : HttpResponse.json(identity)))
    const { result } = renderHook(useAuth, { wrapper })
    await waitFor(() => expect(result.current.status).toBe('anonymous'))
    expect(result.current.error).toContain('Temporary failure')
    expect(localStorage.getItem(key)).toBe(token)
    expect(calls).toBe(1)
    act(() => { window.dispatchEvent(new Event('focus')) })
    await waitFor(() => expect(result.current.status).toBe('authenticated'))
    expect(calls).toBe(2)
  })

  it('clears the active session on another tab logout and ignores unrelated storage events', async () => {
    localStorage.setItem(key, jwt())
    server.use(http.get(`${base}/access/me`, () => HttpResponse.json(identity)))
    const { result } = renderHook(useAuth, { wrapper })
    await waitFor(() => expect(result.current.status).toBe('authenticated'))
    act(() => { window.dispatchEvent(new StorageEvent('storage', { key: 'unrelated' })) })
    expect(result.current.status).toBe('authenticated')
    act(() => { storageChanged(null) })
    expect(result.current.status).toBe('anonymous')
    expect(result.current.apiSession).toBeUndefined()
  })

  it('does not let an old API 401 remove another tab newer token or identity', async () => {
    const oldToken = jwt('old')
    const newToken = jwt('new')
    localStorage.setItem(key, oldToken)
    let release!: () => void
    server.use(
      http.get(`${base}/access/me`, ({ request }) => HttpResponse.json({ ...identity,
        id: request.headers.get('authorization') === `Bearer ${newToken}` ? 'google:new' : identity.id })),
      http.get(`${base}/delayed-private`, async () => {
        await new Promise<void>((resolve) => { release = resolve })
        return HttpResponse.json({ detail: 'Expired old request' }, { status: 401 })
      }),
    )
    const { result } = renderHook(useAuth, { wrapper })
    await waitFor(() => expect(result.current.status).toBe('authenticated'))
    let pending!: Promise<Response>
    act(() => { pending = result.current.apiSession!.fetch!('http://localhost/api/v1/admin/delayed-private') })
    await waitFor(() => expect(release).toBeTypeOf('function'))
    act(() => { storageChanged(newToken) })
    await waitFor(() => expect(result.current.identity?.id).toBe('google:new'))
    await act(async () => { release(); await pending })
    expect(result.current.status).toBe('authenticated')
    expect(localStorage.getItem(key)).toBe(newToken)
  })

  it('restores correctly in React StrictMode', async () => {
    localStorage.setItem(key, jwt())
    server.use(http.get(`${base}/access/me`, () => HttpResponse.json(identity)))
    const strictWrapper = ({ children }: PropsWithChildren) => <StrictMode><AuthProvider>{children}</AuthProvider></StrictMode>
    const { result } = renderHook(useAuth, { wrapper: strictWrapper })
    await waitFor(() => expect(result.current.status).toBe('authenticated'))
    expect(result.current.identity).toEqual(identity)
  })

  it('removes persisted credentials on API 401 as well as clearing the active session', async () => {
    localStorage.setItem(key, jwt())
    server.use(
      http.get(`${base}/access/me`, () => HttpResponse.json(identity)),
      http.get(`${base}/expired-api`, () => HttpResponse.json({ detail: 'Expired' }, { status: 401 })),
    )
    const { result } = renderHook(useAuth, { wrapper })
    await waitFor(() => expect(result.current.status).toBe('authenticated'))
    await act(async () => { await result.current.apiSession!.fetch!('http://localhost/api/v1/admin/expired-api') })
    expect(result.current.status).toBe('anonymous')
    expect(localStorage.getItem(key)).toBeNull()
  })

  it('keeps login usable when browser storage is blocked, without restoring an unverified token', async () => {
    const storage = vi.spyOn(localStorage, 'setItem').mockImplementation(() => { throw new Error('Storage blocked') })
    server.use(
      http.post(`${base}/access/login`, () => HttpResponse.json(identity)),
      http.get(`${base}/access/me`, () => HttpResponse.json(identity)),
    )
    try {
      const { result } = renderHook(useAuth, { wrapper })
      await act(async () => { await result.current.login(jwt()) })
      expect(result.current.status).toBe('authenticated')
      expect(storage).toHaveBeenCalled()
      expect(localStorage.getItem(key)).toBeNull()
      act(() => { window.dispatchEvent(new Event('focus')) })
      expect(result.current.status).toBe('authenticated')
    } finally { storage.mockRestore() }
  })

  it('clears an expired credential when a suspended tab resumes', async () => {
    const exp = Math.floor(Date.now() / 1000) + 600
    localStorage.setItem(key, jwt('expiring', exp))
    server.use(http.get(`${base}/access/me`, () => HttpResponse.json(identity)))
    const { result } = renderHook(useAuth, { wrapper })
    await waitFor(() => expect(result.current.status).toBe('authenticated'))
    const now = vi.spyOn(Date, 'now').mockReturnValue(exp * 1000 + 1)
    try {
      act(() => { window.dispatchEvent(new Event('focus')) })
      expect(result.current.status).toBe('anonymous')
      expect(localStorage.getItem(key)).toBeNull()
    } finally { now.mockRestore() }
  })

  it('handles another tab clearing browser storage as logout', async () => {
    localStorage.setItem(key, jwt())
    server.use(http.get(`${base}/access/me`, () => HttpResponse.json(identity)))
    const { result } = renderHook(useAuth, { wrapper })
    await waitFor(() => expect(result.current.status).toBe('authenticated'))
    act(() => {
      localStorage.clear()
      window.dispatchEvent(new StorageEvent('storage', { key: null }))
    })
    expect(result.current.status).toBe('anonymous')
    expect(result.current.apiSession).toBeUndefined()
  })
})
