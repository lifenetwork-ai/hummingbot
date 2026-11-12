import { useState } from 'react'
import { BarChart3, ChevronDown } from 'lucide-react'
import type { ReportingSeries } from '../reporting/series'
import { Surface } from '../../shared/components/Surface'
import { formatUsdt } from '../../shared/utils/formatters'
import { cn } from '../../shared/utils/cn'

export interface DailyVolumeChartProps {
  series: ReportingSeries
  isLoading?: boolean
}

function formatCompactNumber(val: number): string {
  if (val >= 1_000_000) {
    const m = val / 1_000_000
    return `${m >= 10 ? Math.round(m) : m.toFixed(1).replace(/\.0$/, '')}M`
  }
  if (val >= 1_000) {
    const k = val / 1_000
    return `${k >= 10 ? Math.round(k) : k.toFixed(1).replace(/\.0$/, '')}K`
  }
  if (val > 0 && val < 10) {
    return val.toFixed(1)
  }
  return Math.round(val).toString()
}

function formatDayLabel(dayStr: string): string {
  const parts = dayStr.split('-')
  if (parts.length === 3) {
    return `${parts[2]}/${parts[1]}`
  }
  return dayStr
}

export function DailyVolumeChart({ series, isLoading }: DailyVolumeChartProps) {
  const [hoveredDay, setHoveredDay] = useState<string | null>(null)
  const [selectedDay, setSelectedDay] = useState<string | null>(null)

  // Numeric values are chart geometry only. Labels keep the authoritative decimals.
  const dayMap = new Map(series.map((row) => [row.day, {
    buy: Number(row.buy) || 0, sell: Number(row.sell) || 0, total: Number(row.total) || 0,
    rawBuy: row.buy, rawSell: row.sell, rawTotal: row.total, fills: row.fills,
    known: row.total !== null && Number.isFinite(Number(row.total)),
  }]))
  const sortedDays = Array.from(dayMap.entries()).sort(([a], [b]) => a.localeCompare(b))
  const maxTotal = Math.max(...sortedDays.map(([, v]) => v.total), 1)

  // Current active day to display in detail banner (hovered > clicked > latest day)
  const activeDayKey = hoveredDay ?? selectedDay ?? sortedDays[sortedDays.length - 1]?.[0] ?? null
  const activeData = activeDayKey ? dayMap.get(activeDayKey) : null

  return (
    <Surface className="p-5 md:p-6">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-app-border/80 pb-3">
        <div className="flex items-center gap-2">
          <BarChart3 size={18} className="text-app-action" />
          <h3 className="font-heavy text-base font-bold text-app-text">
            Daily Trading Volume Chart
          </h3>
        </div>
        <div className="flex items-center gap-3 text-[11px] text-app-muted">
          <span className="flex items-center gap-1">
            <span className="h-2.5 w-2.5 rounded-sm bg-emerald-500" /> Buy Volume
          </span>
          <span className="flex items-center gap-1">
            <span className="h-2.5 w-2.5 rounded-sm bg-rose-500" /> Sell Volume
          </span>
          <span>· Unit: USDT</span>
        </div>
      </div>

      <div className="mt-5">
        {isLoading ? (
          <div className="p-8 text-center text-xs text-app-muted">Loading volume chart...</div>
        ) : sortedDays.length === 0 ? (
          <div className="p-8 text-center text-xs text-app-muted">
            No volume data for the selected date range.
          </div>
        ) : (
          <div>
            {/* Vertical Stacked Bar Chart Area */}
            <div className="relative h-64 sm:h-72 w-full pt-6 pb-2">
              {/* Background Reference Grid Lines */}
              <div className="absolute inset-x-0 bottom-6 top-8 flex flex-col justify-between pointer-events-none" aria-hidden="true">
                <div className="border-b border-dashed border-app-border/50 w-full" />
                <div className="border-b border-dashed border-app-border/50 w-full" />
                <div className="border-b border-dashed border-app-border/50 w-full" />
                <div className="border-b border-app-border w-full" />
              </div>

              {/* Columns Container */}
              <div className="relative h-full flex items-end justify-between sm:justify-around gap-2 sm:gap-4 overflow-x-auto min-w-full px-2 pb-6">
                {sortedDays.map(([day, val], index) => {
                  const isHovered = hoveredDay === day
                  const isSelected = selectedDay === day
                  const isActive = isHovered || isSelected || (selectedDay === null && activeDayKey === day)
                  const isFirst = index === 0
                  const isLast = index === sortedDays.length - 1

                  const totalHeightPct = maxTotal > 0 ? (val.total / maxTotal) * 100 : 0
                  const sellRatio = val.total > 0 ? val.sell / val.total : 0
                  const buyRatio = val.total > 0 ? val.buy / val.total : 0

                  const sellHeightPct = sellRatio * 100
                  const buyHeightPct = buyRatio * 100

                  return (
                    <div
                      key={day}
                      onMouseEnter={() => setHoveredDay(day)}
                      onMouseLeave={() => setHoveredDay(null)}
                      onClick={() => setSelectedDay((prev) => (prev === day ? null : day))}
                      className="relative flex flex-col items-center flex-1 max-w-[84px] min-w-[52px] h-full justify-end cursor-pointer group select-none"
                      role="button"
                      tabIndex={0}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                          setSelectedDay((prev) => (prev === day ? null : day))
                        }
                      }}
                      aria-label={`Volume for ${day}: Total ${formatUsdt(val.rawTotal)}, Buy ${formatUsdt(val.rawBuy)}, Sell ${formatUsdt(val.rawSell)}`}
                    >
                      {/* Floating tooltip beside the hovered bar: shows values instantly without looking down at the bottom banner */}
                      {isHovered && (
                        <div
                          role="tooltip"
                          className={cn(
                            'pointer-events-none absolute z-50 w-52 rounded-card border border-app-border/90 bg-app-surface/95 p-2.5 text-xs shadow-xl backdrop-blur-md transition-all duration-150',
                            isFirst ? 'left-0' : isLast ? 'right-0' : 'left-1/2 -translate-x-1/2',
                            totalHeightPct >= 55 ? 'bottom-12 mb-2' : 'top-2 mt-1',
                          )}
                        >
                          <div className="flex items-center justify-between border-b border-app-border/60 pb-1.5 font-bold">
                            <span className="font-mono text-app-text">{day}</span>
                            <span className="text-[10px] text-app-muted font-normal">
                              {val.fills !== null && val.fills !== undefined ? `(${val.fills} fills)` : ''}
                            </span>
                          </div>
                          <div className="mt-1.5 space-y-1 font-mono text-[11px]">
                            <div className="flex justify-between items-center text-app-muted">
                              <span className="flex items-center gap-1.5">
                                <span className="h-2 w-2 rounded-full bg-emerald-500" />
                                Buy:
                              </span>
                              <span className="font-semibold text-emerald-500">
                                {formatUsdt(val.rawBuy)}
                                {val.total > 0 && (
                                  <span className="text-[10px] text-app-muted font-normal ml-1">
                                    ({((val.buy / val.total) * 100).toFixed(1)}%)
                                  </span>
                                )}
                              </span>
                            </div>
                            <div className="flex justify-between items-center text-app-muted">
                              <span className="flex items-center gap-1.5">
                                <span className="h-2 w-2 rounded-full bg-rose-500" />
                                Sell:
                              </span>
                              <span className="font-semibold text-rose-500">
                                {formatUsdt(val.rawSell)}
                                {val.total > 0 && (
                                  <span className="text-[10px] text-app-muted font-normal ml-1">
                                    ({((val.sell / val.total) * 100).toFixed(1)}%)
                                  </span>
                                )}
                              </span>
                            </div>
                            <div className="flex justify-between items-center border-t border-app-border/40 pt-1 text-app-text font-bold">
                              <span className="font-medium text-app-muted">Total:</span>
                              <span>{formatUsdt(val.rawTotal)}</span>
                            </div>
                          </div>
                        </div>
                      )}

                      {/* Top Total Label */}
                      <div className={cn(
                        'mb-1.5 font-mono text-[11px] font-bold text-center transition-colors truncate px-0.5',
                        isActive ? 'text-app-action' : 'text-app-text',
                      )}>
                        {val.total > 0 ? formatCompactNumber(val.total) : '0'}
                      </div>

                      {/* Stacked Vertical Column Bar */}
                      <div
                        className={cn(
                          'relative w-full rounded-t-md overflow-hidden flex flex-col justify-end transition-all duration-200 border border-app-border/40 shadow-xs',
                          isActive ? 'ring-2 ring-app-action ring-offset-2 ring-offset-app-surface scale-[1.03]' : 'hover:border-app-action/60',
                        )}
                        style={{
                          height: `${Math.max(val.total > 0 ? 14 : 4, totalHeightPct)}%`,
                        }}
                      >
                        {/* Sell Segment (Top, Rose) */}
                        {val.sell > 0 && (
                          <div
                            className="w-full bg-rose-500 hover:bg-rose-600 transition-colors flex items-center justify-center overflow-hidden"
                            style={{ height: `${sellHeightPct}%` }}
                            title={`Sell: ${formatUsdt(val.rawSell)}`}
                          >
                            {sellHeightPct >= 22 && val.total > 0 && (
                              <span className="font-mono text-[10px] font-bold text-white tracking-tight drop-shadow-xs px-0.5 select-none truncate">
                                {formatCompactNumber(val.sell)}
                              </span>
                            )}
                          </div>
                        )}

                        {/* Buy Segment (Bottom, Emerald) */}
                        {val.buy > 0 && (
                          <div
                            className="w-full bg-emerald-500 hover:bg-emerald-600 transition-colors flex items-center justify-center overflow-hidden"
                            style={{ height: `${buyHeightPct}%` }}
                            title={`Buy: ${formatUsdt(val.rawBuy)}`}
                          >
                            {buyHeightPct >= 22 && val.total > 0 && (
                              <span className="font-mono text-[10px] font-bold text-white tracking-tight drop-shadow-xs px-0.5 select-none truncate">
                                {formatCompactNumber(val.buy)}
                              </span>
                            )}
                          </div>
                        )}
                      </div>

                      {/* Day Label (X-Axis) */}
                      <div className={cn(
                        'mt-2 font-mono text-[11px] text-center transition-colors select-none',
                        isActive ? 'text-app-action font-bold' : 'text-app-muted group-hover:text-app-text',
                      )}>
                        {formatDayLabel(day)}
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>

            {/* Active Day Inspection Banner (Preserves All Detailed Metrics) */}
            {activeData && activeDayKey && (
              <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-card border border-app-border bg-app-surface-muted/30 p-3 text-xs">
                <div className="flex items-center gap-2">
                  <span className="font-mono font-bold text-app-text">{activeDayKey}</span>
                  <span className="text-[11px] text-app-muted">({activeData.fills ?? '—'} total fills)</span>
                </div>
                <div className="flex flex-wrap items-center gap-3 sm:gap-6 font-mono text-xs">
                  <div className="flex items-center gap-1.5">
                    <span className="h-2 w-2 rounded-full bg-emerald-500" />
                    <span className="text-app-muted">Buy:</span>
                    <span className="font-bold text-emerald-500">{formatUsdt(activeData.rawBuy)}</span>
                    <span className="text-[10px] text-app-muted">
                      ({activeData.total > 0 ? ((activeData.buy / activeData.total) * 100).toFixed(1) : '—'}%)
                    </span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <span className="h-2 w-2 rounded-full bg-rose-500" />
                    <span className="text-app-muted">Sell:</span>
                    <span className="font-bold text-rose-500">{formatUsdt(activeData.rawSell)}</span>
                    <span className="text-[10px] text-app-muted">
                      ({activeData.total > 0 ? ((activeData.sell / activeData.total) * 100).toFixed(1) : '—'}%)
                    </span>
                  </div>
                  <div className="flex items-center gap-1.5 border-l border-app-border pl-3 sm:pl-6">
                    <span className="text-app-muted">Total:</span>
                    <span className="font-bold text-app-text">{formatUsdt(activeData.rawTotal)}</span>
                  </div>
                </div>
              </div>
            )}

            {/* Expandable Table for Exact Inspection & Copying */}
            {sortedDays.length > 0 && (
              <details className="group mt-4 border-t border-app-border/60 pt-3">
                <summary className="cursor-pointer text-xs font-semibold text-app-muted hover:text-app-text flex items-center justify-between select-none">
                  <span>Detailed metrics table ({sortedDays.length} days)</span>
                  <ChevronDown size={14} className="transition-transform group-open:rotate-180 text-app-muted" />
                </summary>
                <div className="mt-3 overflow-x-auto rounded-card border border-app-border">
                  <table className="w-full min-w-[560px] text-left text-xs">
                    <thead>
                      <tr className="border-b border-app-border bg-app-surface-muted/60 text-app-muted font-semibold">
                        <th className="p-2.5">Date</th>
                        <th className="p-2.5">Buy Volume</th>
                        <th className="p-2.5">Sell Volume</th>
                        <th className="p-2.5">Total Volume</th>
                        <th className="p-2.5">Buy / Sell Ratio</th>
                        <th className="p-2.5">Fills</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-app-border/60 font-mono">
                      {sortedDays.map(([day, val]) => (
                        <tr
                          key={day}
                          onClick={() => setSelectedDay(day)}
                          className={cn(
                            'cursor-pointer transition-colors',
                            activeDayKey === day ? 'bg-app-action/10' : 'hover:bg-app-surface-muted/30',
                          )}
                        >
                          <td className="p-2.5 font-bold text-app-text">{day}</td>
                          <td className="p-2.5 text-emerald-500 font-medium">{formatUsdt(val.rawBuy)}</td>
                          <td className="p-2.5 text-rose-500 font-medium">{formatUsdt(val.rawSell)}</td>
                          <td className="p-2.5 font-bold text-app-text">{formatUsdt(val.rawTotal)}</td>
                          <td className="p-2.5 text-app-muted">
                            {val.total > 0
                              ? `${((val.buy / val.total) * 100).toFixed(1)}% / ${((val.sell / val.total) * 100).toFixed(1)}%`
                              : '—'}
                          </td>
                          <td className="p-2.5 text-app-muted">{val.fills}</td>
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
