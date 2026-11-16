import { describe, expect, it } from 'vitest'
import { formatPriceForDisplay, formatUsdtForDisplay } from './formatNumbers'

describe('formatPriceForDisplay', () => {
  it('rounds long decimal prices to 5 significant digits with thousand separators', () => {
    expect(formatPriceForDisplay('12345.678901')).toBe('12,346')
    expect(formatPriceForDisplay('0.123456789')).toBe('0.12346')
  })

  it('drops trailing zeros', () => {
    expect(formatPriceForDisplay('0.100000')).toBe('0.1')
    expect(formatPriceForDisplay('4120.00')).toBe('4,120')
  })

  it('returns null for missing or unparsable values', () => {
    expect(formatPriceForDisplay(null)).toBeNull()
    expect(formatPriceForDisplay(undefined)).toBeNull()
    expect(formatPriceForDisplay('abc')).toBeNull()
  })

  it('accepts pre-formatted strings with separators or units', () => {
    expect(formatPriceForDisplay('6,120.5')).toBe('6,120.5')
    expect(formatPriceForDisplay('412,000 LIFE')).toBe('412,000')
  })
})

describe('formatUsdtForDisplay', () => {
  it('rounds to 2 decimals with thousand separators', () => {
    expect(formatUsdtForDisplay('182.405')).toBe('182.41')
    expect(formatUsdtForDisplay('12345.678')).toBe('12,345.68')
    expect(formatUsdtForDisplay('12')).toBe('12.00')
  })

  it('keeps + sign only when signed', () => {
    expect(formatUsdtForDisplay('25.5', true)).toBe('+25.50')
    expect(formatUsdtForDisplay('25.5')).toBe('25.50')
  })

  it('keeps minus sign for negatives', () => {
    expect(formatUsdtForDisplay('-25.555')).toBe('-25.56')
    expect(formatUsdtForDisplay('-25.555', true)).toBe('-25.56')
  })

  it('returns null for missing values', () => {
    expect(formatUsdtForDisplay(null)).toBeNull()
  })
})
