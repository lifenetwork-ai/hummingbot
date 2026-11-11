import { transferableAbortController } from 'node:util'
import { act, renderHook, waitFor } from '@testing-library/react'
import type { PropsWithChildren } from 'react'
import { afterAll, describe, expect, it, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import { ApiProvider } from './ApiProvider'
import { useReceiptInspection } from './hooks'
import { pendingCommand } from '../../test/mocks/adminFixtures'
import { server } from '../../test/mocks/server'

const controller = transferableAbortController()
vi.stubGlobal('AbortController', controller.constructor)
vi.stubGlobal('AbortSignal', controller.signal.constructor)
afterAll(() => vi.unstubAllGlobals())
const baseUrl = 'https://inspection-lifecycle.test/api/v1/admin'
function wrapper({ children }: PropsWithChildren) {
  return <ApiProvider session={{ baseUrl, cacheIdentity: 'inspect:1' }}>{children}</ApiProvider>
}

describe('read-only receipt selection lifecycle', () => {
  it('does not let a late previous receipt replace the newly selected result', async () => {
    let releaseOld: (() => void) | undefined
    let writes = 0
    server.use(
      http.get(`${baseUrl}/commands/:id`, async ({ params }) => {
        if (params.id === 'old') {
          await new Promise<void>((resolve) => { releaseOld = resolve })
          return HttpResponse.json({ ...pendingCommand, id: 'old', status: 'failed', error: 'Old failure' })
        }
        return HttpResponse.json({ ...pendingCommand, id: String(params.id), status: 'accepted' })
      }),
      http.post(`${baseUrl}/*`, () => { writes++; return HttpResponse.json({}) }),
    )
    const { result, rerender } = renderHook(({ id }) => useReceiptInspection({ kind: 'command', id }),
      { initialProps: { id: 'old' }, wrapper })
    await waitFor(() => expect(releaseOld).toBeTypeOf('function'))
    rerender({ id: 'new' })
    await waitFor(() => expect(result.current?.phase).toBe('ready'))
    expect(result.current?.reference.id).toBe('new')
    expect(result.current?.status).toBe('accepted')
    await act(async () => { releaseOld?.() })
    expect(result.current?.reference.id).toBe('new')
    expect(result.current?.status).toBe('accepted')
    expect(result.current?.detail).not.toBe('Old failure')
    expect(writes).toBe(0)
  })

  it('shows persistent read errors without mutation replay and allows clean reselection', async () => {
    let calls = 0
    let writes = 0
    server.use(
      http.get(`${baseUrl}/commands/:id`, ({ params }) => {
        calls++
        return params.id === 'unavailable'
          ? HttpResponse.json({ detail: 'Receipt unavailable' }, { status: 500 })
          : HttpResponse.json({ ...pendingCommand, id: String(params.id) })
      }),
      http.post(`${baseUrl}/*`, () => { writes++; return HttpResponse.json({}) }),
    )
    const { result, rerender } = renderHook(({ id }) => useReceiptInspection({ kind: 'command', id }),
      { initialProps: { id: 'unavailable' }, wrapper })
    await waitFor(() => expect(result.current?.phase).toBe('error'))
    expect(result.current?.error?.message).toContain('Receipt unavailable')
    expect(calls).toBe(1)
    rerender({ id: 'available' })
    await waitFor(() => expect(result.current?.phase).toBe('ready'))
    expect(result.current?.reference.id).toBe('available')
    expect(result.current?.error).toBeNull()
    expect(writes).toBe(0)
  })
})
