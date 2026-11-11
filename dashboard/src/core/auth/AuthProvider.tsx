import { useCallback, useEffect, useMemo, useRef, useState, type PropsWithChildren } from 'react'
import { getAppConfig } from '../config/env'
import { ApiError } from '../api/client'
import type { ApiSession } from '../api/ApiProvider'
import type { ResponseBody } from '../api/types'
import { AuthContext, type AuthContextValue, type AuthStatus } from './auth-context'
import { authTokenStorageKey, isRestorableGoogleIdToken, readAuthToken, removeAuthTokenIfMatches, storeAuthToken, tokenExpiry } from './auth-persistence'

const { adminApiBaseUrl: baseUrl, googleClientId } = getAppConfig()
const storageKey = authTokenStorageKey(baseUrl, googleClientId)

type State = { status: AuthStatus; identity: ResponseBody<'getAdminIdentity'> | null; error: string | null }

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
  const [state, setState] = useState<State>({ status: 'restoring', identity: null, error: null })
  const tokenRef = useRef<string | null>(null)
  const persistedToken = useRef<string | null>(null)
  const generation = useRef(0)
  const loginInFlight = useRef<Promise<void> | null>(null)
  const identityRefresh = useRef<{ token: string; generation: number; promise: Promise<ResponseBody<'getAdminIdentity'>> } | null>(null)
  const sessionRevision = useRef(0)

  const endSession = useCallback((error: string | null, removePersisted = true) => {
    const previous = tokenRef.current
    generation.current += 1
    loginInFlight.current = null
    tokenRef.current = null
    persistedToken.current = null
    identityRefresh.current = null
    if (removePersisted && previous !== null) removeAuthTokenIfMatches(storageKey, previous)
    setState({ status: 'anonymous', identity: null, error })
  }, [])

  const logout = useCallback(() => {
    const stored = readAuthToken(storageKey)
    endSession(null)
    // Explicit logout also clears a credential awaiting restoration or login.
    if (stored !== null) removeAuthTokenIfMatches(storageKey, stored)
  }, [endSession])

  const restore = useCallback(async (credential: string) => {
    const attempt = ++generation.current
    loginInFlight.current = null
    identityRefresh.current = null
    tokenRef.current = credential
    persistedToken.current = credential
    setState({ status: 'restoring', identity: null, error: null })
    if (!isRestorableGoogleIdToken(credential)) {
      endSession(null)
      return
    }
    try {
      const identity = await requestIdentity(credential)
      if (generation.current !== attempt || tokenRef.current !== credential) return
      if (!isRestorableGoogleIdToken(credential)) { endSession(null); return }
      // A cross-tab change must not be overwritten by an older /me response.
      if (readAuthToken(storageKey) !== credential) { endSession(null, false); return }
      sessionRevision.current += 1
      setState({ status: 'authenticated', identity, error: null })
    } catch (error) {
      if (generation.current !== attempt || tokenRef.current !== credential) return
      const rejected = error instanceof ApiError && (error.status === 401 || error.status === 403)
      endSession(error instanceof Error ? error.message : 'Could not restore sign-in.', rejected)
    }
  }, [endSession])

  useEffect(() => {
    const syncStoredCredential = () => {
      const stored = readAuthToken(storageKey)
      if (stored !== null && stored === tokenRef.current) {
        if (!isRestorableGoogleIdToken(stored)) endSession(null)
        return
      }
      if (stored !== null) void restore(stored)
      else if (persistedToken.current) endSession(null, false)
      else if (!tokenRef.current) setState((current) => current.status === 'restoring'
        ? { status: 'anonymous', identity: null, error: null } : current)
    }
    const onStorage = (event: StorageEvent) => {
      if (event.key === storageKey || event.key === null) syncStoredCredential()
    }
    const onResume = () => { if (!loginInFlight.current) syncStoredCredential() }
    window.addEventListener('storage', onStorage)
    window.addEventListener('focus', onResume)
    window.addEventListener('online', onResume)
    syncStoredCredential()
    return () => {
      window.removeEventListener('storage', onStorage)
      window.removeEventListener('focus', onResume)
      window.removeEventListener('online', onResume)
      generation.current += 1
      tokenRef.current = null
      persistedToken.current = null
      loginInFlight.current = null
      identityRefresh.current = null
    }
  }, [endSession, restore])

  const login = useCallback((credential: string): Promise<void> => {
    if (loginInFlight.current) return loginInFlight.current
    if (!credential.trim()) {
      endSession('Login token is missing.')
      return Promise.resolve()
    }
    const attempt = ++generation.current
    tokenRef.current = credential
    identityRefresh.current = null
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
        await response.json()
        const identity = await requestIdentity(credential)
        if (generation.current !== attempt) return
        const expiry = tokenExpiry(credential)
        if (expiry !== null && expiry <= Date.now()) { endSession('Sign-in token expired. Please sign in again.'); return }
        tokenRef.current = credential
        persistedToken.current = storeAuthToken(storageKey, credential) ? credential : null
        sessionRevision.current += 1
        setState({ status: 'authenticated', identity, error: null })
      } catch (error) {
        if (generation.current !== attempt) return
        endSession(error instanceof Error ? error.message : 'Sign-in failed.')
      } finally {
        if (generation.current === attempt) loginInFlight.current = null
      }
    })()
    loginInFlight.current = pending
    return pending
  }, [endSession])

  useEffect(() => {
    if (state.status !== 'authenticated' || !tokenRef.current) return
    const expiry = tokenExpiry(tokenRef.current)
    if (expiry === null) return
    const expirationGeneration = generation.current
    let timer: number | undefined
    const schedule = () => {
      if (generation.current !== expirationGeneration) return
      const remaining = expiry - Date.now()
      if (remaining <= 0) endSession(null)
      else timer = window.setTimeout(schedule, Math.min(remaining, 2_147_483_647))
    }
    schedule()
    return () => window.clearTimeout(timer)
  }, [endSession, state.status, state.identity])

  const sessionFetch = useCallback<typeof globalThis.fetch>(async (input, init) => {
    const requestToken = tokenRef.current
    const requestGeneration = generation.current
    const response = await globalThis.fetch(input, { ...init, credentials: 'omit' })
    if (requestToken && tokenRef.current === requestToken && generation.current === requestGeneration) {
      if (response.status === 401) endSession(null)
      else if (response.status === 403) {
        let refresh = identityRefresh.current
        if (!refresh || refresh.token !== requestToken || refresh.generation !== requestGeneration) {
          refresh = { token: requestToken, generation: requestGeneration, promise: requestIdentity(requestToken) }
          identityRefresh.current = refresh
        }
        try {
          const identity = await refresh.promise
          if (generation.current === requestGeneration && tokenRef.current === requestToken) {
            setState((current) => {
              if (current.identity?.role !== identity.role || JSON.stringify(current.identity?.permissions) !== JSON.stringify(identity.permissions)) sessionRevision.current += 1
              return { ...current, identity }
            })
          }
        } catch (error) {
          if (generation.current === requestGeneration && tokenRef.current === requestToken) {
            const rejected = error instanceof ApiError && (error.status === 401 || error.status === 403)
            endSession(error instanceof Error ? error.message : 'Could not verify your current permissions. Please sign in again.', rejected)
          }
        } finally {
          if (identityRefresh.current === refresh) identityRefresh.current = null
        }
      }
    }
    return response
  }, [endSession])
  const apiSession = useMemo<ApiSession | undefined>(() => state.status === 'authenticated' && state.identity ? {
    baseUrl,
    principalId: state.identity.id,
    cacheIdentity: `${state.identity.id}:${sessionRevision.current}`,
    getToken: () => tokenRef.current,
    fetch: sessionFetch,
  } : undefined, [sessionFetch, state.identity, state.status])

  const value: AuthContextValue = { ...state, login, logout, apiSession }
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
