import { describe, expect, it } from 'vitest'
import type { components } from '../../core/api/schema'
import { buildChartSeries, summarizeReporting } from './series'
type Bucket = components['schemas']['DailyBucketOutput']
const bucket: Bucket = { day: '2026-11-10', buy_volume_base: '1', buy_volume_quote: '9007199254740993.00001', sell_volume_base: '0', sell_volume_quote: '0',
  converted_fill_count: 1, fill_count: 2, unconverted_fill_count: 1, percentage_fee_fill_count: 0, fee_in_usdt: null,
  fees: [{ currency: 'BTC', paid: '0.0001', rebates: '0.00001', net: '0.00009' }], funding_paid: null, funding_received: '0', last_event_at: null, observed_at: '', source: 'recorder_tables' }
describe('report series semantics', () => {
  it('keeps incomplete days and unobserved funding as gaps, independently of observed volume', () => {
    const rows = buildChartSeries([{ day: '2026-11-10', balances: [], markets: [{ market: 'perpetual', connector: 'okx_perpetual', bucket }] },
      { day: '2026-11-11', balances: [], markets: [{ market: 'perpetual', connector: 'okx_perpetual', bucket: null }] }])
    expect(rows[0]).toMatchObject({ buy: '9007199254740993.00001', total: '9007199254740993.00001', fundingNet: null })
    expect(rows[1]).toMatchObject({ buy: null, sell: null, total: null, fills: null, fundingPaid: null })
  })
  it('keeps fee currencies and paid/rebates separate, computes count coverage instead of inventing 100%', () => {
    const summary = summarizeReporting([{ connector: 'okx_perpetual', market: 'perpetual', funding_status: 'observed_only', days: [bucket] }])
    expect(summary.fees).toEqual([{ currency: 'BTC', paid: '0.0001', rebates: '0.00001' }])
    expect(summary.conversionFraction).toBe(0.5)
    expect(summary.fundingPaid).toBeNull()
    expect(summarizeReporting([])).toMatchObject({ buy: null, fundingPaid: null, conversionFraction: null })
  })
})
