import { describe, expect, it } from 'vitest'
import { fromDateTimeLocalString, toDateTimeLocalString } from './dateTime'

describe('dateTime utilities', () => {
  it('converts ISO string to local datetime string and handles invalid values', () => {
    expect(toDateTimeLocalString('')).toBe('')
    expect(toDateTimeLocalString(null)).toBe('')
    expect(toDateTimeLocalString(undefined)).toBe('')
    expect(toDateTimeLocalString('invalid')).toBe('')

    const sample = '2026-11-10T00:00:00.000Z'
    const local = toDateTimeLocalString(sample)
    expect(local).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/)
  })

  it('converts local datetime string to ISO string and handles invalid values', () => {
    expect(fromDateTimeLocalString('')).toBeUndefined()
    expect(fromDateTimeLocalString(null)).toBeUndefined()
    expect(fromDateTimeLocalString(undefined)).toBeUndefined()
    expect(fromDateTimeLocalString('invalid')).toBeUndefined()

    const localStr = '2026-11-12T14:30'
    const iso = fromDateTimeLocalString(localStr)
    expect(iso).toBeDefined()
    expect(iso).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/)
  })
})
