import { describe, expect, it } from 'vitest'
import type { OverviewModel } from './model'
import { summarizePositions } from './position-summary'
import { completionOverview } from '../../test/mocks/completionFixtures'

type Markets = OverviewModel['positionMarkets']
const market = (items: Markets[number]['positions'], observation: Markets[number]['observation'] = 'fresh'): Markets[number] => ({
  market: 'perpetual', connector: 'okx_perpetual', coverage: 'observed', source: 'okx_account_positions', observed_at: '2026-11-12T00:00:00Z',
  stale: observation !== 'fresh', observation, positions: items,
})
const position = (side: 'LONG' | 'SHORT', amount: string, pair = 'LIFE-USDT', pnl = '1', observation: 'fresh' | 'stale' = 'fresh') => ({
  side, amount, pair, unrealized_pnl: pnl, entry_price: '10', leverage: '1', liquidation_price: null,
  margin: null, margin_mode: null, observed_at: '2026-11-12T00:00:00Z', stale: observation !== 'fresh', observation,
}) as Markets[number]['positions'][number]

describe('summarizePositions', () => {
  it('normalizes backend signed SHORT amounts to positive magnitudes', () => {
    const result = summarizePositions([market([position('SHORT', '-2')])])
    expect(result).toMatchObject({ observed: true, longAmount: 0, shortAmount: 2, gross: 2, netExposure: -2, netPnl: 1 })
  })
  it('calculates LONG and offsetting hedge exposure', () => {
    expect(summarizePositions([market([position('LONG', '3')])])).toMatchObject({ netExposure: 3, longAmount: 3, shortAmount: 0 })
    expect(summarizePositions([market([position('LONG', '3'), position('SHORT', '-2')])])).toMatchObject({ netExposure: 1, gross: 5 })
  })
  it('does not net different instruments', () => {
    expect(summarizePositions([market([position('LONG', '1', 'BTC-USDT'), position('SHORT', '-1', 'ETH-USDT')])])).toMatchObject({ sameInstrument: false, netExposure: null })
  })
  it('does not sum PnL when quote currencies are not established as USDT', () => {
    expect(summarizePositions([market([position('LONG', '1', 'ETH-BTC')])])).toMatchObject({ observed: true, netExposure: 1, netPnl: null })
  })
  it('keeps empty unavailable scope unknown and observed empty scope flat', () => {
    expect(summarizePositions([])).toMatchObject({ observed: false, netExposure: null, netPnl: null })
    expect(summarizePositions([market([])])).toMatchObject({ observed: true, positions: [], netExposure: 0, netPnl: 0 })
  })
  it('ignores not-applicable markets but requires an applicable current observation', () => {
    const notApplicable = { ...market([], 'unknown'), coverage: 'not_applicable' as const }
    expect(summarizePositions([notApplicable])).toMatchObject({ observed: false, netExposure: null })
    expect(summarizePositions([{ ...notApplicable }, market([])])).toMatchObject({ observed: true, netExposure: 0 })
  })
  it('does not infer flat from stale parent or stale item observations', () => {
    expect(summarizePositions([market([], 'stale')])).toMatchObject({ observed: false, netExposure: null })
    expect(summarizePositions([market([position('LONG', '1', 'LIFE-USDT', '1', 'stale')])])).toMatchObject({ observed: false, netExposure: null, netPnl: null })
  })
  it('marks malformed quantity, PnL, or side unknown', () => {
    const invalid = position('LONG', 'NaN', 'LIFE-USDT', 'NaN')
    expect(summarizePositions([market([invalid])])).toMatchObject({ observed: false, netExposure: null, netPnl: null })
    const unknownSide = { ...position('LONG', '1'), side: 'UNKNOWN' } as unknown as Markets[number]['positions'][number]
    expect(summarizePositions([market([unknownSide])])).toMatchObject({ observed: false, netExposure: null })
    expect(summarizePositions([market([position('LONG', '')])])).toMatchObject({ observed: false, netExposure: null })
  })
  it('accepts the shared completion fixture shape', () => {
    expect(summarizePositions(completionOverview.positionMarkets).observed).toBe(false)
  })
})
