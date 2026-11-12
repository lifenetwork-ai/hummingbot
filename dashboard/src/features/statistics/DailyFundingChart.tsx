import { useState } from 'react'
import { ChevronDown, Coins } from 'lucide-react'
import type { ReportingSeries } from '../reporting/series'
import { Surface } from '../../shared/components/Surface'
import { formatUsdt } from '../../shared/utils/formatters'
import { cn } from '../../shared/utils/cn'

export interface DailyFundingChartProps {
  series: ReportingSeries
  isLoading?: boolean
}

function formatCompactFunding(val: number): string {
  const abs = Math.abs(val)
  let str = ''
  if (abs >= 1_000_000) {
    const m = abs / 1_000_000
    str = `${m >= 10 ? Math.round(m) : m.toFixed(1).replace(/\.0$/, '')}M`
  } else if (abs >= 1_000) {
    const k = abs / 1_000
    str = `${k >= 10 ? Math.round(k) : k.toFixed(1).replace(/\.0$/, '')}K`
  } else if (abs > 0 && abs < 10) {
    str = abs.toFixed(2)
  } else {
    str = Math.round(abs).toString()
  }
  return val < 0 ? `-${str}` : (val > 0 ? `+${str}` : '0')
}

function formatDayLabel(dayStr: string): string {
  const parts = dayStr.split('-')
  if (parts.length === 3) {
    return `${parts[2]}/${parts[1]}`
  }
  return dayStr
}

export function DailyFundingChart({ series, isLoading }: DailyFundingChartProps) {
  const [hoveredDay, setHoveredDay] = useState<string | null>(null)
  const [selectedDay, setSelectedDay] = useState<string | null>(null)

  const sortedDays = series.map((row) => ({ day: row.day, funding_received: row.fundingReceived,
    funding_paid: row.fundingPaid, funding_net: row.fundingNet }))
  const maxNet = Math.max(...sortedDays.map((day) => Math.abs(Number(day.funding_net) || 0)), 1)
  const activeDayKey = hoveredDay ?? selectedDay ?? sortedDays[sortedDays.length - 1]?.day ?? null
  const activeDayData = activeDayKey ? sortedDays.find((day) => day.day === activeDayKey) : null
  const activeNet = Number(activeDayData?.funding_net) || 0

  return (
    <Surface className="p-5 md:p-6">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-app-border/80 pb-3">
        <div className="flex items-center gap-2">
          <Coins size={18} className="text-app-accent" />
          <h3 className="font-heavy text-base font-bold text-app-text">
            Daily Funding Payments
          </h3>
        </div>
        <div className="flex items-center gap-3 text-[11px] text-app-muted">
          <span className="flex items-center gap-1">
            <span className="h-2.5 w-2.5 rounded-sm bg-emerald-500" /> Net income (+)
          </span>
          <span className="flex items-center gap-1">
            <span className="h-2.5 w-2.5 rounded-sm bg-rose-500" /> Net paid (-)
          </span>
          <span>· Unit: USDT</span>
        </div>
      </div>

      <div className="mt-5">
        {isLoading ? (
          <div className="p-8 text-center text-xs text-app-muted">Loading funding data...</div>
        ) : sortedDays.length === 0 ? (
          <div className="p-8 text-center text-xs text-app-muted">
            No funding rate data within the observed range.
          </div>
        ) : (
          <div>
            {/* Vertical Bipolar (Diverging) Chart Area */}
            <div className="relative h-64 sm:h-72 w-full pt-6 pb-2">
              {/* Baseline at 50% and Grid Lines */}
              <div className="absolute inset-x-0 bottom-6 top-6 flex flex-col justify-between pointer-events-none" aria-hidden="true">
                <div className="border-b border-dashed border-app-border/40 w-full" />
                <div className="border-b-2 border-app-text/40 w-full relative">
                  <span className="absolute -left-1 -top-2.5 text-[9px] font-mono text-app-muted bg-app-surface px-1">0</span>
                </div>
                <div className="border-b border-dashed border-app-border/40 w-full" />
              </div>

              {/* Columns Container */}
              <div className="relative h-full flex items-center justify-between sm:justify-around gap-2 sm:gap-4 overflow-x-auto min-w-full px-2 pb-6">
                {sortedDays.map((d, index) => {
                  const net = Number(d.funding_net) || 0
                  const isHovered = hoveredDay === d.day
                  const isSelected = selectedDay === d.day
                  const isActive = isHovered || isSelected || (selectedDay === null && activeDayKey === d.day)
                  const isFirst = index === 0
                  const isLast = index === sortedDays.length - 1

                  // Magnitude as % of half-height (max 100%)
                  const magnitudePct = Math.min(100, (Math.abs(net) / maxNet) * 100)

                  return (
                    <div
                      key={d.day}
                      onMouseEnter={() => setHoveredDay(d.day)}
                      onMouseLeave={() => setHoveredDay(null)}
                      onClick={() => setSelectedDay((prev) => (prev === d.day ? null : d.day))}
                      className="relative flex flex-col items-center flex-1 max-w-[84px] min-w-[52px] h-full justify-between cursor-pointer group select-none"
                      role="button"
                      tabIndex={0}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                          setSelectedDay((prev) => (prev === d.day ? null : d.day))
                        }
                      }}
                      aria-label={`Funding for ${d.day}: Net ${d.funding_net !== null && net > 0 ? '+' : ''}${formatUsdt(d.funding_net)}`}
                    >
                      {/* Floating tooltip beside the hovered bar: no need to glance down at the fixed banner */}
                      {isHovered && (
                        <div
                          role="tooltip"
                          className={cn(
                            'pointer-events-none absolute z-50 w-48 rounded-card border border-app-border/90 bg-app-surface/95 p-2.5 text-xs shadow-xl backdrop-blur-md transition-all duration-150',
                            isFirst ? 'left-0' : isLast ? 'right-0' : 'left-1/2 -translate-x-1/2',
                            net >= 0 ? 'top-[52%] mt-2' : 'bottom-[52%] mb-2',
                          )}
                        >
                          <div className="flex items-center justify-between border-b border-app-border/60 pb-1.5 font-bold">
                            <span className="font-mono text-app-text">{d.day}</span>
                            <span className="text-[10px] text-app-muted font-normal">(Perp Contract)</span>
                          </div>
                          <div className="mt-1.5 space-y-1 font-mono text-[11px]">
                            <div className="flex justify-between items-center text-app-muted">
                              <span className="flex items-center gap-1.5">
                                <span className="h-2 w-2 rounded-full bg-emerald-500" />
                                Received:
                              </span>
                              <span className="font-semibold text-emerald-500">{formatUsdt(d.funding_received)}</span>
                            </div>
                            <div className="flex justify-between items-center text-app-muted">
                              <span className="flex items-center gap-1.5">
                                <span className="h-2 w-2 rounded-full bg-rose-500" />
                                Paid:
                              </span>
                              <span className="font-semibold text-rose-500">{formatUsdt(d.funding_paid)}</span>
                            </div>
                            <div className="flex justify-between items-center border-t border-app-border/40 pt-1 text-app-text font-bold">
                              <span className="font-medium text-app-muted">Net:</span>
                              <span className={cn(net >= 0 ? 'text-emerald-500' : 'text-app-danger')}>
                                {d.funding_net !== null && net > 0 ? '+' : ''}{formatUsdt(d.funding_net)}
                              </span>
                            </div>
                          </div>
                        </div>
                      )}
                      {/* Upper half: positive funding bar */}
                      <div className="w-full flex-1 flex flex-col justify-end items-center pb-0.5">
                        {net > 0 && (
                          <div
                            className={cn(
                              'w-full bg-emerald-500 hover:bg-emerald-600 rounded-t-md transition-all duration-200 flex items-center justify-center overflow-hidden border border-emerald-400/40 shadow-xs',
                              isActive ? 'ring-2 ring-emerald-500 ring-offset-2 ring-offset-app-surface scale-[1.03]' : '',
                            )}
                            style={{ height: `${Math.max(14, magnitudePct)}%` }}
                            title={`Net received: +${formatUsdt(d.funding_net)}`}
                          >
                            {magnitudePct >= 25 && (
                              <span className="font-mono text-[10px] font-bold text-white tracking-tight drop-shadow-xs px-0.5 truncate">
                                {formatCompactFunding(net)}
                              </span>
                            )}
                          </div>
                        )}
                      </div>

                      {/* Lower half: negative funding bar */}
                      <div className="w-full flex-1 flex flex-col justify-start items-center pt-0.5">
                        {net < 0 && (
                          <div
                            className={cn(
                              'w-full bg-rose-500 hover:bg-rose-600 rounded-b-md transition-all duration-200 flex items-center justify-center overflow-hidden border border-rose-400/40 shadow-xs',
                              isActive ? 'ring-2 ring-rose-500 ring-offset-2 ring-offset-app-surface scale-[1.03]' : '',
                            )}
                            style={{ height: `${Math.max(14, magnitudePct)}%` }}
                            title={`Net paid: ${formatUsdt(d.funding_net)}`}
                          >
                            {magnitudePct >= 25 && (
                              <span className="font-mono text-[10px] font-bold text-white tracking-tight drop-shadow-xs px-0.5 truncate">
                                {formatCompactFunding(net)}
                              </span>
                            )}
                          </div>
                        )}
                        {d.funding_net === null && <span className="text-xs text-app-muted">—</span>}
                        {d.funding_net !== null && net === 0 && (
                          <div className="w-full h-1 bg-app-border rounded-full" />
                        )}
                      </div>

                      {/* Day Label (X-Axis) */}
                      <div className={cn(
                        'mt-1 font-mono text-[11px] text-center transition-colors',
                        isActive ? 'text-app-action font-bold' : 'text-app-muted group-hover:text-app-text',
                      )}>
                        {formatDayLabel(d.day)}
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>

            {/* Active Day Inspection Banner */}
            {activeDayData && activeDayKey && (
              <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-card border border-app-border bg-app-surface-muted/30 p-3 text-xs">
                <div className="flex items-center gap-2">
                  <span className="font-mono font-bold text-app-text">{activeDayKey}</span>
                  <span className="text-[11px] text-app-muted">(Perpetual Contract)</span>
                </div>
                <div className="flex flex-wrap items-center gap-3 sm:gap-6 font-mono text-xs">
                  <div className="flex items-center gap-1.5">
                    <span className="h-2 w-2 rounded-full bg-emerald-500" />
                    <span className="text-app-muted">Received:</span>
                    <span className="font-bold text-emerald-500">{formatUsdt(activeDayData?.funding_received)}</span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <span className="h-2 w-2 rounded-full bg-rose-500" />
                    <span className="text-app-muted">Paid:</span>
                    <span className="font-bold text-rose-500">{formatUsdt(activeDayData?.funding_paid)}</span>
                  </div>
                  <div className="flex items-center gap-1.5 border-l border-app-border pl-3 sm:pl-6">
                    <span className="text-app-muted">Net:</span>
                    <span className={cn('font-bold', activeNet >= 0 ? 'text-emerald-500' : 'text-app-danger')}>
                      {activeDayData?.funding_net && activeNet > 0 ? '+' : ''}{formatUsdt(activeDayData?.funding_net)}
                    </span>
                  </div>
                </div>
              </div>
            )}

            {/* Expandable Table for Detailed Auditing */}
            {sortedDays.length > 0 && (
              <details className="group mt-4 border-t border-app-border/60 pt-3">
                <summary className="cursor-pointer text-xs font-semibold text-app-muted hover:text-app-text flex items-center justify-between select-none">
                  <span>Detailed funding table by day ({sortedDays.length} days)</span>
                  <ChevronDown size={14} className="transition-transform group-open:rotate-180 text-app-muted" />
                </summary>
                <div className="mt-3 overflow-x-auto rounded-card border border-app-border">
                  <table className="w-full min-w-[500px] text-left text-xs">
                    <thead>
                      <tr className="border-b border-app-border bg-app-surface-muted/60 text-app-muted font-semibold">
                        <th className="p-2.5">Date</th>
                        <th className="p-2.5">Funding Received</th>
                        <th className="p-2.5">Funding Paid</th>
                        <th className="p-2.5">Net Funding</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-app-border/60 font-mono">
                      {sortedDays.map((d) => {
                        const net = Number(d.funding_net) || 0
                        return (
                          <tr
                            key={d.day}
                            onClick={() => setSelectedDay(d.day)}
                            className={cn(
                              'cursor-pointer transition-colors',
                              activeDayKey === d.day ? 'bg-app-action/10' : 'hover:bg-app-surface-muted/30',
                            )}
                          >
                            <td className="p-2.5 font-bold text-app-text">{d.day}</td>
                            <td className="p-2.5 text-emerald-500 font-medium">{formatUsdt(d.funding_received)}</td>
                            <td className="p-2.5 text-rose-500 font-medium">{formatUsdt(d.funding_paid)}</td>
                            <td className={cn('p-2.5 font-bold', net >= 0 ? 'text-emerald-500' : 'text-app-danger')}>
                              {d.funding_net !== null && net > 0 ? '+' : ''}{formatUsdt(d.funding_net)}
                            </td>
                          </tr>
                        )
                      })}
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
