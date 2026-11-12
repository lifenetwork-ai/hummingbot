import type { ReadResponse } from '../../core/api/admin'

type DailyStatistics = ReadResponse<'getDailyStatistics'>
type Performance = ReadResponse<'getPerformance'>

export type ReportingRange = { from?: string; to?: string }
export type RangeValidation = { valid: true } | { valid: false; reason: 'invalid_date' | 'incomplete_range' | 'reversed_range' | 'range_too_large' }

const DAY_MS = 86_400_000
const ISO_DAY = /^\d{4}-\d{2}-\d{2}$/
function parseDay(value: string): number | null {
  if (!ISO_DAY.test(value)) return null
  const date = new Date(`${value}T00:00:00.000Z`)
  return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value ? date.getTime() : null
}

/** API ranges are [from, to), and the server accepts at most 90 reporting dates. */
export function validateReportingRange(range: ReportingRange): RangeValidation {
  if (range.from === undefined && range.to === undefined) return { valid: true }
  if (range.from === undefined || range.to === undefined) return { valid: false, reason: 'incomplete_range' }
  const from = parseDay(range.from)
  const to = parseDay(range.to)
  if (from === null || to === null) return { valid: false, reason: 'invalid_date' }
  if (to <= from) return { valid: false, reason: 'reversed_range' }
  if ((to - from) / DAY_MS > 90) return { valid: false, reason: 'range_too_large' }
  return { valid: true }
}

export type Source<T> = { phase: 'loading' | 'ready' | 'error'; data?: T; error: Error | null; refreshing: boolean }

/** Preserve decimal strings and nulls; absent reporting days are explicit gaps. */
export function buildReportingTimeline(daily: DailyStatistics | undefined, range: ReportingRange) {
  if (!daily) return []
  const first = range.from ?? daily.start_day
  const end = range.to ?? daily.end_day
  const startMs = parseDay(first)
  const endMs = parseDay(end)
  if (startMs === null || endMs === null || endMs <= startMs || (endMs - startMs) / DAY_MS > 90) return []
  const days = Array.from({ length: (endMs - startMs) / DAY_MS }, (_, index) =>
    new Date(startMs + index * DAY_MS).toISOString().slice(0, 10))
  return days.map((day) => ({
    day,
    // A missing bucket is null across every metric, never an interpolated or zero value.
    markets: daily.markets.map((market) => {
      const bucket = market.days.find((item) => item.day === day)
      return { market: market.market, connector: market.connector, bucket: bucket ?? null }
    }),
    // API exposes this account-wide series once; do not duplicate it per market.
    balances: daily.balances.filter((balance) => balance.day === day),
  }))
}

export function deriveReportingModel(input: {
  range: ReportingRange
  daily: Source<DailyStatistics>
  performance: Source<Performance>
}) {
  const validation = validateReportingRange(input.range)
  const unavailable = !validation.valid || (input.daily.phase === 'error' && input.daily.data === undefined)
  return {
    range: input.range,
    validation,
    daily: input.daily,
    performance: input.performance,
    freshness: input.daily.data?.freshness ?? null,
    presentation: unavailable || (input.daily.phase !== 'loading' && (!input.daily.data || !input.daily.data.server_time || !input.daily.data.reporting_timezone))
      ? 'unavailable' as const
      : input.daily.phase === 'loading' && !input.daily.data
        ? 'loading' as const
        : input.daily.phase === 'error' || input.daily.data?.freshness === 'stale'
          ? 'updating' as const
          : 'available' as const,
    reportingTimezone: input.daily.data?.reporting_timezone ?? null,
    observedAt: input.daily.data?.server_time ?? null,
    retryAfterSeconds: input.daily.data?.retry_after_seconds ?? null,
    coverage: input.daily.data?.coverage ?? null,
    fundingSync: input.daily.data?.funding_sync ?? null,
    fundingPrecision: input.daily.data?.funding_precision ?? null,
    performanceScope: input.performance.data?.scope ?? null,
    performanceOwnerAvailable: input.performance.data?.owner_available ?? null,
    timeline: buildReportingTimeline(input.daily.data, input.range),
  }
}

export type ReportingModel = ReturnType<typeof deriveReportingModel>
