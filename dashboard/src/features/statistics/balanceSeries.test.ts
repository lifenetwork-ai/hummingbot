import { describe, expect, it } from 'vitest'
import { buildBalanceSeries } from './balanceSeries'
import type { DailyBalance } from './balanceSeries'

type Row = Partial<DailyBalance> & { day: string; account_id: string; currency: string; equity: string | null }

function rows(data: Row[]): DailyBalance[] {
  return data.map((row) => ({
    available_cash: null,
    available_equity: null,
    cash_balance: null,
    connector: 'binance',
    ...row,
  })) as DailyBalance[]
}

/** Floating-point tolerant comparison for arrays of percent changes. */
function expectChanges(actual: readonly (number | null)[], expected: readonly (number | null)[]) {
  expect(actual).toHaveLength(expected.length)
  actual.forEach((value, i) => {
    if (expected[i] === null) expect(value).toBeNull()
    else expect(value).toBeCloseTo(expected[i] as number)
  })
}

describe('buildBalanceSeries', () => {
  it('unions and sorts days across all series', () => {
    const model = buildBalanceSeries(rows([
      { day: '2026-11-09', account_id: 'a1', currency: 'USDT', equity: '200' },
      { day: '2026-11-08', account_id: 'a1', currency: 'USDT', equity: '100' },
      { day: '2026-11-10', account_id: 'a1', currency: 'BTC', equity: '1' },
    ]))
    expect(model.days).toEqual(['2026-11-08', '2026-11-09', '2026-11-10'])
  })

  it('measures each asset as percent change from its first positive recorded day (0)', () => {
    const model = buildBalanceSeries(rows([
      { day: '2026-11-08', account_id: 'a1', currency: 'USDT', equity: '10000' },
      { day: '2026-11-09', account_id: 'a1', currency: 'USDT', equity: '11000' },
      { day: '2026-11-08', account_id: 'a1', currency: 'BTC', equity: '0.5' },
      { day: '2026-11-09', account_id: 'a1', currency: 'BTC', equity: '0.4' },
    ]))
    expect(model.series).toHaveLength(2)
    const usdt = model.series.find((series) => series.currency === 'USDT')
    const btc = model.series.find((series) => series.currency === 'BTC')
    expect(usdt?.baseDay).toBe('2026-11-08')
    expectChanges(usdt?.points.map((point) => point.changePct) ?? [], [0, 10])
    // Different magnitude, same relative scale: 0.4 vs 0.5 also reads as -20.
    expectChanges(btc?.points.map((point) => point.changePct) ?? [], [0, -20])
  })

  it('keeps missing days as null gaps instead of interpolating', () => {
    const model = buildBalanceSeries(rows([
      { day: '2026-11-08', account_id: 'a1', currency: 'USDT', equity: '100' },
      { day: '2026-11-09', account_id: 'a1', currency: 'USDT', equity: null },
      { day: '2026-11-10', account_id: 'a1', currency: 'USDT', equity: '120' },
    ]))
    expectChanges(model.series[0]?.points.map((point) => point.changePct) ?? [], [0, null, 20])
    expect(model.series[0]?.points[1]?.raw).toBeNull()
  })

  it('bases the change on the first day with a positive value, skipping leading zeros and nulls', () => {
    const model = buildBalanceSeries(rows([
      { day: '2026-11-08', account_id: 'a1', currency: 'LIFE', equity: '0' },
      { day: '2026-11-09', account_id: 'a1', currency: 'LIFE', equity: null },
      { day: '2026-11-10', account_id: 'a1', currency: 'LIFE', equity: '50' },
    ]))
    expect(model.series[0]?.baseDay).toBe('2026-11-10')
    expect(model.series[0]?.base).toBe(50)
    // Vs the base (50), the leading zero day reads -100%.
    expectChanges(model.series[0]?.points.map((point) => point.changePct) ?? [], [-100, null, 0])
  })

  it('excludes series whose equity is never positive (nothing to index)', () => {
    const model = buildBalanceSeries(rows([
      { day: '2026-11-08', account_id: 'a1', currency: 'USDT', equity: '100' },
      { day: '2026-11-08', account_id: 'a1', currency: 'DUST', equity: '0' },
    ]))
    expect(model.series.map((series) => series.currency)).toEqual(['USDT'])
  })

  it('splits same currency held on different accounts into separate series', () => {
    const model = buildBalanceSeries(rows([
      { day: '2026-11-08', account_id: 'a1', currency: 'USDT', equity: '100' },
      { day: '2026-11-08', account_id: 'a2', currency: 'USDT', equity: '500' },
    ]))
    expect(model.series).toHaveLength(2)
    expect(new Set(model.series.map((series) => series.accountId))).toEqual(new Set(['a1', 'a2']))
  })
})
