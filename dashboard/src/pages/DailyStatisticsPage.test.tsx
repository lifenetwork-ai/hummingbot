import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { http, HttpResponse } from 'msw'
import type { ReadResponse } from '../core/api/admin'
import { setupPageApi, pageApiUrl, pageWrapper } from '../test/operationalTest'
import { server } from '../test/mocks/server'
import { DailyStatisticsPage } from './DailyStatisticsPage'
setupPageApi()
export const daily: ReadResponse<'getDailyStatistics'> = {
  balance_scope: 'account_last_observed', balances: [], coverage: 'observed_recorder_events', end_day: '2026-11-12', start_day: '2026-11-10',
  freshness: 'stale', funding_precision: 'decimal_bills_with_legacy_float', funding_sync: null, message: 'Cached', observation_age_seconds: 2,
  reporting_timezone: 'UTC', retry_after_seconds: 3, server_time: '2026-11-11T01:00:00Z', trade_storage_scale: null,
  markets: [{ connector: 'okx_perpetual', market: 'perpetual', funding_status: 'observed_only', days: [{
    day: '2026-11-10', buy_volume_base: '1', buy_volume_quote: '9007199254740993.125', sell_volume_base: '0', sell_volume_quote: '0',
    converted_fill_count: 0, unconverted_fill_count: 2, fill_count: 2, percentage_fee_fill_count: 0, fee_in_usdt: null,
    fees: [{ currency: 'USDT', paid: '0.0000001', rebates: '0', net: '0.0000001' }], funding_paid: null, funding_received: null,
    last_event_at: null, observed_at: '2026-11-11T01:00:00Z', source: 'recorder_tables',
  }] }],
}
describe('DailyStatisticsPage integrated reporting', () => {
  it('uses server timezone and stale hint while retaining exact financial values and gaps', async () => {
    server.use(http.get(`${pageApiUrl}/statistics/daily`, () => HttpResponse.json(daily)))
    render(<DailyStatisticsPage />, { wrapper: pageWrapper() })
    await screen.findByText('Reporting timezone: UTC')
    expect(screen.getByText(/retry after 3 seconds/)).toBeInTheDocument()
    expect(screen.getAllByTitle('9007199254740993.125').length).toBeGreaterThan(0)
    expect(screen.queryByText('100.0%')).not.toBeInTheDocument()
    expect(screen.getAllByText('2026-11-11').length).toBeGreaterThan(0)
    expect(screen.getByText(/settlement basis/)).toBeInTheDocument()
  })
  it('blocks invalid date ranges without sending another report request', async () => {
    let calls = 0
    server.use(http.get(`${pageApiUrl}/statistics/daily`, () => { calls++; return HttpResponse.json(daily) }))
    render(<DailyStatisticsPage />, { wrapper: pageWrapper() })
    await screen.findByText('Reporting timezone: UTC')
    fireEvent.change(screen.getByLabelText('From day (Inclusive):'), { target: { value: '2026-11-12' } })
    fireEvent.change(screen.getByLabelText('To day (Exclusive):'), { target: { value: '2026-11-10' } })
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('reversed_range'))
    expect(calls).toBe(1)
  })
  it('keeps daily statistics available when the newly mounted controller performance source fails', async () => {
    server.use(http.get(`${pageApiUrl}/statistics/daily`, () => HttpResponse.json({ ...daily, freshness: 'fresh' })),
      http.get(`${pageApiUrl}/performance`, () => HttpResponse.json({ detail: 'Performance temporarily unavailable' }, { status: 422 })))
    render(<DailyStatisticsPage />, { wrapper: pageWrapper() })
    await screen.findByText('Reporting timezone: UTC')
    expect(await screen.findByText('Performance temporarily unavailable')).toBeInTheDocument()
    expect(screen.getAllByTitle('9007199254740993.125').length).toBeGreaterThan(0)
    expect(screen.getByRole('heading', { name: /Controller Performance/ })).toBeInTheDocument()
  })

})
