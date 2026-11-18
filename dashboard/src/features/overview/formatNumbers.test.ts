import { describe, expect, it } from 'vitest'
import { formatPriceForDisplay, formatQuantityForDisplay, formatRiskValueForDisplay, formatUsdtForDisplay } from './formatNumbers'

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

  it('keeps tiny non-zero fee values visible instead of rounding to 0.00', () => {
    expect(formatUsdtForDisplay('0.000000001')).toBe('0.000000001')
    expect(formatUsdtForDisplay('0.0000123456')).toBe('0.000012346')
    expect(formatUsdtForDisplay('0')).toBe('0.00')
    expect(formatUsdtForDisplay('0.004')).toBe('0.004')
  })
})

describe('formatQuantityForDisplay', () => {
  it('keeps small fractional amounts instead of rounding to 0', () => {
    expect(formatQuantityForDisplay('0.0498')).toBe('0.0498')
    expect(formatQuantityForDisplay('0.049800000000000001')).toBe('0.0498')
  })
})

describe('formatRiskValueForDisplay', () => {
  it('formats USDT values with 2 decimals like other USDT figures', () => {
    expect(formatRiskValueForDisplay('1225.614492738332', 'USDT')).toBe('1,225.61')
    expect(formatRiskValueForDisplay('-1366.2051217303832', 'USDT')).toBe('-1,366.21')
  })

  it('formats non-USDT units with 5 significant digits', () => {
    expect(formatRiskValueForDisplay('0.04980000000001', 'BTC')).toBe('0.0498')
  })

  it('returns em dash for missing values regardless of unit', () => {
    expect(formatRiskValueForDisplay(null, 'USDT')).toBe('—')
    expect(formatRiskValueForDisplay(undefined, 'BTC')).toBe('—')
  })
})
