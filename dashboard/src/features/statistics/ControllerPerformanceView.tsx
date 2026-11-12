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

export function ControllerPerformanceView({ model, onRefresh, className }: ControllerPerformanceViewProps) {
  const { source, rows, scope, observedAt, ownerAvailable, explanation, percentageUnit, percentageDenominator } = model
  const isError = source.phase === 'error'
  const isLoading = source.phase === 'loading'
  const isRefreshing = !!source.refreshing

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
                <th className="py-2.5 px-3 text-right">Global PnL (Quote / Pct)</th>
                <th className="py-2.5 px-3 text-right">Realized PnL</th>
                <th className="py-2.5 px-3 text-right">Unrealized PnL</th>
                <th className="py-2.5 px-3">Close Types</th>
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

                const closeTypes = Object.entries(row.report.close_type_counts || {})

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
                        <div className="flex flex-wrap gap-1">
                          {closeTypes.map(([type, count]) => (
                            <span
                              key={type}
                              className="inline-flex items-center rounded bg-app-surface-muted px-1.5 py-0.5 text-[10px] font-mono text-app-muted border border-app-border/50"
                            >
                              {type}: {count}
                            </span>
                          ))}
                        </div>
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
        <span>Performance metrics are not aggregated across controllers.</span>
        <span>Source: v2_controller_report_cache</span>
      </div>
    </Surface>
  )
}
