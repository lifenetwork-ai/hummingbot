import { transferableAbortController } from 'node:util'
import { act, renderHook, waitFor } from '@testing-library/react'
import type { PropsWithChildren } from 'react'
import { http, HttpResponse } from 'msw'
import { afterAll, describe, expect, it, vi } from 'vitest'
import { ApiProvider, type ApiSession } from '../../core/api/ApiProvider'
import { pollingInterval } from '../../core/api/queries'
import { server } from '../../test/mocks/server'
import { useReportingController } from './useReportingController'

const nativeController = transferableAbortController()
vi.stubGlobal('AbortController', nativeController.constructor)
vi.stubGlobal('AbortSignal', nativeController.signal.constructor)
afterAll(() => vi.unstubAllGlobals())

const baseUrl = 'https://reporting.example.test/api/v1/admin'
const session: ApiSession = { baseUrl, cacheIdentity: 'reporting-user', principalId: 'google:reporting-user', getToken: () => 'token' }
function Wrapper({ children }: PropsWithChildren) { return <ApiProvider session={session}>{children}</ApiProvider> }
const dailyResponse = (overrides: Record<string, unknown> = {}) => ({
  balance_scope: 'account_last_observed', balances: [], coverage: 'observed_recorder_events', end_day: '2026-11-12',
  freshness: 'fresh', funding_precision: 'decimal_bills_with_legacy_float', funding_sync: null, markets: [], message: null,
  observation_age_seconds: null, reporting_timezone: 'Asia/Ho_Chi_Minh', retry_after_seconds: null,
  server_time: '2026-11-11T00:00:00Z', start_day: '2026-11-10', trade_storage_scale: null, ...overrides,
})
const performanceResponse = { markets: [], owner_available: true, scope: 'controller_lifetime_and_active_exposure', server_time: '2026-11-11T00:00:00Z' }

describe('useReportingController', () => {
  it('does not fetch an invalid date range and skips the optional performance request', async () => {
    let dailyCalls = 0
    let performanceCalls = 0
    server.use(
      http.get(`${baseUrl}/statistics/daily`, () => { dailyCalls++; return HttpResponse.json(dailyResponse()) }),
      http.get(`${baseUrl}/performance`, () => { performanceCalls++; return HttpResponse.json(performanceResponse) }),
    )
    const { result } = renderHook(() => useReportingController({ from: '2026-11-11', to: '2026-11-01' }), { wrapper: Wrapper })
    expect(result.current.validation).toEqual({ valid: false, reason: 'reversed_range' })
    await act(async () => { await Promise.resolve() })
    expect(dailyCalls).toBe(0)
    expect(performanceCalls).toBe(0)
  })

  it('uses the backend stale retry hint and keeps the server timezone and observation time', async () => {
    server.use(http.get(`${baseUrl}/statistics/daily`, () => HttpResponse.json(dailyResponse({
      freshness: 'stale', message: 'cached observation', observation_age_seconds: 5, retry_after_seconds: 3,
    }))))
    const { result } = renderHook(() => useReportingController({ from: '2026-11-10', to: '2026-11-12' }), { wrapper: Wrapper })
    await waitFor(() => expect(result.current.daily.data?.freshness).toBe('stale'))
    expect(result.current.presentation).toBe('updating')
    expect(result.current.reportingTimezone).toBe('Asia/Ho_Chi_Minh')
    expect(result.current.observedAt).toBe('2026-11-11T00:00:00Z')
    expect(pollingInterval('getDailyStatistics', result.current.daily.data, null, 0)).toBe(3000)
  })

  it('keeps performance available when daily statistics fail', async () => {
    server.use(
      http.get(`${baseUrl}/statistics/daily`, () => HttpResponse.json({ error: 'invalid report' }, { status: 422 })),
      http.get(`${baseUrl}/performance`, () => HttpResponse.json(performanceResponse)),
    )
    const { result } = renderHook(() => useReportingController({}, { includePerformance: true }), { wrapper: Wrapper })
    await waitFor(() => expect(result.current.performance.data?.owner_available).toBe(true))
    await waitFor(() => expect(result.current.daily.phase).toBe('error'))
    expect(result.current.performance.phase).toBe('ready')
    expect(result.current.presentation).toBe('unavailable')
  })

  it('keeps daily statistics available when controller performance fails', async () => {
    server.use(
      http.get(`${baseUrl}/statistics/daily`, () => HttpResponse.json(dailyResponse())),
      http.get(`${baseUrl}/performance`, () => HttpResponse.json({ error: 'performance unavailable' }, { status: 422 })),
    )
    const { result } = renderHook(() => useReportingController({}, { includePerformance: true }), { wrapper: Wrapper })
    await waitFor(() => expect(result.current.daily.data?.server_time).toBe('2026-11-11T00:00:00Z'))
    await waitFor(() => expect(result.current.performance.phase).toBe('error'))
    expect(result.current.presentation).toBe('available')
  })

  it('keys date filters independently and does not reuse a mismatched response', async () => {
    const requested: string[] = []
    server.use(http.get(`${baseUrl}/statistics/daily`, ({ request }) => {
      const url = new URL(request.url)
      const from = url.searchParams.get('from') ?? 'default'
      requested.push(from)
      return HttpResponse.json(dailyResponse({ start_day: from }))
    }))
    const { result, rerender } = renderHook(({ from }) => useReportingController({ from, to: '2026-11-12' }), {
      initialProps: { from: '2026-11-10' }, wrapper: Wrapper,
    })
    await waitFor(() => expect(result.current.daily.data?.start_day).toBe('2026-11-10'))
    rerender({ from: '2026-11-11' })
    await waitFor(() => expect(result.current.daily.data?.start_day).toBe('2026-11-11'))
    expect(requested).toEqual(['2026-11-10', '2026-11-11'])
  })
})
