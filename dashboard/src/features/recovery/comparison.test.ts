import { describe, expect, it } from 'vitest'
import { buildRecoveryComparison } from './comparison'
import type { components } from '../../core/api/schema'
const change: components['schemas']['RecoveryPositionRepairOutput'] = { id: 'position-1', pair: 'LIFE-USDT', connector: 'okx_perpetual', controller_id: 'controller-1',
  valuation_basis: 'reconstructed_average_entry_not_live_pnl',
  before: { side: 'BUY', amount: '1.000000001', breakeven_price: '0.5', cum_fees_quote: '0.000001', realized_pnl_quote: '-100', unrealized_pnl_quote: '-20', volume_traded_quote: '9007199254740993' },
  after: { side: 'BUY', amount: '1.000000001', breakeven_price: '0.6', cum_fees_quote: '0.000001', realized_pnl_quote: '0', unrealized_pnl_quote: '0', volume_traded_quote: '9007199254740993' } }
describe('recovery complete comparison', () => {
  it('exposes every before/after value with basis and no live-PnL/refund claim', () => {
    const model = buildRecoveryComparison([change])[0]
    expect(model.rows).toHaveLength(7)
    expect(model.rows.find((row) => row.field === 'realized_pnl_quote')).toMatchObject({ before: '-100', after: '0', unit: 'quote' })
    expect(model.rows.find((row) => row.field === 'volume_traded_quote')?.after).toBe('9007199254740993')
    expect(model.explanation).toContain('does not imply a loss refund')
    expect(model.valuationBasis).toBe(change.valuation_basis)
    expect(JSON.stringify(model)).not.toContain('confirmation_token')
  })
  it('does not fabricate rows when a check has no changes', () => expect(buildRecoveryComparison()).toEqual([]))
})
