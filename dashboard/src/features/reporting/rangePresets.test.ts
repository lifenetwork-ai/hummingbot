import { describe, expect, it } from 'vitest'
import {
  REPORTING_PRESETS,
  REPORTING_RANGE_LIMIT_DAYS,
  dayInZone,
  presetReportingRange,
  reportingRangeForPreset,
} from './rangePresets'
import { validateReportingRange } from './model'

const DAY_MS = 86_400_000
function spanDays(range: { from?: string; to?: string }): number {
  return (Date.parse(`${range.to}T00:00:00Z`) - Date.parse(`${range.from}T00:00:00Z`)) / DAY_MS
}

describe('dayInZone', () => {
  it('returns the calendar day of the reporting timezone, not UTC', () => {
    // 20:00 UTC on Nov 16 is already Nov 17 in Ho Chi Minh City (UTC+7).
    expect(dayInZone(new Date('2026-11-16T20:00:00Z'), 'Asia/Ho_Chi_Minh')).toBe('2026-11-17')
    expect(dayInZone(new Date('2026-11-16T20:00:00Z'), 'UTC')).toBe('2026-11-16')
  })
  it('falls back to the local zone for unknown timezone identifiers', () => {
    expect(dayInZone(new Date('2026-11-16T20:00:00Z'), 'Mars/Olympus_Mons')).toMatch(/^\d{4}-\d{2}-\d{2}$/)
    expect(dayInZone(new Date('2026-11-16T20:00:00Z'), null)).toMatch(/^\d{4}-\d{2}-\d{2}$/)
  })
})

describe('presetReportingRange', () => {
  it('mirrors the server default window: inclusive from, exclusive tomorrow', () => {
    expect(presetReportingRange(7, 'UTC', new Date('2026-11-16T10:00:00Z'))).toEqual({ from: '2026-11-10', to: '2026-11-17' })
  })
  it('covers exactly the requested number of reporting days', () => {
    for (const days of [30, 90] as const) {
      const range = presetReportingRange(days, 'UTC', new Date('2026-11-16T10:00:00Z'))
      expect(spanDays(range)).toBe(days)
    }
  })
  it('aligns the window to the reporting timezone day', () => {
    const range = presetReportingRange(30, 'Asia/Ho_Chi_Minh', new Date('2026-11-16T20:00:00Z'))
    // Today in UTC+7 is Nov 17: from = Oct 19 (inclusive), to = Nov 18 (exclusive).
    expect(range).toEqual({ from: '2026-10-19', to: '2026-11-18' })
  })
  it('keeps every preset within the server range limit', () => {
    for (const { days } of REPORTING_PRESETS) {
      const range = presetReportingRange(days, 'UTC', new Date('2026-11-16T10:00:00Z'))
      expect(validateReportingRange(range)).toEqual({ valid: true })
      expect(days).toBeLessThanOrEqual(REPORTING_RANGE_LIMIT_DAYS)
    }
  })
})

describe('reportingRangeForPreset', () => {
  it('sends no parameters for the 7-day preset so the server default stays authoritative', () => {
    expect(reportingRangeForPreset(7, {}, 'UTC', new Date('2026-11-16T10:00:00Z'))).toEqual({})
  })
  it('passes custom ranges through untouched', () => {
    expect(reportingRangeForPreset('custom', { from: '2026-01-01', to: '2026-02-01' }, null)).toEqual({ from: '2026-01-01', to: '2026-02-01' })
  })
  it('computes longer presets in the reporting timezone', () => {
    expect(reportingRangeForPreset(30, {}, 'Asia/Ho_Chi_Minh', new Date('2026-11-16T20:00:00Z'))).toEqual({ from: '2026-10-19', to: '2026-11-18' })
  })
})
