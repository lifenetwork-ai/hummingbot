export const AUTH_TOKEN_STORAGE_PREFIX = 'life-mm.auth-token.v1:'

export function authTokenStorageKey(apiBaseUrl: string, googleClientId: string | undefined): string {
  const normalizedApiBase = apiBaseUrl.trim().replace(/\/+$/, '')
  return `${AUTH_TOKEN_STORAGE_PREFIX}${encodeURIComponent(normalizedApiBase)}:${encodeURIComponent(googleClientId?.trim() ?? '')}`
}

export function tokenExpiry(token: string): number | null {
  try {
    const parts = token.split('.')
    if (parts.length !== 3 || parts.some((part) => !part)) return null
    const normalized = parts[1].replace(/-/g, '+').replace(/_/g, '/')
    const claims: unknown = JSON.parse(atob(normalized.padEnd(Math.ceil(normalized.length / 4) * 4, '=')))
    if (!claims || typeof claims !== 'object' || !('exp' in claims) || typeof claims.exp !== 'number') return null
    const expiryMs = claims.exp * 1000
    return Number.isFinite(expiryMs) && expiryMs > 0 && expiryMs <= 8_640_000_000_000_000 ? expiryMs : null
  } catch { return null }
}

/** Local expiry check is a startup filter; /access/me still verifies the credential. */
export function isRestorableGoogleIdToken(token: string, nowMs = Date.now()): boolean {
  const expiry = tokenExpiry(token)
  return expiry !== null && expiry > nowMs
}

function browserStorage(): Storage | null {
  try { return typeof window === 'undefined' ? null : window.localStorage } catch { return null }
}

export function readAuthToken(key: string, storage = browserStorage()): string | null {
  try { return storage?.getItem(key) ?? null } catch { return null }
}

export function storeAuthToken(key: string, token: string, storage = browserStorage()): boolean {
  if (!storage || !isRestorableGoogleIdToken(token)) return false
  try { storage.setItem(key, token); return true } catch { return false }
}

export function removeAuthTokenIfMatches(key: string, expectedToken: string, storage = browserStorage()): boolean {
  if (!storage) return false
  try {
    if (storage.getItem(key) !== expectedToken) return false
    storage.removeItem(key)
    return true
  } catch { return false }
}
