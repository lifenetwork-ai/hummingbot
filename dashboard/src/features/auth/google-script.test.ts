import { afterEach, expect, it, vi } from 'vitest'

afterEach(() => {
  vi.useRealTimers()
  delete window.google
  document.querySelectorAll('script[src="https://accounts.google.com/gsi/client"]').forEach((script) => script.remove())
  vi.resetModules()
})

it('shares a single SDK load across concurrent mounts', async () => {
  const { loadGoogleIdentity } = await import('./google-script')
  const first = loadGoogleIdentity()
  expect(loadGoogleIdentity()).toBe(first)
  const scripts = document.querySelectorAll('script[src="https://accounts.google.com/gsi/client"]')
  expect(scripts).toHaveLength(1)
  const identity = { initialize: vi.fn(), renderButton: vi.fn() }
  window.google = { accounts: { id: identity } }
  scripts[0].dispatchEvent(new Event('load'))
  await expect(first).resolves.toBe(identity)
  await expect(loadGoogleIdentity()).resolves.toBe(identity)
})

it('allows retry after network failure and bounds a stalled SDK load', async () => {
  vi.useFakeTimers()
  const { loadGoogleIdentity } = await import('./google-script')
  const failed = loadGoogleIdentity()
  const rejected = expect(failed).rejects.toThrow('could not load')
  document.querySelector('script[src="https://accounts.google.com/gsi/client"]')!.dispatchEvent(new Event('error'))
  await rejected
  const retried = loadGoogleIdentity()
  const timeout = expect(retried).rejects.toThrow('could not load')
  await vi.advanceTimersByTimeAsync(15_000)
  await timeout
  expect(document.querySelector('script[src="https://accounts.google.com/gsi/client"]')).toBeNull()
})
