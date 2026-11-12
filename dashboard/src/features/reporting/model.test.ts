import { describe, expect, it } from 'vitest'
import type { ReadResponse } from '../../core/api/admin'
import { buildReportingTimeline, deriveReportingModel, validateReportingRange } from './model'

type Daily = ReadResponse<'getDailyStatistics'>
type Performance = ReadResponse<'getPerformance'>
const daily = (overrides: Partial<Daily> = {}): Daily => ({
  balance_scope: 'account_last_observed', balances: [{ account_id: 'acct', available_cash: null, available_equity: null,
    cash_balance: '0.00000001', connector: 'okx', currency: 'USDT', day: '2026-11-10', environment: 'demo',
    equity: null, observed_at: '2026-11-10T01:00:00Z', reporting_timezone: 'Asia/Ho_Chi_Minh', source: 'account', updated_at: '2026-11-10T01:00:00Z' }],
  coverage: 'observed_recorder_events', end_day: '2026-11-12', freshness: 'fresh', funding_precision: 'decimal_bills_with_legacy_float', funding_sync: null,
  markets: [{ connector: 'okx', funding_status: 'observed_only', market: 'perpetual', days: [{
    buy_volume_base: '0.00000001', buy_volume_quote: '0.00000002', converted_fill_count: 0, day: '2026-11-10', fee_in_usdt: null, fees: [], fill_count: 0,
    funding_paid: null, funding_received: '0', last_event_at: null, observed_at: '2026-11-10T01:00:00Z', percentage_fee_fill_count: 0,
    sell_volume_base: '0', sell_volume_quote: '0', source: 'recorder_tables', unconverted_fill_count: 0,
  }] }], message: null, observation_age_seconds: null, reporting_timezone: 'Asia/Ho_Chi_Minh', retry_after_seconds: null,
  server_time: '2026-11-10T01:00:00Z', start_day: '2026-11-10', trade_storage_scale: null, ...overrides,
})
const performance: Performance = { markets: [], owner_available: false, scope: 'controller_lifetime_and_active_exposure', server_time: '2026-11-10T01:00:00Z' }
const source = <T,>(data: T, phase: 'ready' | 'error' = 'ready') => ({ phase, data, error: phase === 'error' ? new Error('refresh failed') : null, refreshing: phase === 'error' })

describe('reporting model', () => {
  it('validates optional date ranges as half-open, bounded calendar ranges', () => {
    expect(validateReportingRange({})).toEqual({ valid: true })
    expect(validateReportingRange({ from: '2026-11-01' })).toEqual({ valid: false, reason: 'incomplete_range' })
    expect(validateReportingRange({ from: '2026-02-29', to: '2026-03-01' })).toEqual({ valid: false, reason: 'invalid_date' })
    expect(validateReportingRange({ from: '2026-11-02', to: '2026-11-02' })).toEqual({ valid: false, reason: 'reversed_range' })
    expect(validateReportingRange({ from: '2026-01-01', to: '2026-04-02' })).toEqual({ valid: false, reason: 'range_too_large' })
    expect(validateReportingRange({ from: '2024-02-28', to: '2024-03-01' })).toEqual({ valid: true })
  })

  it('keeps missing dates as gaps, balances once, and financial values as exact strings/nulls', () => {
    const response = daily()
    const timeline = buildReportingTimeline(response, { from: '2026-11-10', to: '2026-11-12' })
    expect(timeline).toHaveLength(2)
    expect(timeline[0].markets[0].bucket?.buy_volume_base).toBe('0.00000001')
    expect(timeline[0].markets[0].bucket?.funding_paid).toBeNull()
    expect(timeline[1].markets[0].bucket).toBeNull()
    expect(timeline[0].balances).toHaveLength(1)
    expect(timeline[1].balances).toEqual([])
  })

  it('uses server reporting timezone and original observation when stale fallback is served', () => {
    const model = deriveReportingModel({ range: {}, daily: source(daily({ freshness: 'stale', retry_after_seconds: 3,
      observation_age_seconds: 8, reporting_timezone: 'Asia/Ho_Chi_Minh' })), performance: source(performance) })
    expect(model.presentation).toBe('updating')
    expect(model.reportingTimezone).toBe('Asia/Ho_Chi_Minh')
    expect(model.observedAt).toBe('2026-11-10T01:00:00Z')
    expect(model.retryAfterSeconds).toBe(3)
  })

  it('keeps unavailable and report/performance failures independent', () => {
    const unavailable = deriveReportingModel({ range: {}, daily: { phase: 'error', error: new Error('503'), refreshing: false }, performance: source(performance) })
    expect(unavailable.presentation).toBe('unavailable')
    const reportFailed = deriveReportingModel({ range: {}, daily: source(daily(), 'error'),
      performance: { phase: 'error', error: new Error('503'), refreshing: false } })
    expect(reportFailed.presentation).toBe('updating')
    expect(reportFailed.daily.data?.markets[0].days[0].buy_volume_base).toBe('0.00000001')
    expect(reportFailed.performance.phase).toBe('error')
    const initial = deriveReportingModel({ range: {}, daily: { phase: 'loading', error: null, refreshing: true }, performance: source(performance) })
    expect(initial.presentation).toBe('loading')
    expect(deriveReportingModel({ range: {}, daily: source(daily({ server_time: '' })), performance: source(performance) }).presentation).toBe('unavailable')
    expect(deriveReportingModel({ range: { from: 'bad', to: 'also-bad' }, daily: { phase: 'loading', error: null, refreshing: false }, performance: source(performance) }).presentation).toBe('unavailable')
  })
})
