import { describe, expect, it } from 'vitest'
import { derivePerformancePanel } from './performance'
import type { ReadResponse } from '../../core/api/admin'
const data: ReadResponse<'getPerformance'> = { scope: 'controller_lifetime_and_active_exposure', owner_available: true, server_time: '2026-11-11T00:00:00Z', markets: [{ market: 'perpetual', coverage: 'observed', funding_status: 'observed_only', observations: [{
  connector: 'okx_perpetual', market: 'perpetual', controller_id: 'c1', pair: 'LIFE-USDT', observed_at: '2026-11-11T00:00:00Z', stale: false,
  source: 'v2_controller_report_cache', valuation_basis: 'executor_and_position_hold_reports', report: { global_pnl_quote: '9007199254740993.000001', global_pnl_pct: '0.1', realized_pnl_quote: '0', realized_pnl_pct: '0', unrealized_pnl_quote: '0', unrealized_pnl_pct: '0', volume_traded: '1', close_type_counts: {}, positions_summary: [] } }], daily_statistics_url: '/statistics/daily' }] }
describe('controller performance source panel', () => {
  it('preserves scoped reports without summing them or converting decimals for display', () => {
    const model = derivePerformancePanel({ phase: 'ready', data, error: null, refreshing: false })
    expect(model.scope).toBe(data.scope)
    expect(model.percentageUnit).toBe('percentage_points')
    expect(model.percentageDenominator).toBe('volume_traded')
    expect(model.rows[0].report.global_pnl_quote).toBe('9007199254740993.000001')
    expect(model.rows[0].observation).toBe('fresh')
    expect(model.rows[0].valuationBasis).toBe('executor_and_position_hold_reports')
    expect(model.explanation).toContain('not total account return')
  })
  it('retains last-good data on errors and does not turn it into current authority', () => {
    expect(derivePerformancePanel({ phase: 'error', data, error: new Error('unavailable'), refreshing: false }).rows[0].observation).toBe('stale')
    expect(derivePerformancePanel({ phase: 'ready', data: { ...data, owner_available: false }, error: null, refreshing: false }).rows[0].observation).toBe('unknown')
    expect(derivePerformancePanel({ phase: 'loading', error: null, refreshing: true }).rows).toEqual([])
  })
})
