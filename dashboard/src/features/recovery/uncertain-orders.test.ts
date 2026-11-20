import { describe, expect, it } from 'vitest'
import { exactUncertainOrderRepairs, normalizeUncertainOrderRepairs, uncertainOrderQuery, uncertainOrderRequest, validateUncertainOrderIds } from './uncertain-orders'

describe('uncertain order scope', () => {
  it('accepts one ID or a distinct batch of two through eight exact IDs', () => {
    expect(validateUncertainOrderIds(['Order001'])).toBeNull()
    expect(validateUncertainOrderIds(['Order001', 'Order002'])).toBeNull()
    expect(validateUncertainOrderIds(Array.from({ length: 8 }, (_, i) => `Order${i}`))).toBeNull()
  })

  it.each([
    [[''], 'Order IDs must contain 1–32 letters or numbers.'],
    [['order-id'], 'Order IDs must contain 1–32 letters or numbers.'],
    [['x'.repeat(33)], 'Order IDs must contain 1–32 letters or numbers.'],
    [['same', 'same'], 'Order IDs must be distinct.'],
    [Array.from({ length: 9 }, (_, i) => `Order${i}`), 'Review one order or a batch of 2–8 orders.'],
  ] as const)('rejects invalid IDs %j', (ids, message) => {
    expect(validateUncertainOrderIds(ids)).toBe(message)
  })

  it('maps one ID to singular query/body and batches to plural query/body', () => {
    expect(uncertainOrderQuery(['A1'])).toEqual({ query: { uncertain_order_id: 'A1' } })
    expect(uncertainOrderRequest(['A1'])).toEqual({ uncertain_order_id: 'A1' })
    expect(uncertainOrderQuery(['A1', 'B2'])).toEqual({ query: { uncertain_order_ids: 'A1,B2' } })
    expect(uncertainOrderRequest(['A1', 'B2'])).toEqual({ uncertain_order_ids: ['A1', 'B2'] })
    expect(uncertainOrderQuery([])).toEqual({})
    expect(uncertainOrderRequest([])).toEqual({})
  })

  it('normalizes singular and plural preview and verifies exact repaired membership independent of ordering', () => {
    const buy = { id: 'A1', side: 'BUY', pair: 'BTC-USDT', amount: '1', price: '10', connector: 'okx', before: 'PendingBuy', after: 'OrderFailure', deadline_ms: 1, evidence: 'expired_submission_and_reconciled_exchange' } as const
    const sell = { ...buy, id: 'B2', side: 'SELL', before: 'PendingSell' } as const
    expect(normalizeUncertainOrderRepairs({ proposed_uncertain_order: buy })).toEqual([buy])
    expect(normalizeUncertainOrderRepairs({ proposed_uncertain_orders: [buy, sell] })).toEqual([buy, sell])
    expect(exactUncertainOrderRepairs(['A1', 'B2'], [sell, buy])).toBe(true)
    expect(exactUncertainOrderRepairs(['A1', 'B2'], [buy])).toBe(false)
    expect(exactUncertainOrderRepairs(['A1', 'B2'], [buy, { ...sell, id: 'C3' }])).toBe(false)
  })
})
