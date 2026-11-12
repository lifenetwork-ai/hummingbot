import type { ReadInput } from '../../core/api/admin'
export type CommandFilters = Omit<NonNullable<ReadInput<'listCommands'>['query']>, 'cursor'>
export type EventFilters = Omit<NonNullable<ReadInput<'getSessionEvents'>['query']>, 'cursor'>
export type AuditFilters = Omit<NonNullable<ReadInput<'getAdminAudit'>['query']>, 'cursor'>
export type HistoryFilters = { commands: CommandFilters; events: EventFilters; audit: AuditFilters }

/** API time filters are UTC instants, not date-only values with an assumed browser timezone. */
export function validateHistoryFilters(input: CommandFilters | EventFilters | AuditFilters) {
  const errors: Record<string, string> = {}
  if (input.limit !== undefined && (!Number.isSafeInteger(input.limit) || input.limit < 1 || input.limit > 500)) errors.limit = 'limit_out_of_range'
  if ('from_time' in input || 'to_time' in input) {
    const from = 'from_time' in input ? input.from_time : null
    const to = 'to_time' in input ? input.to_time : null
    const valid = (value: unknown) => value === null || value === undefined || typeof value === 'string' && /T.*(?:Z|[+-]\d{2}:\d{2})$/.test(value) && Number.isFinite(Date.parse(value))
    if (!valid(from)) errors.from_time = 'utc_instant_required'
    if (!valid(to)) errors.to_time = 'utc_instant_required'
    if (typeof from === 'string' && typeof to === 'string' && valid(from) && valid(to) && Date.parse(from) >= Date.parse(to)) errors.to_time = 'range_must_be_half_open'
  }
  return { valid: Object.keys(errors).length === 0, errors }
}
