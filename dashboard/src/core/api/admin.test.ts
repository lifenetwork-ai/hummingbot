import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { http, HttpResponse } from 'msw'
import { describe, expect, it, vi } from 'vitest'
import type { components } from './schema'
import { createApiClient } from './client'
import { adminOperations, createAdminApi } from './admin'
import { server } from '../../test/mocks/server'

const baseUrl = 'https://example.test/api/v1/admin'
Object.defineProperty(window, 'localStorage', { configurable: true, value: { clear: () => undefined } })
const createApi = () => createAdminApi(createApiClient({ baseUrl, getToken: () => 'fixture-token' }))

const stopBody: components['schemas']['StopCommand'] = {
  action: 'stop', expected_version: 3, session_id: 'session-1',
}

const typeContract = () => {
  // @ts-expect-error Path IDs required by the generated operation contract.
  createApi().read('getOrder', { query: {} })
  // @ts-expect-error POST body is required by the generated contract.
  createApi().mutate('submitCommand', { idempotencyKey: 'key' })
  // @ts-expect-error Idempotency key is required by the generated contract.
  createApi().mutate('submitCommand', { body: stopBody })
  // @ts-expect-error Query enum values come from the generated contract.
  createApi().read('listOrders', { query: { state: 'pending' } })
  // @ts-expect-error Operation must be a generated read operation.
  createApi().read('submitCommand', {})
}
void typeContract

describe('admin operation adapter', () => {
  it('maps every JSON admin operation and route in the checked OpenAPI contract', () => {
    const spec = JSON.parse(readFileSync(resolve(process.cwd(), '../docs/api/openapi.json'), 'utf8')) as {
      paths: Record<string, Record<string, { operationId?: string }>>
    }
    const expected = Object.entries(spec.paths).flatMap(([path, methods]) => Object.entries(methods)
      .filter(([method, operation]) => ['get', 'post'].includes(method) && operation.operationId
        && !path.includes('/docs') && !path.endsWith('/openapi.json'))
      .map(([method, operation]) => [operation.operationId!, [method.toUpperCase(), path.replace('/api/v1/admin', '')]]))
    expect(Object.entries(adminOperations).sort()).toEqual(expected.sort())
  })

  it('encodes path values and preserves falsy values while omitting null/undefined query values', async () => {
    let requested = ''
    server.use(http.get(`${baseUrl}/orders/:order_id`, ({ request }) => {
      requested = request.url
      return HttpResponse.json({ id: 'x' })
    }))
    await createApi().read('getOrder', {
      path: { order_id: 'order/with space' },
      query: { timeline_limit: 0 },
    })
    const url = new URL(requested)
    expect(url.pathname).toContain('order%2Fwith%20space')
    expect(url.searchParams.get('timeline_limit')).toBe('0')

    server.use(http.get(`${baseUrl}/orders`, ({ request }) => {
      requested = request.url
      return HttpResponse.json({ items: [], next_cursor: null })
    }))
    await createApi().read('listOrders', { query: { market: 'spot', pair: undefined, limit: 0 } })
    expect(new URL(requested).searchParams.get('limit')).toBe('0')
    expect(new URL(requested).searchParams.has('pair')).toBe(false)
  })

  it('sends generated mutation body, current auth and required idempotency key', async () => {
    let observed: { authorization: string | null; key: string | null; body: unknown } | undefined
    server.use(http.post(`${baseUrl}/commands`, async ({ request }) => {
      observed = {
        authorization: request.headers.get('authorization'),
        key: request.headers.get('idempotency-key'),
        body: await request.json(),
      }
      return HttpResponse.json({ id: 'receipt' }, { status: 202 })
    }))
    await createApi().mutate('submitCommand', { body: stopBody, idempotencyKey: 'same-command-retry' })
    expect(observed).toEqual({ authorization: 'Bearer fixture-token', key: 'same-command-retry', body: stopBody })
  })

  it('accepts a non-202 successful mutation status from the OpenAPI contract', async () => {
    server.use(http.post(`${baseUrl}/access/accounts`, () => HttpResponse.json({ id: 'created' }, { status: 201 })))
    await expect(createApi().mutate('addAdminAccount', { body: { email: 'a@example.test', role: 'read', active: true } }))
      .resolves.toEqual({ id: 'created' })
  })

  it('propagates structured HTTP errors without retrying the write', async () => {
    const requests = vi.fn()
    server.use(http.post(`${baseUrl}/commands`, () => {
      requests()
      return HttpResponse.json({ error: 'conflict', code: 'version_changed' }, { status: 409 })
    }))
    await expect(createApi().mutate('submitCommand', { body: stopBody, idempotencyKey: 'same-key' }))
      .rejects.toMatchObject({ status: 409, body: { code: 'version_changed' } })
    expect(requests).toHaveBeenCalledTimes(1)
  })

  it('propagates caller cancellation to fetch', async () => {
    const controller = new AbortController()
    const abort = new DOMException('Aborted', 'AbortError')
    const client = createApiClient({ baseUrl, fetch: vi.fn((_input, init) => init?.signal?.aborted ? Promise.reject(abort) : Promise.resolve(Response.json({}))) })
    const request = createAdminApi(client).read('getRuntimeStatus', {}, controller.signal)
    controller.abort()
    await expect(request).rejects.toMatchObject({ name: 'AbortError' })
  })
})
