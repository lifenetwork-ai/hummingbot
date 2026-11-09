import { HttpResponse, http } from 'msw'
import { describe, expect, it } from 'vitest'
import { MOCK_API_URL } from './handlers'
import { server } from './server'

describe('MSW example', () => {
  it('intercepts a request without a real API', async () => {
    const response = await fetch(MOCK_API_URL)
    expect(response.ok).toBe(true)
    expect(await response.json()).toEqual({ ok: true, service: 'starter' })
  })

  it('overrides a mock for a single test', async () => {
    server.use(http.get(MOCK_API_URL, () => HttpResponse.json({ ok: false }, { status: 503 })))
    const response = await fetch(MOCK_API_URL)
    expect(response.status).toBe(503)
    expect(await response.json()).toEqual({ ok: false })
  })
})
