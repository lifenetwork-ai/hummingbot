import { transferableAbortController } from 'node:util'
import { act, renderHook, waitFor } from '@testing-library/react'
import type { PropsWithChildren } from 'react'
import { http, HttpResponse } from 'msw'
import { afterAll, describe, expect, it, vi } from 'vitest'
import { ApiProvider } from '../../core/api/ApiProvider'
import { contextStatus } from '../../test/mocks/contextFixtures'
import { staleBalances } from '../../test/mocks/adminFixtures'
import { server } from '../../test/mocks/server'
import type { useOperationalController } from '../operational-context/useOperationalController'
import { useOverviewController } from './useOverviewController'

const controller = transferableAbortController()
vi.stubGlobal('AbortController', controller.constructor)
vi.stubGlobal('AbortSignal', controller.signal.constructor)
afterAll(() => vi.unstubAllGlobals())
const baseUrl = 'https://overview.test/api/v1/admin'
const shared = {
  status: { phase: 'ready' as const, data: contextStatus, error: null, refreshing: false },
  balances: { phase: 'ready' as const, data: staleBalances, error: null, refreshing: false },
} satisfies Pick<ReturnType<typeof useOperationalController>, 'status' | 'balances'>
function wrapper({ children }: PropsWithChildren) {
  return <ApiProvider session={{ baseUrl, cacheIdentity: 'overview-user:1' }}>{children}</ApiProvider>
}

const liquidity = {
  coverage_started_at: null, ended_at: null, generation: 1, markets: {},
  measurement: 'sampled_owner_observations' as const, observed_at: '2026-11-11T00:00:00Z',
  profile_id: null, session_id: null, started_at: null, state: 'observed' as const,
}
const positions = { markets: [], owner_available: true, server_time: '2026-11-11T00:00:00Z' }

describe('overview controller', () => {
  it('reuses supplied status and balances while loading liquidity and positions independently', async () => {
    let releaseLiquidity: (() => void) | undefined
    let failPositions = true
    server.use(
      http.get(`${baseUrl}/statistics/liquidity`, async () => {
        await new Promise<void>((resolve) => { releaseLiquidity = resolve })
        return HttpResponse.json(liquidity)
      }),
      http.get(`${baseUrl}/positions`, () => failPositions
        ? HttpResponse.json({ detail: 'position store unavailable' }, { status: 503 })
        : HttpResponse.json(positions)),
    )
    const { result } = renderHook(() => useOverviewController(shared), { wrapper })
    await waitFor(() => expect(result.current.positions.phase).toBe('error'), { timeout: 8000 })
    expect(result.current.status.data).toBe(contextStatus)
    expect(result.current.balances.data).toBe(staleBalances)
    expect(result.current.liquidity.phase).toBe('loading')

    failPositions = false
    await act(async () => { await result.current.refreshPositions() })
    await waitFor(() => expect(result.current.positions.phase).toBe('ready'))
    expect(result.current.liquidity.phase).toBe('loading')
    releaseLiquidity?.()
    await waitFor(() => expect(result.current.liquidity.phase).toBe('ready'))
  })

  it('preserves a recovered source when the other source continues failing', async () => {
    let failLiquidity = false
    server.use(
      http.get(`${baseUrl}/statistics/liquidity`, () => failLiquidity
        ? HttpResponse.json({ detail: 'liquidity unavailable' }, { status: 503 })
        : HttpResponse.json(liquidity)),
      http.get(`${baseUrl}/positions`, () => HttpResponse.json(positions)),
    )
    const { result } = renderHook(() => useOverviewController(shared), { wrapper })
    await waitFor(() => expect(result.current.liquidity.phase).toBe('ready'))
    await waitFor(() => expect(result.current.positions.phase).toBe('ready'))
    failLiquidity = true
    await act(async () => { await result.current.refreshLiquidity() })
    await waitFor(() => expect(result.current.liquidity.phase).toBe('error'))
    expect(result.current.positions.phase).toBe('ready')
    expect(result.current.balances.phase).toBe('ready')
  })
})
