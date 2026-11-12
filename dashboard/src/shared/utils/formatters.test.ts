import { describe, expect, it } from 'vitest'
import {
  formatAssetAmount,
  formatDuration,
  formatLife,
  formatPercentage,
  formatPrice,
  formatSpread,
  formatSpreadValue,
  formatSpreadList,
  formatUsdt,
  localizePhase,
  localizeSystemMessage,
} from './formatters'

describe('formatters', () => {
  it('formats USDT correctly and never turns null into 0', () => {
    expect(formatUsdt('1234.5678')).toBe('1,234.57 USDT')
    expect(formatUsdt(100)).toBe('100.00 USDT')
    expect(formatUsdt(null)).toBe('—')
    expect(formatUsdt(undefined)).toBe('—')
    expect(formatUsdt('')).toBe('—')
  })

  it('formats LIFE correctly with 4 decimals', () => {
    expect(formatLife('98765.43219')).toBe('98,765.4322 LIFE')
    expect(formatLife(null)).toBe('—')
  })

  it('formats price with the shared 4-place default, or explicit places', () => {
    expect(formatPrice('0.045230')).toBe('0.0452')
    expect(formatPrice('12500.50')).toBe('12,500.5000')
    expect(formatPrice('72763.83718193778863', 2)).toBe('72,763.84')
    expect(formatPrice(null)).toBe('—')
  })

  it('formats asset amounts dynamically and localizes messages', () => {
    expect(formatAssetAmount('1.23456', 'BTC')).toBe('1.2346 BTC')
    expect(formatAssetAmount('100.5', 'USDT')).toBe('100.50 USDT')
    expect(formatAssetAmount('5.25', 'ETH')).toBe('5.2500 ETH')
    expect(formatAssetAmount(null, 'BTC')).toBe('—')
    expect(localizeSystemMessage('The market is not actively quoting.')).toBe('The market is not actively quoting.')
    expect(localizePhase('starting')).toBe('Starting')
  })

  it('formats spread and percentage', () => {
    expect(formatSpread('12.5')).toBe('12.5000 bps')
    expect(formatSpread(null)).toBe('—')
    expect(formatPercentage(0.994)).toBe('99.4%')
    expect(formatPercentage(99.4, false)).toBe('99.4%')
    expect(formatPercentage(null)).toBe('—')
  })

  it('formats duration', () => {
    expect(formatDuration(3665)).toBe('01:01:05')
    expect(formatDuration(125)).toBe('02:05')
    expect(formatDuration(null)).toBe('—')
  })
  it('preserves huge and tiny nonzero decimals without floating-point loss', () => {
    expect(formatUsdt('9007199254740993.125')).toBe('9,007,199,254,740,993.13 USDT')
    expect(formatUsdt('-9007199254740993.125')).toBe('-9,007,199,254,740,993.13 USDT')
    expect(formatUsdt('0.000000001')).toBe('<0.01 USDT')
    expect(formatUsdt('-0.000000001')).toBe('>-0.01 USDT')
    expect(formatLife('0.00000001')).toBe('<0.0001 LIFE')
    expect(formatPrice('-9007199254740993.000001')).toBe('-9,007,199,254,740,993.0000')
    expect(formatPrice('1e-8')).toBe('<0.0001')
    expect(formatUsdt('0')).toBe('0.00 USDT')
    expect(formatUsdt('1.005')).toBe('1.01 USDT')
  })
  it.each(['12oops', 'Infinity', 'NaN', '.', '--1', '1e99999'])('rejects malformed decimal %s', (value) => {
    expect(formatUsdt(value)).toBe('—')
    expect(formatPrice(value)).toBe('—')
  })
  it('rejects non-finite duration and retains negative tiny prices', () => {
    expect(formatDuration(Infinity)).toBe('—')
    expect(formatDuration(NaN)).toBe('—')
    expect(formatPrice('-0.0001')).toBe('-0.0001')
    expect(formatPercentage('0.0000001')).toBe('<0.1%')
  })

})

describe('bps display rounding', () => {
  it.each([
    ['12.34565', '12.3457 bps'],
    ['-12.34565', '-12.3457 bps'],
    ['0', '0.0000 bps'],
    ['1e-8', '<0.0001 bps'],
    ['-1e-8', '>-0.0001 bps'],
    ['9007199254740993.12345', '9,007,199,254,740,993.1235 bps'],
  ])('formats %s without losing decimal precision', (value, expected) => {
    expect(formatSpread(value)).toBe(expected)
  })
  it.each([null, undefined, '', '12oops', Infinity, NaN])('rejects missing or invalid bps %s', (value) => {
    expect(formatSpread(value)).toBe('—')
  })
  it('converts fractional configuration spreads to bps before rounding', () => {
    expect(formatSpreadValue('0.001234565')).toBe('12.3457 bps (0.12%)')
    expect(formatSpreadValue('-0.001234565')).toBe('-12.3457 bps (-0.12%)')
    expect(formatSpreadValue('1e-9')).toBe('<0.0001 bps (<0.01%)')
    expect(formatSpreadValue('0')).toBe('0.0000 bps (0%)')
    expect(formatSpreadValue('12oops')).toBe('—')
    expect(formatSpreadValue(Infinity)).toBe('—')
    expect(formatSpreadList(['0.001', '0.002'])).toBe('10.0000 bps (0.1%), 20.0000 bps (0.2%)')
    expect(formatSpreadList([])).toBe('—')
    expect(formatSpreadList(null)).toBe('—')
  })
})
