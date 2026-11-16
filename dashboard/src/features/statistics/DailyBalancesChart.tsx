import { useMemo, useState } from 'react'
import { ChevronDown, Wallet } from 'lucide-react'
import type { components } from '../../core/api/schema'
import { Surface } from '../../shared/components/Surface'
import { formatAssetAmount } from '../../shared/utils/formatters'
import { cn } from '../../shared/utils/cn'
import { buildBalanceSeries, type BalanceSeries, type BalanceSeriesPoint } from './balanceSeries'
import { chartSeriesColor, MAX_CHART_SERIES, SERIES_PALETTE_ROOT } from './chartColors'

export interface DailyBalancesChartProps {
  balances: components['schemas']['DailyBalanceOutput'][]
  isLoading?: boolean
}

/** Vertical padding inside the plot, in viewBox-percent units (viewBox is 0 0 1000 100). */
const PAD_TOP = 8
const PAD_BOTTOM = 8

function formatDayLabel(dayStr: string): string {
  const parts = dayStr.split('-')
  if (parts.length === 3) return `${parts[2]}/${parts[1]}`
  return dayStr
}

function formatDelta(deltaPct: number): string {
  return `${deltaPct >= 0 ? '+' : ''}${deltaPct.toFixed(1)}%`
}

function seriesLabel(series: BalanceSeries, singleAccount: boolean): string {
  return singleAccount ? series.currency : `${series.accountId} · ${series.currency}`
}

/** Latest point that carries a value; drives the legend's concrete numbers. */
function latestPoint(series: BalanceSeries): BalanceSeriesPoint | null {
  for (let i = series.points.length - 1; i >= 0; i--) {
    if (series.points[i].changePct !== null) return series.points[i]
  }
  return null
}

export function DailyBalancesChart({ balances, isLoading }: DailyBalancesChartProps) {
  const [hoveredDay, setHoveredDay] = useState<string | null>(null)
  const [selectedDay, setSelectedDay] = useState<string | null>(null)
  const [hiddenKeys, setHiddenKeys] = useState<ReadonlySet<string>>(new Set())

  const model = useMemo(() => buildBalanceSeries(balances), [balances])
  const days = model.days
  const charted = model.series.slice(0, MAX_CHART_SERIES)
  const visible = charted.filter((series) => !hiddenKeys.has(series.key))
  const singleAccount = new Set(charted.map((series) => series.accountId)).size <= 1

  // Change-vs-base domain across visible series, anchored at the 0 baseline (the first recorded day).
  let lo = Infinity
  let hi = -Infinity
  for (const series of visible) {
    for (const point of series.points) {
      if (point.changePct === null) continue
      lo = Math.min(lo, point.changePct)
      hi = Math.max(hi, point.changePct)
    }
  }
  lo = Math.min(lo, 0)
  hi = Math.max(hi, 0)
  const span = hi - lo
  lo -= span * 0.08 || 2
  hi += span * 0.08 || 2

  const xFrac = (i: number) => (days.length > 1 ? (i / (days.length - 1)) * 100 : 50)
  const yFrac = (value: number) => PAD_TOP + (1 - (value - lo) / (hi - lo)) * (100 - PAD_TOP - PAD_BOTTOM)

  // Round 1/2/5×10^k ticks so scan lines land on readable values (0, +2, +4...).
  const rawStep = (hi - lo) / 6
  const stepMagnitude = 10 ** Math.floor(Math.log10(rawStep))
  const step = ([1, 2, 5, 10].find((candidate) => rawStep / stepMagnitude <= candidate) ?? 10) * stepMagnitude
  const ticks: { top: number; label: string }[] = []
  for (let value = Math.ceil(lo / step) * step; value <= hi; value += step) {
    const rounded = Math.round(value * 1e6) / 1e6
    ticks.push({ top: yFrac(rounded), label: `${rounded > 0 ? '+' : ''}${step >= 1 ? Math.round(rounded) : rounded.toFixed(1)}%` })
  }

  const activeDayKey = hoveredDay ?? selectedDay ?? days[days.length - 1] ?? null
  const activeIndex = activeDayKey !== null ? days.indexOf(activeDayKey) : -1

  const labelStep = Math.max(1, Math.ceil(days.length / 14))

  const seriesValueAt = (series: BalanceSeries, dayIndex: number) =>
    dayIndex >= 0 && dayIndex < series.points.length ? series.points[dayIndex] : null

  const ariaSummary = (day: string) =>
    visible
      .map((series) => {
        const point = seriesValueAt(series, days.indexOf(day))
        return `${seriesLabel(series, singleAccount)} ${point?.raw !== null && point?.raw !== undefined ? formatAssetAmount(point.raw, series.currency) : '—'} (${point?.changePct !== null && point?.changePct !== undefined ? formatDelta(point.changePct) : '—'} vs first recorded day)`
      })
      .join(', ')

  return (
    <Surface className={cn('p-5 md:p-6', SERIES_PALETTE_ROOT)}>
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-app-border/80 pb-3">
        <div className="flex items-center gap-2">
          <Wallet size={18} className="text-app-action" />
          <h3 className="font-heavy text-base font-bold text-app-text">
            Daily Account Balance Fluctuations (Daily Balances)
          </h3>
        </div>
        <span className="text-[11px] text-app-muted">% change from each asset&apos;s first recorded day (0) · Shared wallets not aggregated</span>
      </div>

      <div className="mt-5">
        {isLoading ? (
          <div className="p-8 text-center text-xs text-app-muted">Loading balance history...</div>
        ) : days.length === 0 || charted.length === 0 ? (
          <div className="p-8 text-center text-xs text-app-muted">
            No historical balance data for the selected date range.
          </div>
        ) : (
          <div>
            {/* Legend chips: click to toggle a series; numbers are the latest recorded values vs the range-start base. */}
            <div className="mb-4 flex flex-wrap items-center gap-2">
              {charted.map((series, slot) => {
                const color = chartSeriesColor(slot)
                const latest = latestPoint(series)
                const delta = latest?.changePct ?? null
                const isHidden = hiddenKeys.has(series.key)
                return (
                  <button
                    key={series.key}
                    type="button"
                    aria-pressed={!isHidden}
                    onClick={() =>
                      setHiddenKeys((prev) => {
                        const next = new Set(prev)
                        if (next.has(series.key)) next.delete(series.key)
                        else next.add(series.key)
                        return next
                      })
                    }
                    className={cn(
                      'flex items-center gap-1.5 whitespace-nowrap rounded-full border border-app-border bg-app-surface px-2.5 py-1 text-[11px] transition-opacity hover:border-app-action/60',
                      isHidden && 'opacity-45',
                    )}
                  >
                    <span className="h-2 w-2 shrink-0 rounded-full" style={{ backgroundColor: isHidden ? 'var(--app-border)' : color }} />
                    <span className="font-semibold text-app-text">{seriesLabel(series, singleAccount)}</span>
                    {latest && (
                      <span className="font-mono text-app-muted">{formatAssetAmount(latest.raw, series.currency)}</span>
                    )}
                    {delta !== null && (
                      <span className={cn('font-mono font-semibold', delta > 0 ? 'text-app-success' : delta < 0 ? 'text-app-danger' : 'text-app-muted')}>
                        {formatDelta(delta)}
                      </span>
                    )}
                  </button>
                )
              })}
            </div>

            {model.series.length > MAX_CHART_SERIES && (
              <p className="mb-2 text-[11px] text-app-muted">
                Showing the first {MAX_CHART_SERIES} of {model.series.length} asset series; the rest stay in the detail table below.
              </p>
            )}

            {/* Indexed multi-line plot: one line per account × currency asset. */}
            <div className="overflow-x-auto">
              <div className="min-w-full" style={{ minWidth: `${44 + Math.max(0, (days.length - 1) * 48)}px` }}>
                <div className="flex">
                  {/* Y tick gutter */}
                  <div className="relative h-64 w-11 shrink-0 sm:h-72" aria-hidden="true">
                    {ticks.map((tick) => (
                      <span
                        key={tick.top}
                        className="absolute right-1 -translate-y-1/2 font-mono text-[10px] text-app-muted"
                        style={{ top: `${tick.top}%` }}
                      >
                        {tick.label}
                      </span>
                    ))}
                  </div>

                  <div className="relative h-64 flex-1 sm:h-72">
                    {/* Gridlines */}
                    <div className="pointer-events-none absolute inset-0" aria-hidden="true">
                      {ticks.map((tick) => (
                        <div key={tick.top} className="absolute inset-x-0 border-t border-dashed border-app-border/50" style={{ top: `${tick.top}%` }} />
                      ))}
                      {/* Baseline: 0 = each asset's first recorded day */}
                      <div className="absolute inset-x-0 border-t-2 border-dashed border-app-text/25" style={{ top: `${yFrac(0)}%` }}>
                        <span className="absolute right-0 -top-2 bg-app-surface/80 px-1 font-mono text-[9px] text-app-muted">first day = 0</span>
                      </div>
                    </div>

                    {/* Lines (viewBox 1000x100 stretched to the box; strokes stay 2px via vector-effect) */}
                    <svg className="absolute inset-0 h-full w-full" viewBox="0 0 1000 100" preserveAspectRatio="none" aria-hidden="true">
                      {visible.map((series, slot) => {
                        const segments: string[] = []
                        let current: string[] = []
                        series.points.forEach((point, i) => {
                          if (point.changePct !== null) current.push(`${xFrac(i) * 10},${yFrac(point.changePct)}`)
                          else if (current.length > 0) {
                            segments.push(current.join(' '))
                            current = []
                          }
                        })
                        if (current.length > 0) segments.push(current.join(' '))
                        return segments.map((points, segmentIndex) => (
                          <polyline
                            key={`${series.key}-${segmentIndex}`}
                            points={points}
                            fill="none"
                            style={{ stroke: chartSeriesColor(slot) }}
                            strokeWidth={2}
                            strokeLinejoin="round"
                            strokeLinecap="round"
                            vectorEffect="non-scaling-stroke"
                          />
                        ))
                      })}
                    </svg>

                    {/* End dots + direct labels (identity is never color-alone) */}
                    {visible.map((series, slot) => {
                      const last = latestPoint(series)
                      if (!last) return null
                      const lastIndex = series.points.lastIndexOf(last)
                      const x = xFrac(lastIndex)
                      const y = yFrac(last.changePct ?? 0)
                      const nearRightEdge = x > 82
                      return (
                        <div key={series.key}>
                          <div
                            className="pointer-events-none absolute h-2.5 w-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full ring-2 ring-app-surface"
                            style={{ left: `${x}%`, top: `${y}%`, backgroundColor: chartSeriesColor(slot) }}
                          />
                          {visible.length <= 4 && (
                            <span
                              className={cn(
                                'pointer-events-none absolute -translate-y-1/2 rounded bg-app-surface/85 px-1 text-[10px] font-semibold text-app-text',
                                nearRightEdge && '-translate-x-full',
                              )}
                              style={{ left: `calc(${x}% ${nearRightEdge ? '-' : '+'} 6px)`, top: `${y}%` }}
                            >
                              {seriesLabel(series, singleAccount)}
                            </span>
                          )}
                        </div>
                      )
                    })}

                    {/* Active-day crosshair + value dots */}
                    {activeIndex >= 0 && (
                      <div className="pointer-events-none absolute inset-y-0 w-px bg-app-border" style={{ left: `${xFrac(activeIndex)}%` }} aria-hidden="true">
                        {visible.map((series, slot) => {
                          const point = seriesValueAt(series, activeIndex)
                          if (point === null || point.changePct === null) return null
                          return (
                            <div
                              key={series.key}
                              className="absolute h-2.5 w-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full ring-2 ring-app-surface"
                              style={{ left: 0, top: `calc(${yFrac(point.changePct)}% )`, transform: 'translate(-50%, -50%)', backgroundColor: chartSeriesColor(slot) }}
                            />
                          )
                        })}
                      </div>
                    )}

                    {/* Hover tooltip with the concrete per-asset numbers */}
                    {hoveredDay && activeIndex >= 0 && (
                      <div
                        role="tooltip"
                        className={cn(
                          'pointer-events-none absolute z-50 w-64 rounded-card border border-app-border/90 bg-app-surface/95 p-2.5 text-xs shadow-xl backdrop-blur-md',
                          activeIndex < days.length / 2 ? '' : 'right-0',
                        )}
                        style={activeIndex < days.length / 2 ? { left: `calc(${xFrac(activeIndex)}% + 14px)` } : undefined}
                      >
                        <div className="border-b border-app-border/60 pb-1.5 font-mono font-bold text-app-text">{hoveredDay}</div>
                        <div className="mt-1.5 space-y-1 font-mono text-[11px]">
                          {visible.map((series, slot) => {
                            const point = seriesValueAt(series, activeIndex)
                            const delta = point?.changePct ?? null
                            return (
                              <div key={series.key} className="flex items-center justify-between gap-2">
                                <span className="flex items-center gap-1.5 whitespace-nowrap text-app-muted">
                                  <span className="h-2 w-2 shrink-0 rounded-full" style={{ backgroundColor: chartSeriesColor(slot) }} />
                                  <span>{seriesLabel(series, singleAccount)}:</span>
                                </span>
                                <span className="flex shrink-0 items-center gap-1.5 whitespace-nowrap">
                                  <span className="font-semibold text-app-text">
                                    {point?.raw !== null && point?.raw !== undefined ? formatAssetAmount(point.raw, series.currency) : '—'}
                                  </span>
                                  {delta !== null && (
                                    <span className={cn('text-[10px] font-semibold', delta > 0 ? 'text-app-success' : delta < 0 ? 'text-app-danger' : 'text-app-muted')}>
                                      {formatDelta(delta)}
                                    </span>
                                  )}
                                </span>
                              </div>
                            )
                          })}
                        </div>
                      </div>
                    )}

                    {/* Per-day hit columns: hover to inspect, click/Enter to pin */}
                    {days.map((day, i) => {
                      const left = days.length > 1 ? (i === 0 ? 0 : ((i - 0.5) / (days.length - 1)) * 100) : 0
                      const right = days.length > 1 ? (i === days.length - 1 ? 100 : ((i + 0.5) / (days.length - 1)) * 100) : 100
                      return (
                        <div
                          key={day}
                          role="button"
                          tabIndex={0}
                          aria-label={`Balance detail for ${day}: ${ariaSummary(day)}`}
                          onMouseEnter={() => setHoveredDay(day)}
                          onMouseLeave={() => setHoveredDay(null)}
                          onFocus={() => setHoveredDay(day)}
                          onBlur={() => setHoveredDay(null)}
                          onClick={() => setSelectedDay((prev) => (prev === day ? null : day))}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter' || e.key === ' ') {
                              e.preventDefault()
                              setSelectedDay((prev) => (prev === day ? null : day))
                            }
                          }}
                          className="absolute inset-y-0 cursor-pointer"
                          style={{ left: `${left}%`, width: `${right - left}%` }}
                        />
                      )
                    })}

                    {visible.length === 0 && (
                      <div className="absolute inset-0 flex items-center justify-center text-xs text-app-muted">All asset series are hidden.</div>
                    )}
                  </div>
                </div>

                {/* X axis day labels */}
                <div className="flex">
                  <div className="w-11 shrink-0" aria-hidden="true" />
                  <div className="relative h-5 flex-1" aria-hidden="true">
                    {days.map((day, i) =>
                      i % labelStep === 0 ? (
                        <span
                          key={day}
                          className={cn(
                            'absolute font-mono text-[10px] whitespace-nowrap',
                            i === 0 ? '' : i === days.length - 1 ? '-translate-x-full' : '-translate-x-1/2',
                            day === activeDayKey ? 'font-bold text-app-action' : 'text-app-muted',
                          )}
                          style={{ left: `${xFrac(i)}%` }}
                        >
                          {formatDayLabel(day)}
                        </span>
                      ) : null,
                    )}
                  </div>
                </div>
              </div>
            </div>

            {/* Active day inspection banner: concrete absolute numbers per asset */}
            {activeDayKey && visible.length > 0 && (
              <div className="mt-4 rounded-card border border-app-border bg-app-surface-muted/30 p-3 text-xs">
                <div className="flex items-center justify-between gap-2 border-b border-app-border/50 pb-2">
                  <span className="font-mono font-bold text-app-text">{activeDayKey}</span>
                  <span className="text-[11px] text-app-muted">Equity · Δ% vs first recorded day (0)</span>
                </div>
                <div className="mt-2 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                  {visible.map((series, slot) => {
                    const point = seriesValueAt(series, days.indexOf(activeDayKey))
                    const delta = point?.changePct ?? null
                    return (
                      <div key={series.key} className="flex items-center justify-between gap-2 whitespace-nowrap rounded-card border border-app-border/60 px-2.5 py-1.5 font-mono">
                        <span className="flex items-center gap-1.5">
                          <span className="h-2 w-2 shrink-0 rounded-full" style={{ backgroundColor: chartSeriesColor(slot) }} />
                          <span className="font-semibold text-app-text">{seriesLabel(series, singleAccount)}</span>
                        </span>
                        <span className="flex shrink-0 items-center gap-2">
                          <span className="font-bold text-app-text">
                            {point?.raw !== null && point?.raw !== undefined ? formatAssetAmount(point.raw, series.currency) : '—'}
                          </span>
                          {delta !== null && (
                            <span className={cn('text-[11px] font-bold', delta > 0 ? 'text-app-success' : delta < 0 ? 'text-app-danger' : 'text-app-muted')}>
                              {formatDelta(delta)}
                            </span>
                          )}
                        </span>
                      </div>
                    )
                  })}
                </div>
              </div>
            )}

            {/* Expandable table for exact inspection & copying */}
            {balances.length > 0 && (
              <details className="group mt-4 border-t border-app-border/60 pt-3">
                <summary className="flex cursor-pointer select-none items-center justify-between text-xs font-semibold text-app-muted hover:text-app-text">
                  <span>Detailed balance table ({balances.length} rows)</span>
                  <ChevronDown size={14} className="text-app-muted transition-transform group-open:rotate-180" />
                </summary>
                <div className="mt-3 overflow-x-auto rounded-card border border-app-border">
                  <table className="w-full min-w-[600px] text-left text-xs">
                    <thead>
                      <tr className="border-b border-app-border bg-app-surface-muted/60 text-app-muted font-semibold">
                        <th className="p-2.5">Day</th>
                        <th className="p-2.5">Account</th>
                        <th className="p-2.5">Currency</th>
                        <th className="p-2.5">Cash</th>
                        <th className="p-2.5">Equity</th>
                        <th className="p-2.5">Available Equity</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-app-border/60">
                      {balances.map((b, idx) => (
                        <tr key={`${b.day}-${b.currency}-${idx}`} className="hover:bg-app-surface-muted/30">
                          <td className="p-2.5 font-mono text-app-muted">{b.day}</td>
                          <td className="p-2.5 font-mono text-app-text">{b.account_id}</td>
                          <td className="p-2.5 font-bold">{b.currency}</td>
                          <td className="p-2.5 font-mono">{formatAssetAmount(b.cash_balance, b.currency)}</td>
                          <td className="p-2.5 font-mono font-bold text-app-text">{formatAssetAmount(b.equity, b.currency)}</td>
                          <td className="p-2.5 font-mono text-app-muted">{formatAssetAmount(b.available_equity, b.currency)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </details>
            )}
          </div>
        )}
      </div>
    </Surface>
  )
}
