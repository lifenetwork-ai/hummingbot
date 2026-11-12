import { ChevronDown, Info } from 'lucide-react'
import type { OverviewModel } from './model'
import { InfoStatusBadge } from './visuals/InfoStatusBadge'
import { Surface } from '../../shared/components/Surface'
import { cn } from '../../shared/utils/cn'
import { formatDuration, formatPercentage } from '../../shared/utils/formatters'

export interface OperatingTargetsCardProps {
  markets: OverviewModel['markets']
  isLoading?: boolean
  className?: string
}

/** Backend reports cumulative sampled session counters, not a rolling SLA. */
export function OperatingTargetsCard({ markets, isLoading, className }: OperatingTargetsCardProps) {
  return (
    <Surface className={cn('flex h-full flex-col p-4 sm:p-5', className)}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="font-heavy text-base font-bold text-app-text">Two-Sided Coverage</h3>
          <p className="mt-0.5 text-[10px] text-app-muted">Cumulative session · sampled owner observations</p>
        </div>
        <InfoStatusBadge
          label={isLoading ? 'SYNCING' : 'SESSION TOTAL'}
          tone="neutral"
          detail="Cumulative session sampled rate. Rolling SLA is not provided by this API."
        />
      </div>

      {!isLoading && markets.length === 0 && (
        <p className="mt-3 text-xs text-app-muted">No coverage observations.</p>
      )}

      <div className="mt-4 space-y-4">
        {markets.map((market) => {
          const fractionPct = Math.min(100, Math.max(0, (market.observation === 'fresh' ? market.twoSidedElapsedFraction ?? 0 : 0) * 100))
          return (
            <div key={market.market} className="space-y-2">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <span className="text-[11px] font-semibold uppercase tracking-wider text-app-muted">
                  {market.market} <span className="font-mono normal-case">{market.sample.pair ?? '—'}</span>
                </span>
                <div className="flex items-center gap-2">
                  <span className="font-mono text-lg font-bold tabular-nums text-app-text">
                    {formatPercentage(market.observation === 'fresh' ? market.twoSidedElapsedFraction : null)}
                  </span>
                  <InfoStatusBadge
                    label={market.observation === 'session_mismatch' ? 'SESSION MISMATCH' : market.observation !== 'fresh' ? 'NO DATA' : 'SESSION TOTAL'}
                    tone="neutral"
                    detail={market.observation === 'fresh' ? 'Two-sided seconds divided by elapsed session seconds; eligible time is reported separately.' : 'No current liquidity observation for this session and generation.'}
                  />
                </div>
              </div>


              {/* Cumulative coverage bar */}
              <div className="h-1.5 w-full overflow-hidden rounded-pill bg-app-border/30" aria-hidden="true">
                <div
                  className="h-full rounded-pill bg-app-action transition-all duration-300"
                  style={{ width: `${fractionPct}%` }}
                />
              </div>

              <div className="flex flex-wrap justify-between gap-x-3 text-[10px] text-app-muted">
                <span>Elapsed <span className="font-mono text-app-text">{formatDuration(market.coverageWindow.elapsedSeconds)}</span></span>
                <span>Eligible <span className="font-mono text-app-text">{formatDuration(market.coverageWindow.eligibleSeconds)}</span></span>
                <span>Quoting absent <span className={cn('font-mono', (market.noQuoteSeconds ?? 0) > 60 ? 'text-rose-500 font-bold' : (market.noQuoteSeconds ?? 0) > 20 ? 'text-amber-400 font-bold' : 'text-app-text')}>
                  {market.noQuoteSeconds === null ? '—' : `${market.noQuoteSeconds}s`}
                </span></span>
              </div>
            </div>
          )
        })}
      </div>

      <p className="mt-3 text-xs text-app-muted">Rolling 30-minute SLA and per-minute history are not provided by this API.</p>

      {/* Technical parameters & thresholds accordion */}
      {markets.length > 0 && (
        <details className="group mt-4 rounded-card bg-app-surface-muted/20 p-2.5 text-xs">
          <summary className="flex cursor-pointer select-none items-center justify-between text-app-muted hover:text-app-text">
            <span className="flex items-center gap-1.5">
              <Info size={12} className="shrink-0 text-app-action" />
              <span>Technical parameters & SLA warning thresholds</span>
            </span>
            <ChevronDown size={13} className="transition-transform group-open:rotate-180" />
          </summary>
          <div className="mt-2 space-y-2 border-t border-app-border/40 pt-2 text-[11px] text-app-muted">
            {markets.map((market) => (
              <div key={`tech-${market.market}`} className="space-y-1">
                <strong className="text-app-text uppercase text-[10px]">{market.market} ({market.sample.pair})</strong>
                <p>
                  Unverified: {formatDuration(market.coverageWindow.unknownSeconds)} · Observed but not eligible: {formatDuration(market.coverageWindow.readinessIneligibleObservedSeconds)}
                </p>
                <p>
                  Deployment warning threshold: {market.warningAfterSeconds === null ? 'unverified' : market.warningAfterSeconds === 0 ? 'disabled' : `${market.warningAfterSeconds}s`}
                  {market.warningThresholdStatus === 'aligned' ? ' (matches 20s target)' : market.warningThresholdStatus === 'deployment_threshold_differs' ? ' (differs from 20s target)' : ''}.
                  {' '}Backend warning is currently {market.activity === null ? 'unknown' : market.activity.warning_active ? 'enabled' : 'disabled'}.
                </p>
                {market.sides.filter((side) => side.orderCount === 0 && side.diagnosis).map((side) => (
                  <p key={`${market.market}-${side.side}`}>
                    Side {side.side.toUpperCase()} quoting absent for {formatDuration(side.diagnosis!.observed_seconds)}:
                    {' '}{side.diagnosis!.observed_seconds > market.escalationAfterSeconds ? 'requires investigation (> 60s)' : side.diagnosis!.observed_seconds > 20 ? 'exceeds target threshold (> 20s)' : 'within target threshold'}.
                    {' '}{side.diagnosis!.next_action}
                  </p>
                ))}
              </div>
            ))}
          </div>
        </details>
      )}
    </Surface>
  )
}
