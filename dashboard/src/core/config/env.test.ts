import { afterEach, describe, expect, it, vi } from 'vitest'
import { getAppConfig } from './env'

afterEach(() => vi.unstubAllEnvs())

describe('public application configuration', () => {
  it('retains the same-origin API default and missing Google configuration', () => {
    vi.stubEnv('VITE_ADMIN_API_BASE_URL', undefined)
    vi.stubEnv('VITE_GOOGLE_CLIENT_ID', undefined)
    expect(getAppConfig()).toEqual({ adminApiBaseUrl: '/api/v1/admin', googleClientId: undefined })
  })
  it('uses the configured API URL and public Google client ID', () => {
    vi.stubEnv('VITE_ADMIN_API_BASE_URL', 'https://api.example.test/api/v1/admin')
    vi.stubEnv('VITE_GOOGLE_CLIENT_ID', 'public-client.apps.googleusercontent.com')
    expect(getAppConfig()).toEqual({
      adminApiBaseUrl: 'https://api.example.test/api/v1/admin',
      googleClientId: 'public-client.apps.googleusercontent.com',
    })
  })
  it('preserves the API default when its environment value is empty', () => {
    vi.stubEnv('VITE_ADMIN_API_BASE_URL', '')
    expect(getAppConfig().adminApiBaseUrl).toBe('/api/v1/admin')
  })
})
