import type { components } from '../../core/api/schema'
import type { OverviewModel } from './model'
import { InfoStatusBadge } from './visuals/InfoStatusBadge'
import { Surface } from '../../shared/components/Surface'
import { cn } from '../../shared/utils/cn'
import { formatPrice, formatUsdt } from '../../shared/utils/formatters'
import { summarizePositions } from './position-summary'

export interface HeldPositionsCardProps {
  positions?: components['schemas']['PositionsResponseOutput'] | null
  available: boolean
  markets: OverviewModel['positionMarkets']
  isLoading?: boolean
  error?: Error | null
  className?: string
}

/**
 * Delta balance gauge (bi-directional Exposure & Skew Gauge).
 * Combines SHORT↔FLAT↔LONG and inventory skew into one; includes
 * margin health gauge (entry → liquidation distance; mark price not in
 * observation so actual distance to liquidation is not calculated).
 */
export function HeldPositionsCard({ positions, available, markets, isLoading = false, error, className }: HeldPositionsCardProps) {
  const summary = summarizePositions(markets)
  const allPositions = summary.positions
  const observed = summary.observed && available && !error && !isLoading
  const { sameInstrument, netExposure, netPnl, pair } = summary
  const comparable = observed && sameInstrument && summary.gross !== null && summary.gross > 0 && netExposure !== null
  const needlePercent = comparable ? 50 + (netExposure / summary.gross!) * 44 : 50
  const observationLabel = isLoading ? 'SYNCING' : error ? 'API ERROR' : !available ? 'UNAVAILABLE' : observed ? null : markets.length > 0 && markets.every((market) => market.coverage === 'not_applicable') ? 'NOT APPLICABLE' : 'STALE / NO DATA'

  return (
    <Surface className={cn('flex h-full flex-col p-4 sm:p-5', className)}>
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-baseline gap-2">
          <h3 className="font-heavy text-base font-bold text-app-text">Derivative Positions & Skew</h3>
          {pair && <span className="font-mono text-[11px] text-app-muted">{pair}</span>}
        </div>
        {observationLabel ? (
          <InfoStatusBadge label={observationLabel} tone={error ? 'danger' : 'neutral'} detail={error?.message ?? (isLoading ? 'Updating position observations' : !available ? 'Backend owner is unavailable' : observationLabel === 'NOT APPLICABLE' ? 'Positions are outside the observed market scope' : 'No fresh complete position observation; current exposure is unknown')} />
        ) : allPositions.length > 0 ? (
          <InfoStatusBadge
            label={observed ? `${allPositions.length} POSITIONS` : 'STALE'}
            tone={observed ? 'warning' : 'neutral'}
            detail={observed ? 'Verified on exchange in latest observation' : 'Latest recorded positions; current state unverified'}
          />
        ) : (
          <InfoStatusBadge
            label="FLAT"
            tone="neutral"
            detail="No derivative positions in observed scope"
          />
        )}
      </div>

      {/* Delta balance gauge: SHORT ← 0 (Flat) → LONG */}
      <div className="mt-4 rounded-card bg-app-surface-muted/30 p-3" aria-label="Volume correlation for same instrument; not a risk limit">
        <div className="flex items-baseline justify-between text-[11px]">
          <span className="font-semibold text-rose-500">◀ SHORT</span>
          <span className="font-mono text-sm font-bold text-app-text">
            {comparable
              ? netExposure === 0 ? 'FLAT 0' : `${netExposure > 0 ? 'LONG' : 'SHORT'} ${formatPrice(Math.abs(netExposure), 4)}`
              : '—'}
          </span>
          <span className="font-semibold text-emerald-500">LONG ▶</span>
        </div>
        <div className="relative mt-2 h-3.5 w-full rounded-pill bg-app-border/30">
          <div className="absolute left-0 top-0 h-full w-1/2 rounded-l-pill bg-gradient-to-r from-rose-500/25 to-transparent" />
          <div className="absolute right-0 top-0 h-full w-1/2 rounded-r-pill bg-gradient-to-l from-emerald-500/25 to-transparent" />
          {/* Net exposure bar */}
          {comparable && netExposure !== 0 && (
            <div
              className={cn('absolute top-0 h-full', netExposure > 0 ? 'bg-emerald-500/70' : 'bg-rose-500/70')}
              style={{
                left: netExposure > 0 ? '50%' : `${needlePercent}%`,
                width: `${Math.abs(needlePercent - 50)}%`,
              }}
            />
          )}
          <div className="absolute left-1/2 top-0 bottom-0 w-0.5 bg-app-text/70" title="Flat balance point" />
          {comparable && (
            <div
              className={cn(
                'absolute top-1/2 z-10 h-4 w-4 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 shadow-sm transition-all duration-300',
                netExposure === 0 ? 'border-app-border bg-app-surface' : netExposure > 0 ? 'border-emerald-300 bg-emerald-500' : 'border-rose-300 bg-rose-500',
              )}
              style={{ left: `${needlePercent}%` }}
              title={`Long/Short correlation for same instrument; does not infer maximum position limit (no max exposure limit in observation)`}
            />
          )}
        </div>
        <div className="mt-1.5 flex items-center justify-between text-[10px] text-app-muted">
          <span>{comparable ? 'Same-instrument volume correlation' : !sameInstrument && allPositions.length > 0 ? 'Different instruments — net exposure not calculated' : 'Insufficient data'}</span>
          {!comparable && <InfoStatusBadge label="UNSUPPORTED" detail={!observed ? 'Maximum exposure limit and current exposure are unavailable' : !sameInstrument ? 'Maximum exposure limit is not provided; different instruments cannot be netted' : 'Maximum exposure limit is not provided by backend'} />}
        </div>

        {/* Net PnL */}
        {allPositions.length > 0 && observed && netPnl !== null && (
          <div className="mt-2.5 flex items-center justify-between border-t border-app-border/40 pt-2 text-xs">
            <span className="text-app-muted">Unrealized PnL (Total):</span>
            <span className={cn('font-mono font-bold tabular-nums', netPnl >= 0 ? 'text-emerald-500' : 'text-app-danger')}>
              {formatUsdt(netPnl)}
            </span>
          </div>
        )}
      </div>

      {/* Margin health: entry → liquidation distance */}
      {allPositions.length > 0 && (
        <div className="mt-3 space-y-2.5">
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-semibold uppercase tracking-wider text-app-muted">Margin Health</span>
            <InfoStatusBadge
              label="MARK: NO DATA"
              detail="Observation lacks mark price; gauge is based on entry price → liquidation price, not actual distance to liquidation"
            />
          </div>
          {allPositions.map((pos, idx) => {
            const entry = pos.entry_price ? parseFloat(pos.entry_price) : null
            const liq = pos.liquidation_price ? parseFloat(pos.liquidation_price) : null
            const hasScale = entry !== null && liq !== null && liq > 0 && entry > 0 && liq !== entry
            // Place entry at 15%, liquidation at 85%
            return (
              <div key={`${pos.side}-${pos.pair}-${idx}`} className="rounded-card bg-app-surface-muted/30 px-3 py-2.5">
                <div className="flex items-center justify-between text-[11px]">
                  <span className={cn('font-bold uppercase', pos.side === 'LONG' ? 'text-emerald-500' : 'text-rose-500')}>
                    {pos.side} · {formatPrice(Math.abs(Number(pos.amount)), 4)} · {pos.leverage}x
                  </span>
                  {observed && pos.pair.endsWith('-USDT') && <span className={cn('font-mono font-bold tabular-nums', Number(pos.unrealized_pnl) >= 0 ? 'text-emerald-500' : 'text-app-danger')}>
                    {formatUsdt(pos.unrealized_pnl)}
                  </span>}
                </div>
                {hasScale ? (
                  <div className="relative mt-2 h-2 w-full rounded-pill bg-app-border/30" role="img" aria-label={`Entry ${formatPrice(entry, 2)} to liquidation price ${formatPrice(liq, 2)}`}>
                    <div
                      className="absolute top-0 h-full rounded-pill bg-gradient-to-r from-emerald-500/60 to-rose-500/70"
                      style={{ left: '15%', width: '70%' }}
                    />
                    <div className="absolute top-1/2 h-3 w-0.5 -translate-y-1/2 bg-app-text" style={{ left: '15%' }} title={`Entry: ${formatPrice(entry, 2)}`} />
                    <div className="absolute top-1/2 h-3 w-0.5 -translate-y-1/2 bg-app-danger" style={{ left: '85%' }} title={`Liquidation price: ${formatPrice(liq, 2)}`} />
                  </div>
                ) : (
                  <p className="mt-1.5 text-[10px] text-app-muted">Entry/liquidation price not present in observation.</p>
                )}
                <div className="mt-1.5 flex flex-wrap justify-between gap-x-3 text-[10px] text-app-muted">
                  <span>Entry <span className="font-mono text-app-text">{formatPrice(pos.entry_price, 2)}</span></span>
                  <span>Liquidation <span className="font-mono text-app-danger">{formatPrice(pos.liquidation_price, 2)}</span></span>
                  <span>Margin <span className="font-mono text-app-text">{formatUsdt(pos.margin)}</span></span>
                </div>
              </div>
            )
          })}
        </div>
      )}

      <div className="mt-auto flex items-center justify-between border-t border-app-border/40 pt-2 text-[10px] text-app-muted">
        <span>OKX Position Risk</span>
        <span className="font-mono">{positions?.server_time ? new Date(positions.server_time).toLocaleString() : '—'}</span>
      </div>
    </Surface>
  )
}
