import type { ReportingRange } from './model'

/** The server rejects any daily range above ninety reporting days (admin_reporting.day_range). */
export const REPORTING_RANGE_LIMIT_DAYS = 90

export type ReportingPresetDays = 7 | 30 | 90
export type ReportingPreset = ReportingPresetDays | 'custom'

export const REPORTING_PRESETS: readonly { days: ReportingPresetDays; label: string }[] = [
  { days: 7, label: 'Last 7 days' },
  { days: 30, label: 'Last 30 days' },
  { days: 90, label: 'Last 90 days (max)' },
]

const ISO_DAY = /^\d{4}-\d{2}-\d{2}$/

function toIsoDay(ms: number): string {
  return new Date(ms).toISOString().slice(0, 10)
}

function parseIsoDay(value: string): number | null {
  if (!ISO_DAY.test(value)) return null
  const ms = Date.parse(`${value}T00:00:00.000Z`)
  return Number.isFinite(ms) ? ms : null
}

/** Calendar day of `now` in `timeZone` as YYYY-MM-DD; falls back to the local zone for unknown zones. */
export function dayInZone(now: Date, timeZone: string | null): string {
  try {
    return new Intl.DateTimeFormat('en-CA', {
      timeZone: timeZone ?? undefined, year: 'numeric', month: '2-digit', day: '2-digit',
    }).format(now)
  } catch {
    return new Intl.DateTimeFormat('en-CA', { year: 'numeric', month: '2-digit', day: '2-digit' }).format(now)
  }
}

/**
 * [from, to) covering the last `days` reporting days ending tomorrow, mirroring the
 * server's `day_range` default (end = today + 1, exclusive) in the reporting timezone.
 */
export function presetReportingRange(days: ReportingPresetDays, timeZone: string | null, now = new Date()): ReportingRange {
  const today = dayInZone(now, timeZone)
  const todayMs = parseIsoDay(today)
  if (todayMs === null) return {}
  const to = toIsoDay(todayMs + 86_400_000)
  const from = toIsoDay(todayMs - (days - 1) * 86_400_000)
  return { from, to }
}

/**
 * Query range for the active preset. The 7-day preset sends no parameters so the
 * server default (exactly the last seven reporting days in its own timezone) stays
 * authoritative; longer presets are computed in the reporting timezone once known.
 */
export function reportingRangeForPreset(
  preset: ReportingPreset,
  custom: ReportingRange,
  timeZone: string | null,
  now = new Date(),
): ReportingRange {
  if (preset === 'custom') return custom
  if (preset === 7) return {}
  return presetReportingRange(preset, timeZone, now)
}
