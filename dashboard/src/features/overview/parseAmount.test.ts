import { describe, expect, it } from 'vitest'
import { parseAmount } from './parseAmount'

describe('parseAmount', () => {
  it('parses plain and signed numbers', () => {
    expect(parseAmount('0.0412')).toBe(0.0412)
    expect(parseAmount('+182.40')).toBe(182.4)
    expect(parseAmount('-84.20')).toBe(-84.2)
  })

  it('strips thousands separators and units', () => {
    expect(parseAmount('6,120')).toBe(6120)
    expect(parseAmount('412,000 LIFE')).toBe(412000)
    expect(parseAmount('171,182.40')).toBeCloseTo(171182.4)
  })

  it('returns null for missing or unparseable values', () => {
    expect(parseAmount(null)).toBeNull()
    expect(parseAmount(undefined)).toBeNull()
    expect(parseAmount('—')).toBeNull()
  })
})
