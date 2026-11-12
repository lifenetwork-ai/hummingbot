import { describe, expect, it } from 'vitest'
import { validateHistoryFilters } from './filters'
describe('history filters validation', () => {
  it('validates explicit instants without inventing date-only timezone semantics', () => {
    expect(validateHistoryFilters({ from_time: '2026-11-11' }).valid).toBe(false)
    expect(validateHistoryFilters({ from_time: '2026-11-11T00:00:00', to_time: null }).valid).toBe(false)
    expect(validateHistoryFilters({ from_time: '2026-11-11T00:00:00Z', to_time: '2026-11-12T00:00:00+07:00' }).valid).toBe(true)
    expect(validateHistoryFilters({ from_time: '2026-11-12T00:00:00Z', to_time: '2026-11-11T00:00:00Z' }).errors.to_time).toBe('range_must_be_half_open')
  })
  it('retains valid server filters and rejects invalid paging sizes', () => {
    expect(validateHistoryFilters({ action: 'switch', status: 'superseded', session_id: 's1', limit: 500 }).valid).toBe(true)
    expect(validateHistoryFilters({ limit: 501 }).valid).toBe(false)
    expect(validateHistoryFilters({ limit: 0 }).valid).toBe(false)
    expect(validateHistoryFilters({ limit: NaN }).valid).toBe(false)
    expect(validateHistoryFilters({}).valid).toBe(true)
  })
})
