import type { ReactNode } from 'react'
import { AlertCircle, Clock, HelpCircle, RefreshCw, Cpu } from 'lucide-react'
import type { PerformancePanelModel } from '../reporting/performance'
import { Badge } from '../../shared/components/Badge'
import { Button } from '../../shared/components/Button'
import { CopyableId } from '../../shared/components/CopyableId'
import { Surface } from '../../shared/components/Surface'
import { formatPercentage, formatUsdt } from '../../shared/utils/formatters'
import { cn } from '../../shared/utils/cn'

export interface ControllerPerformanceViewProps {
  model: PerformancePanelModel
  onRefresh?: () => void
  className?: string
}

/** Close types in a fixed display order so segment colors never repaint between renders. */
const CLOSE_TYPE_ORDER = ['TAKE_PROFIT', 'TRAILING_STOP', 'TIME_LIMIT', 'EXPIRED', 'EARLY_STOP', 'COMPLETED', 'POSITION_HOLD', 'STOP_LOSS', 'INSUFFICIENT_BALANCE', 'FAILED']
const PROFIT_CLOSE_TYPES = new Set(['TAKE_PROFIT'])
const LOSS_CLOSE_TYPES = new Set(['STOP_LOSS', 'INSUFFICIENT_BALANCE', 'FAILED'])
/** Human-readable labels; the enum name stays available in the chip tooltip. */
const CLOSE_TYPE_LABELS: Record<string, string> = {
  TAKE_PROFIT: 'Take profit',
  STOP_LOSS: 'Stop loss',
  TIME_LIMIT: 'Time limit',
  TRAILING_STOP: 'Trailing stop',
  EXPIRED: 'Expired',
  EARLY_STOP: 'Early stop',
  INSUFFICIENT_BALANCE: 'Insufficient balance',
  FAILED: 'Failed',
  COMPLETED: 'Completed',
  POSITION_HOLD: 'Position hold',
}

function sortCloseTypes(counts: [string, number][]): [string, number][] {
  return [...counts].sort((a, b) => {
    const ia = CLOSE_TYPE_ORDER.indexOf(a[0])
    const ib = CLOSE_TYPE_ORDER.indexOf(b[0])
    return (ia === -1 ? CLOSE_TYPE_ORDER.length : ia) - (ib === -1 ? CLOSE_TYPE_ORDER.length : ib) || a[0].localeCompare(b[0])
  })
}

function KpiTile({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="rounded-card border border-app-border/70 bg-app-surface-muted/30 p-3.5">
      <span className="text-[11px] font-medium uppercase tracking-wider text-app-muted">{label}</span>
      <div className="mt-2">{children}</div>
    </div>
  )
}

function BigValue({ tone, children }: { tone?: 'default' | 'positive' | 'negative'; children: ReactNode }) {
  return (
    <span
      className={cn(
        'font-mono text-xl font-bold tabular-nums sm:text-2xl',
        tone === 'positive' ? 'text-emerald-500' : tone === 'negative' ? 'text-app-danger' : 'text-app-text',
      )}
    >
      {children}
    </span>
  )
}

/**
 * Diverging split bar for one controller's PnL: zero at center, realized solid,
 * unrealized at 60% opacity, green to the profit side and red to the loss side.
 * All scales share the panel-wide max so rows are comparable; aria-hidden because
 * the exact values are in the Realized / Unrealized columns.
 */
function PnlSplitBar({ realized, unrealized, scale }: { realized: number; unrealized: number; scale: number }) {
  if (scale <= 0) return null
  const width = (value: number) => `${Math.min(50, (Math.abs(value) / scale) * 50)}%`
  const sameSideAsRealized = (unrealized >= 0) === (realized >= 0)
  return (
    <div
      aria-hidden="true"
      title={`Realized ${formatUsdt(realized)} · Unrealized ${formatUsdt(unrealized)}`}
      className="relative ml-auto mt-1.5 h-1.5 w-full min-w-[72px] max-w-[120px] overflow-hidden rounded-pill bg-app-surface-muted"
    >
      <span className="absolute inset-y-0 left-1/2 w-px bg-app-border" />
      <span
        className={cn('absolute inset-y-0 rounded-pill', realized >= 0 ? 'left-1/2 bg-emerald-500' : 'right-1/2 bg-app-danger')}
        style={{ width: width(realized) }}
      />
      <span
        className={cn('absolute inset-y-0 rounded-pill opacity-60', unrealized >= 0 ? 'bg-emerald-500' : 'bg-app-danger')}
        style={{
          width: width(unrealized),
          ...(unrealized >= 0
            ? { left: `calc(50% + ${sameSideAsRealized ? width(realized) : '0%'} + 2px)` }
            : { right: `calc(50% + ${sameSideAsRealized ? width(realized) : '0%'} + 2px)` }),
        }}
      />
    </div>
  )
}

/**
 * Stacked share bar of executor close outcomes (share of total closes).
 * Identity is carried by the text chips below it, so it is aria-hidden;
 * green = profit closes, red = loss closes, gray = neutral closes.
 */
function CloseTypeBar({ counts }: { counts: [string, number][] }) {
  const total = counts.reduce((acc, [, count]) => acc + count, 0)
  if (total <= 0) return null
  return (
    <div
      aria-hidden="true"
      className="mb-1.5 flex h-1.5 w-full min-w-[80px] max-w-[140px] gap-0.5"
      title={counts.map(([type, count]) => `${type}: ${count}`).join(' · ')}
    >
      {counts.map(([type, count]) => (
        <span
          key={type}
          className={cn(
            'h-full rounded-pill',
            PROFIT_CLOSE_TYPES.has(type) ? 'bg-emerald-500' : LOSS_CLOSE_TYPES.has(type) ? 'bg-app-danger' : 'bg-app-muted/70',
          )}
          style={{ width: `${(count / total) * 100}%` }}
        />
      ))}
    </div>
  )
}

export function ControllerPerformanceView({ model, onRefresh, className }: ControllerPerformanceViewProps) {
  const { source, rows, scope, observedAt, ownerAvailable, explanation, percentageUnit, percentageDenominator } = model
  const isError = source.phase === 'error'
  const isLoading = source.phase === 'loading'
  const isRefreshing = !!source.refreshing

  const sumVolume = rows.reduce((acc, row) => acc + (Number(row.report.volume_traded) || 0), 0)
  const sumGlobalPnl = rows.reduce((acc, row) => acc + (Number(row.report.global_pnl_quote) || 0), 0)
  const freshCount = rows.filter((row) => row.observation === 'fresh').length
  const staleCount = rows.filter((row) => row.observation === 'stale').length
  const unknownCount = rows.length - freshCount - staleCount
  const pnlScale = rows.reduce(
    (acc, row) => Math.max(acc, Math.abs(Number(row.report.realized_pnl_quote) || 0), Math.abs(Number(row.report.unrealized_pnl_quote) || 0)),
    0,
  )

  return (
    <Surface className={cn('p-5 space-y-5', className)}>
      {/* Header */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between border-b border-app-border/70 pb-4">
        <div>
          <div className="flex items-center gap-2">
            <Cpu size={18} className="text-app-action" />
            <h3 className="font-semibold text-app-text text-base">
              Standalone Controller Performance
            </h3>
            <Badge tone="neutral" className="text-[10px] px-2 py-0.5">
              Secondary Scope
            </Badge>
          </div>
          <p className="mt-1 text-xs text-app-muted">
            Tracks lifetime and active exposure of v2 controllers from the server cache.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {scope && (
            <Badge tone="neutral" className="text-[11px] font-mono">
              Scope: {scope}
            </Badge>
          )}
          {ownerAvailable !== null && (
            <Badge tone={ownerAvailable ? 'success' : 'warning'} className="text-[11px]">
              Runtime Owner: {ownerAvailable ? 'Available' : 'Unavailable'}
            </Badge>
          )}
          {observedAt && (
            <span className="text-[11px] text-app-muted font-mono flex items-center gap-1">
              <Clock size={12} />
              {observedAt}
            </span>
          )}
          {onRefresh && (
            <Button
              variant="secondary"
              onClick={onRefresh}
              disabled={isRefreshing}
              className="h-7 px-2.5 text-xs gap-1"
            >
              <RefreshCw size={12} className={cn(isRefreshing && 'animate-spin')} />
              <span>{isRefreshing ? 'Loading...' : 'Refresh'}</span>
            </Button>
          )}
        </div>
      </div>

      {/* Explanatory banner */}
      <div className="rounded-card border border-app-border/70 bg-app-surface-muted/30 p-3 text-xs text-app-muted flex items-start gap-2.5">
        <HelpCircle size={15} className="mt-0.5 shrink-0 text-app-action" />
        <div className="space-y-1">
          <p className="font-medium text-app-text">{explanation}</p>
          <p className="text-[11px]">
            Percentage unit: <strong className="text-app-text">{percentageUnit}</strong> (percentage points) over the <strong className="text-app-text">{percentageDenominator}</strong> denominator (trading volume). This is not return on capital, and controllers are not aggregated into total account PnL.
          </p>
        </div>
      </div>

      {/* Error state if source.phase === 'error' */}
      {isError && (
        <div
          role="alert"
          className="rounded-card border border-app-danger/30 bg-app-danger/10 p-3.5 text-xs text-app-danger flex items-start gap-2.5"
        >
          <AlertCircle size={16} className="mt-0.5 shrink-0" />
          <div className="space-y-0.5">
            <strong>Failed to load Controller performance data:</strong>
            <p className="text-app-text text-[11px]">
              {source.error?.message || 'Unknown server error.'}
            </p>
            <p className="text-[10px] text-app-muted">
              Note: This report is a standalone secondary panel; errors here do not interrupt daily statistics or session operations.
            </p>
          </div>
        </div>
      )}

      {/* Loading state */}
      {isLoading && rows.length === 0 && (
        <div className="py-8 text-center text-xs text-app-muted flex flex-col items-center justify-center gap-2">
          <RefreshCw size={18} className="animate-spin text-app-action" />
          <span>Loading controller performance...</span>
        </div>
      )}

      {/* Ready and empty state */}
      {!isLoading && !isError && rows.length === 0 && (
        <div className="py-8 text-center text-xs text-app-muted">
          No observations from the controller report cache yet.
        </div>
      )}

      {/* KPI strip — display-only sums across controller rows */}
      {rows.length > 0 && (
        <div className="grid gap-3 sm:grid-cols-3">
          <KpiTile label="Σ Volume Traded">
            <BigValue>{formatUsdt(sumVolume)}</BigValue>
            <p className="mt-1 text-[11px] text-app-muted">Across {rows.length} controller{rows.length === 1 ? '' : 's'} · lifetime</p>
          </KpiTile>
          <KpiTile label="Σ Global PnL">
            <BigValue tone={sumGlobalPnl > 0 ? 'positive' : sumGlobalPnl < 0 ? 'negative' : 'default'}>
              {sumGlobalPnl > 0 ? '+' : ''}{formatUsdt(sumGlobalPnl)}
            </BigValue>
            <p className="mt-1 text-[11px] text-app-muted">Sum of controllers — not account PnL</p>
          </KpiTile>
          <KpiTile label="Controller Observations">
            <BigValue tone={staleCount + unknownCount === 0 ? 'positive' : 'default'}>{freshCount} fresh</BigValue>
            <p className="mt-1 text-[11px] text-app-muted">
              {staleCount} stale · {unknownCount} unknown
            </p>
          </KpiTile>
        </div>
      )}

      {/* Rows Table */}
      {rows.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs border-collapse">
            <thead>
              <tr className="border-b border-app-border text-[11px] text-app-muted font-medium">
                <th className="py-2.5 px-3">Controller</th>
                <th className="py-2.5 px-3">Market / Pair</th>
                <th className="py-2.5 px-3">Observation Status</th>
                <th className="py-2.5 px-3 text-right">Volume Traded</th>
                <th className="py-2.5 px-3 text-right">
                  <div>Global PnL (Quote / Pct)</div>
                  <div className="mt-1 flex items-center justify-end gap-2 font-normal text-[10px]">
                    <span className="flex items-center gap-1">
                      <span className="inline-block h-1.5 w-3 rounded-pill bg-emerald-500" aria-hidden="true" />realized
                    </span>
                    <span className="flex items-center gap-1">
                      <span className="inline-block h-1.5 w-3 rounded-pill bg-emerald-500 opacity-60" aria-hidden="true" />unrealized
                    </span>
                    <span>(zero at center)</span>
                  </div>
                </th>
                <th className="py-2.5 px-3 text-right">Realized PnL</th>
                <th className="py-2.5 px-3 text-right">Unrealized PnL</th>
                <th className="py-2.5 px-3">
                  <div>Close Types</div>
                  <div className="mt-1 font-normal text-[10px]">executor closes by exit reason (count)</div>
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-app-border/40 font-mono">
              {rows.map((row) => {
                const obsTone =
                  row.observation === 'fresh'
                    ? 'success'
                    : row.observation === 'stale'
                    ? 'warning'
                    : 'neutral'
                const obsLabel =
                  row.observation === 'fresh'
                    ? 'Fresh'
                    : row.observation === 'stale'
                    ? 'Stale'
                    : 'Unknown'

                const closeTypes = sortCloseTypes(Object.entries(row.report.close_type_counts || {}))

                return (
                  <tr key={`${row.connector}-${row.controllerId}`} className="hover:bg-app-surface-muted/20 transition-colors">
                    {/* Controller */}
                    <td className="py-3 px-3">
                      <div className="font-semibold text-app-text">
                        <CopyableId id={row.controllerId} />
                      </div>
                      <div className="text-[10px] text-app-muted mt-0.5">
                        Connector: {row.connector}
                      </div>
                      <div className="text-[10px] text-app-muted mt-0.5 font-sans" title={row.valuationBasis}>
                        Basis: <span className="font-mono">{row.valuationBasis}</span>
                      </div>
                    </td>

                    {/* Market / Pair */}
                    <td className="py-3 px-3">
                      <div className="flex items-center gap-1.5">
                        <Badge tone={row.market === 'perpetual' ? 'brand' : 'neutral'} className="text-[10px] px-1.5 py-0">
                          {row.market}
                        </Badge>
                        <span className="font-bold text-app-text">{row.pair}</span>
                      </div>
                      <div className="text-[10px] text-app-muted mt-0.5">
                        Funding: {row.fundingStatus}
                      </div>
                    </td>

                    {/* Observation */}
                    <td className="py-3 px-3">
                      <Badge tone={obsTone} className="text-[10px] px-2 py-0.5">
                        {obsLabel}
                      </Badge>
                      <div className="text-[10px] text-app-muted mt-1 font-sans">
                        Observed: <span className="font-mono">{row.observedAt}</span>
                      </div>
                    </td>

                    {/* Volume Traded */}
                    <td className="py-3 px-3 text-right">
                      <span className="text-app-text font-semibold">
                        {formatUsdt(row.report.volume_traded)}
                      </span>
                    </td>

                    {/* Global PnL */}
                    <td className="py-3 px-3 text-right">
                      <div className="font-semibold text-app-text">
                        {formatUsdt(row.report.global_pnl_quote)}
                      </div>
                      <div className="text-[11px] text-app-muted">
                        {formatPercentage(row.report.global_pnl_pct, false)}
                      </div>
                      <PnlSplitBar
                        realized={Number(row.report.realized_pnl_quote) || 0}
                        unrealized={Number(row.report.unrealized_pnl_quote) || 0}
                        scale={pnlScale}
                      />
                    </td>

                    {/* Realized PnL */}
                    <td className="py-3 px-3 text-right">
                      <div className="text-app-text">
                        {formatUsdt(row.report.realized_pnl_quote)}
                      </div>
                      <div className="text-[11px] text-app-muted">
                        {formatPercentage(row.report.realized_pnl_pct, false)}
                      </div>
                    </td>

                    {/* Unrealized PnL */}
                    <td className="py-3 px-3 text-right">
                      <div className="text-app-text">
                        {formatUsdt(row.report.unrealized_pnl_quote)}
                      </div>
                      <div className="text-[11px] text-app-muted">
                        {formatPercentage(row.report.unrealized_pnl_pct, false)}
                      </div>
                    </td>

                    {/* Close Types */}
                    <td className="py-3 px-3 font-sans">
                      {closeTypes.length === 0 ? (
                        <span className="text-app-muted text-[11px]">—</span>
                      ) : (
                        <>
                          <CloseTypeBar counts={closeTypes} />
                          <div className="flex flex-wrap gap-1">
                          {closeTypes.map(([type, count]) => (
                            <span
                              key={type}
                              title={`Executor closes by ${CLOSE_TYPE_LABELS[type] ?? type} (${type})`}
                              className="inline-flex items-center rounded bg-app-surface-muted px-1.5 py-0.5 text-[10px] font-mono text-app-muted border border-app-border/50"
                            >
                              {CLOSE_TYPE_LABELS[type] ?? type}: {count}
                            </span>
                          ))}
                          </div>
                        </>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Footer warning */}
      <div className="border-t border-app-border/50 pt-3 text-[11px] text-app-muted flex items-center justify-between">
        <span>Σ tiles are for scale only; metrics are not aggregated into total account PnL.</span>
        <span>Source: v2_controller_report_cache</span>
      </div>
    </Surface>
  )
}
