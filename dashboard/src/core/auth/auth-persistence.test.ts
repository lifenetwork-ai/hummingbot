import { describe, expect, it } from 'vitest'
import {
  authTokenStorageKey,
  AUTH_TOKEN_STORAGE_PREFIX,
  isRestorableGoogleIdToken,
  readAuthToken,
  removeAuthTokenIfMatches,
  storeAuthToken,
  tokenExpiry,
} from './auth-persistence'

function jwt(payload: Record<string, unknown>): string {
  return `header.${btoa(JSON.stringify(payload)).replace(/=/g, '').replace(/\+/g, '-').replace(/\//g, '_')}.signature`
}

class MemoryStorage implements Storage {
  private readonly values = new Map<string, string>()
  get length() { return this.values.size }
  clear() { this.values.clear() }
  getItem(key: string) { return this.values.get(key) ?? null }
  key(index: number) { return [...this.values.keys()][index] ?? null }
  removeItem(key: string) { this.values.delete(key) }
  setItem(key: string, value: string) { this.values.set(key, String(value)) }
}

describe('auth token persistence', () => {
  it('scopes a stable key by normalized API base URL and Google client ID', () => {
    expect(authTokenStorageKey('https://api.example.test/admin/', 'client-a'))
      .toBe(`${AUTH_TOKEN_STORAGE_PREFIX}https%3A%2F%2Fapi.example.test%2Fadmin:client-a`)
    expect(authTokenStorageKey('https://api.example.test/admin', 'client-b'))
      .not.toBe(authTokenStorageKey('https://api.example.test/admin', 'client-a'))
  })

  it('decodes finite JWT expiry and restores only unexpired tokens', () => {
    const now = 1_800_000_000_000
    expect(tokenExpiry(jwt({ exp: now / 1000 + 60 }))).toBe(now + 60_000)
    expect(isRestorableGoogleIdToken(jwt({ exp: now / 1000 + 60 }), now)).toBe(true)
    expect(isRestorableGoogleIdToken(jwt({ exp: now / 1000 }), now)).toBe(false)
    expect(isRestorableGoogleIdToken(jwt({ exp: now / 1000 + 86_401 }), now)).toBe(true)
    for (const token of [
      'opaque-token', 'header.not-json.signature',
      jwt({ exp: Number.POSITIVE_INFINITY }), jwt({ exp: Number.MAX_VALUE }),
      'one.two.three.four',
    ]) {
      expect(tokenExpiry(token)).toBeNull()
      expect(isRestorableGoogleIdToken(token, now)).toBe(false)
    }
  })

  it('writes, reads and conditionally removes only the matching token', () => {
    const storage = new MemoryStorage()
    const key = authTokenStorageKey('/api/v1/admin', 'client')
    const first = jwt({ exp: Date.now() / 1000 + 600 })
    const newer = jwt({ exp: Date.now() / 1000 + 900 })
    expect(storeAuthToken(key, first, storage)).toBe(true)
    expect(readAuthToken(key, storage)).toBe(first)
    storage.setItem(key, newer)
    expect(removeAuthTokenIfMatches(key, first, storage)).toBe(false)
    expect(readAuthToken(key, storage)).toBe(newer)
    expect(removeAuthTokenIfMatches(key, newer, storage)).toBe(true)
    expect(readAuthToken(key, storage)).toBeNull()
  })

  it('treats blocked or unavailable storage as a safe memory-only fallback', () => {
    const blocked = {
      getItem: () => { throw new Error('blocked') },
      setItem: () => { throw new Error('blocked') },
      removeItem: () => { throw new Error('blocked') },
    } as unknown as Storage
    const token = jwt({ exp: Date.now() / 1000 + 60 })
    expect(readAuthToken('key', blocked)).toBeNull()
    expect(storeAuthToken('key', token, blocked)).toBe(false)
    expect(removeAuthTokenIfMatches('key', token, blocked)).toBe(false)
  })
})
