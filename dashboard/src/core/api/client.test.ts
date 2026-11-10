import { http, HttpResponse } from 'msw'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { server } from '../../test/mocks/server'
import { ApiError, createApiClient } from './client'

const url = 'https://example.test/api/v1/admin'

// This API-only suite does not use storage; provide the storage stub expected by shared setup.
Object.defineProperty(window, 'localStorage', { configurable: true, value: { clear: () => undefined } })

describe('createApiClient', () => {
  afterEach(() => vi.restoreAllMocks())

  it('returns payloads without coercing decimal strings or nulls and resolves auth for each request', async () => {
    const tokens = ['first-token', 'refreshed-token']
    const getToken = vi.fn(() => tokens.shift())
    const seen: string[] = []
    server.use(http.get(`${url}/balances`, ({ request }) => {
      seen.push(request.headers.get('authorization') ?? '')
      return HttpResponse.json({ amount: '0.100000000000000001', equity: null })
    }))
    const client = createApiClient({ baseUrl: url, getToken })

    const first = await client.request<{ amount: string; equity: null }>('/balances')
    const second = await client.request('/balances')

    expect(first).toEqual({ amount: '0.100000000000000001', equity: null })
    expect(second).toEqual(first)
    expect(seen).toEqual(['Bearer first-token', 'Bearer refreshed-token'])
  })

  it.each([
    [422, { error: 'validation_failed', code: 'invalid_request', request_id: 'req-422', retry: 'fix_request', fields: [{ path: 'limit', code: 'out_of_range' }] }],
    [409, { error: 'conflict', code: 'control_version_changed', request_id: 'req-409', retry: 'refresh_state' }],
    [503, { error: 'unavailable', code: 'service_unavailable', request_id: 'req-503', retry: 'wait', retry_after_seconds: 3 }],
  ] as const)('exposes structured HTTP %s errors', async (status, payload) => {
    server.use(http.get(`${url}/failure`, () => HttpResponse.json(payload, { status })))
    const client = createApiClient({ baseUrl: url })

    await expect(client.request('/failure')).rejects.toMatchObject({
      name: 'ApiError', status, requestId: payload.request_id, retry: payload.retry,
      retryAfterSeconds: 'retry_after_seconds' in payload ? payload.retry_after_seconds : undefined,
      fields: 'fields' in payload ? payload.fields : [],
    })
  })

  it('propagates network and abort failures unchanged', async () => {
    const networkFailure = new TypeError('network down')
    const networkClient = createApiClient({ baseUrl: url, fetch: vi.fn().mockRejectedValue(networkFailure) })
    await expect(networkClient.request('/status')).rejects.toBe(networkFailure)

    const controller = new AbortController()
    controller.abort()
    const abortFailure = new DOMException('Aborted', 'AbortError')
    const abortClient = createApiClient({
      baseUrl: url,
      fetch: vi.fn((_input, init) => init?.signal?.aborted ? Promise.reject(abortFailure) : Promise.resolve(Response.json({}))),
    })
    await expect(abortClient.request('/status', { signal: controller.signal })).rejects.toMatchObject({ name: 'AbortError' })
  })

  it('sends only the caller supplied idempotency key and does not retry a failed mutation', async () => {
    let calls = 0
    let receivedKey: string | null = null
    server.use(http.post(`${url}/commands`, async ({ request }) => {
      calls += 1
      receivedKey = request.headers.get('Idempotency-Key')
      await request.json()
      return HttpResponse.json({ error: 'conflict', request_id: 'req-command' }, { status: 409 })
    }))
    const client = createApiClient({ baseUrl: url })

    await expect(client.request('/commands', { method: 'POST', body: { action: 'start' }, idempotencyKey: 'retry-1' }))
      .rejects.toBeInstanceOf(ApiError)
    expect(receivedKey).toBe('retry-1')
    expect(calls).toBe(1)
  })

  it('allows independent requests to complete concurrently', async () => {
    const client = createApiClient({ baseUrl: url })
    server.use(http.get(`${url}/slow`, async () => {
      await new Promise((resolve) => setTimeout(resolve, 20))
      return HttpResponse.json({ value: 'slow' })
    }))
    server.use(http.get(`${url}/fast`, () => HttpResponse.json({ value: 'fast' })))

    const slow = client.request('/slow')
    const fast = client.request('/fast')
    await expect(fast).resolves.toEqual({ value: 'fast' })
    await expect(slow).resolves.toEqual({ value: 'slow' })
  })
})
