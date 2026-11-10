import { useCallback, useEffect, useMemo, useRef, useState, type PropsWithChildren } from 'react'
import { getAppConfig } from '../config/env'
import { ApiError } from '../api/client'
import type { ApiSession } from '../api/ApiProvider'
import type { ResponseBody } from '../api/types'
import { AuthContext, type AuthContextValue, type AuthStatus } from './auth-context'

const { adminApiBaseUrl: baseUrl } = getAppConfig()

type State = { status: AuthStatus; identity: ResponseBody<'getAdminIdentity'> | null; error: string | null }

function tokenExpiry(token: string): number | null {
  try {
    const payload = token.split('.')[1]
    if (!payload) return null
    const normalized = payload.replace(/-/g, '+').replace(/_/g, '/')
    const claims: unknown = JSON.parse(atob(normalized.padEnd(Math.ceil(normalized.length / 4) * 4, '=')))
    if (!claims || typeof claims !== 'object' || !('exp' in claims) || typeof claims.exp !== 'number') return null
    return claims.exp * 1000
  } catch {
    return null
  }
}

async function requestIdentity(token: string): Promise<ResponseBody<'getAdminIdentity'>> {
  const response = await fetch(`${baseUrl.replace(/\/+$/, '')}/access/me`, {
    method: 'GET', credentials: 'omit', headers: { Authorization: `Bearer ${token}`, Accept: 'application/json' },
  })
  if (!response.ok) {
    let detail = `Request failed with status ${response.status}`
    try {
      const body: unknown = await response.json()
      if (body && typeof body === 'object' && 'detail' in body && typeof body.detail === 'string') detail = body.detail
      else if (body && typeof body === 'object' && 'error' in body && typeof body.error === 'string') detail = body.error
    } catch { /* keep status message */ }
    throw new ApiError(response.status, { detail })
  }
  return await response.json() as ResponseBody<'getAdminIdentity'>
}

export function AuthProvider({ children }: PropsWithChildren) {
  const [state, setState] = useState<State>({ status: 'anonymous', identity: null, error: null })
  const tokenRef = useRef<string | null>(null)
  const generation = useRef(0)
  const loginInFlight = useRef<Promise<void> | null>(null)
  const sessionRevision = useRef(0)

  const logout = useCallback(() => {
    generation.current += 1
    loginInFlight.current = null
    tokenRef.current = null
    setState({ status: 'anonymous', identity: null, error: null })
  }, [])

  const login = useCallback((credential: string): Promise<void> => {
    if (loginInFlight.current) return loginInFlight.current
    if (!credential.trim()) {
      setState({ status: 'anonymous', identity: null, error: 'Login token is missing.' })
      return Promise.resolve()
    }
    const attempt = ++generation.current
    setState({ status: 'authenticating', identity: null, error: null })
    const pending = (async () => {
      try {
        const response = await fetch(`${baseUrl.replace(/\/+$/, '')}/access/login`, {
          method: 'POST', credentials: 'omit', headers: { Authorization: `Bearer ${credential}`, Accept: 'application/json' },
        })
        if (!response.ok) {
          let detail = `Request failed with status ${response.status}`
          try {
            const body: unknown = await response.json()
            if (body && typeof body === 'object' && 'detail' in body && typeof body.detail === 'string') detail = body.detail
            else if (body && typeof body === 'object' && 'error' in body && typeof body.error === 'string') detail = body.error
          } catch { /* keep status message */ }
          throw new ApiError(response.status, { detail })
        }
        // The login response confirms authentication; /me supplies the canonical current role.
        await response.json()
        const identity = await requestIdentity(credential)
        if (generation.current !== attempt) return
        tokenRef.current = credential
        sessionRevision.current += 1
        setState({ status: 'authenticated', identity, error: null })
      } catch (error) {
        if (generation.current !== attempt) return
        tokenRef.current = null
        setState({ status: 'anonymous', identity: null, error: error instanceof Error ? error.message : 'Sign-in failed.' })
      } finally {
        if (generation.current === attempt) loginInFlight.current = null
      }
    })()
    loginInFlight.current = pending
    return pending
  }, [])

  useEffect(() => {
    if (state.status !== 'authenticated' || !tokenRef.current) return
    const expiry = tokenExpiry(tokenRef.current)
    if (expiry === null) return
    const delay = expiry - Date.now()
    if (delay <= 0) {
      logout()
      return
    }
    const timer = window.setTimeout(logout, Math.min(delay, 2_147_483_647))
    return () => window.clearTimeout(timer)
  }, [logout, state.status, state.identity])

  const sessionFetch = useCallback<typeof globalThis.fetch>(async (input, init) => {
    const requestToken = tokenRef.current
    const requestGeneration = generation.current
    const response = await globalThis.fetch(input, { ...init, credentials: 'omit' })
    if (requestToken && tokenRef.current === requestToken && generation.current === requestGeneration) {
      if (response.status === 401) {
        logout()
      } else if (response.status === 403) {
        try {
          const identity = await requestIdentity(requestToken)
          if (generation.current === requestGeneration && tokenRef.current === requestToken) {
            setState((current) => {
              if (current.identity?.role !== identity.role || JSON.stringify(current.identity?.permissions) !== JSON.stringify(identity.permissions)) sessionRevision.current += 1
              return { ...current, identity }
            })
          }
        } catch (error) {
          if (error instanceof ApiError && (error.status === 401 || error.status === 403) && generation.current === requestGeneration) logout()
        }
      }
    }
    return response
  }, [logout])
  const apiSession = useMemo<ApiSession | undefined>(() => state.status === 'authenticated' && state.identity ? {
    baseUrl,
    cacheIdentity: `${state.identity.id}:${sessionRevision.current}`,
    getToken: () => tokenRef.current,
    fetch: sessionFetch,
  } : undefined, [sessionFetch, state.identity, state.status])

  const value: AuthContextValue = { ...state, login, logout, apiSession }
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
