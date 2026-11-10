/** Only permit same-origin dashboard destinations. */
export function safeRedirect(value: unknown): string {
  if (typeof value !== 'string' || !value.startsWith('/') || value.startsWith('//') || /[\\\r\n]/.test(value)) return '/'
  try {
    const origin = window.location.origin
    const url = new URL(value, origin)
    if (url.origin !== origin || url.pathname === '/login' || url.pathname.startsWith('/login/')) return '/'
    return url.pathname + url.search + url.hash
  } catch { return '/' }
}
