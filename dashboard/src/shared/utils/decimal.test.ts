import { describe, expect, it } from 'vitest'
import { subtractDecimalStrings, sumDecimalStrings } from './decimal'
describe('exact decimal arithmetic for reports', () => {
  it('preserves large/tiny values and opposite signs without floating-point roundoff', () => {
    expect(sumDecimalStrings(['9007199254740993.125', '0.000000001'])).toBe('9007199254740993.125000001')
    expect(sumDecimalStrings(['-1.25', '1.250'])).toBe('0.000')
    expect(subtractDecimalStrings('0.000000001', '0.1')).toBe('-0.099999999')
    expect(subtractDecimalStrings('1', '-2')).toBe('3')
  })
  it('does not replace absent/malformed observations with zero', () => {
    expect(sumDecimalStrings([])).toBeNull()
    expect(sumDecimalStrings(['0', null])).toBeNull()
    expect(sumDecimalStrings(['invalid'])).toBeNull()
    expect(subtractDecimalStrings('1', null)).toBeNull()
  })
})
