import type { components } from '../../core/api/schema'

export type DailyBalance = components['schemas']['DailyBalanceOutput']

export interface BalanceSeriesPoint {
  readonly day: string
  /** Server decimal string (authoritative); null when the day has no record. */
  readonly raw: string | null
  /** Numeric equity for chart geometry only; null when missing or not finite. */
  readonly value: number | null
  /** Percent change vs the series base (the first recorded day = 0%). Chart geometry and display share this number. null when the value is missing. */
  readonly changePct: number | null
}

export interface BalanceSeries {
  readonly key: string
  readonly accountId: string
  readonly currency: string
  /** First positive equity in the range; the index base (=100). */
  readonly base: number
  readonly baseDay: string
  readonly points: readonly BalanceSeriesPoint[]
}

export interface BalanceSeriesModel {
  readonly days: readonly string[]
  readonly series: readonly BalanceSeries[]
}

function toFiniteNumber(value: string | null | undefined): number | null {
  if (value === null || value === undefined) return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

/**
 * Groups daily balance rows into one series per account × currency as percent
 * change from each series' first positive recorded day (that day = 0%) so assets
 * with very different magnitudes share one relative scale. Equity is the
 * position-total column the detail table emphasizes; cash-only rows stay in the
 * table.
 */
export function buildBalanceSeries(balances: readonly DailyBalance[]): BalanceSeriesModel {
  const groups = new Map<string, { accountId: string; currency: string; byDay: Map<string, string | null> }>()
  const daySet = new Set<string>()
  for (const row of balances) {
    daySet.add(row.day)
    const key = `${row.account_id}::${row.currency}`
    let group = groups.get(key)
    if (!group) {
      group = { accountId: row.account_id, currency: row.currency, byDay: new Map() }
      groups.set(key, group)
    }
    group.byDay.set(row.day, row.equity)
  }

  const days = Array.from(daySet).sort()
  const series: BalanceSeries[] = []
  for (const [key, group] of groups) {
    let base: number | null = null
    let baseDay = ''
    for (const day of days) {
      const value = toFiniteNumber(group.byDay.get(day) ?? null)
      if (value !== null && value > 0) {
        base = value
        baseDay = day
        break
      }
    }
    if (base === null) continue
    const points = days.map((day) => {
      const raw = group.byDay.get(day) ?? null
      const value = toFiniteNumber(raw)
      const changePct = value === null ? null : (value / base - 1) * 100
      return { day, raw, value, changePct }
    })
    series.push({ key, accountId: group.accountId, currency: group.currency, base, baseDay, points })
  }
  return { days, series }
}
