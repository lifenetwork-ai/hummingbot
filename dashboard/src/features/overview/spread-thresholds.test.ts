import { describe, expect, it } from 'vitest'
import { classifySpreadTone, SPREAD_TONE_THRESHOLDS_BPS } from './spread-thresholds'

describe('classifySpreadTone', () => {
  it('keeps a missing observation neutral', () => {
    expect(classifySpreadTone(null)).toBe('neutral')
  })

  it('marks tight spreads success up to (not including) the good threshold', () => {
    expect(classifySpreadTone(0)).toBe('success')
    expect(classifySpreadTone(SPREAD_TONE_THRESHOLDS_BPS.goodBelow - 0.01)).toBe('success')
  })

  it('marks the good threshold itself warning (boundary belongs to the stricter bucket)', () => {
    expect(classifySpreadTone(SPREAD_TONE_THRESHOLDS_BPS.goodBelow)).toBe('warning')
    expect(classifySpreadTone(SPREAD_TONE_THRESHOLDS_BPS.warnBelow - 0.01)).toBe('warning')
  })

  it('marks spreads at or beyond the warn threshold danger', () => {
    expect(classifySpreadTone(SPREAD_TONE_THRESHOLDS_BPS.warnBelow)).toBe('danger')
    expect(classifySpreadTone(120)).toBe('danger')
  })
})
