import { useState } from 'react'
import { ChevronDown } from 'lucide-react'
import { Badge } from '../../shared/components/Badge'
import { Surface } from '../../shared/components/Surface'
import { cn } from '../../shared/utils/cn'
import { formatDuration, formatPrice, formatUsdt } from '../../shared/utils/formatters'
import { BackendGapChip } from './BackendGapChip'
import { parseAmount } from './parseAmount'
import type { LiquidityMarketView } from './supplement'

export type DetailMode = 'foundation' | 'operator'

function isMet(market: LiquidityMarketView) {
  return (
    (market.spreadActualBps ?? Infinity) <= (market.spreadTargetBps ?? Infinity) &&
    market.coverageTargetFraction !== null && market.fullWindow === true && market.rollingCoverageAvailable &&
    (market.coverageFraction ?? 0) >= market.coverageTargetFraction &&
    market.twoSided
  )
}

function MarketVerdict({ market, mode }: { market: LiquidityMarketView; mode: DetailMode }) {
  const [whyOpen, setWhyOpen] = useState(false)
  const spreadMet = market.spreadTargetBps !== null && (market.spreadActualBps ?? Infinity) <= market.spreadTargetBps
  const overallMet = isMet(market)
  const evidenceKnown = market.spreadTargetBps !== null && market.spreadActualBps !== null && market.coverageTargetFraction !== null && market.coverageFraction !== null && market.fullWindow === true && market.rollingCoverageAvailable

  const bidNum = parseAmount(market.bidNotional) ?? 0
  const askNum = parseAmount(market.askNotional) ?? 0
  const maxNotional = Math.max(bidNum, askNum, 1)

  const w = market.why
  const elapsed = Math.max(w.elapsedSeconds, 1)
  const seg = (v: number) => `${Math.min(100, (v / elapsed) * 100)}%`
  const unobserved = Math.max(0, w.elapsedSeconds - w.eligibleSeconds - w.notEligibleSeconds - w.unknownSeconds)

  const depthLevels = market.depth.available ? market.depth.value.levels : null

  return (
    <Surface id={`market-card-${market.market}`} className="scroll-mt-24 p-4 sm:p-5">
      {/* Header: pair + verdict — MỘT chỗ duy nhất kết luận đạt/chưa đạt */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-baseline gap-2">
          <h3 className="text-base font-bold text-app-text">{market.pair ?? market.market}</h3>
          <span className="text-[10px] font-semibold uppercase tracking-wider text-app-muted">
            {market.market === 'spot' ? 'Spot' : 'Perp'}
          </span>
        </div>
        <div className="flex items-center gap-1.5">
          <Badge tone={!evidenceKnown ? 'neutral' : overallMet ? 'success' : 'warning'} className="text-[11px]">
            {!evidenceKnown ? 'INSUFFICIENT DATA' : overallMet ? 'MET' : 'NOT MET'}
          </Badge>
          {!market.twoSided && <Badge tone="warning" className="text-[10px]">ONE-SIDED</Badge>}
        </div>
      </div>

      {/* Ladder giá — giá to hai bên, spread vs target giữa */}
      <div className="mt-4">
        <div className="flex items-baseline justify-between gap-2 text-xs">
          <span className="font-mono text-base font-bold text-emerald-500">{market.bestBid ? formatPrice(market.bestBid) : '—'}</span>
          <span className="flex items-baseline gap-1.5 whitespace-nowrap">
            <span className="text-[10px] uppercase tracking-wider text-app-muted">Own quotes</span>
            <span className={cn('font-mono text-xs font-bold', spreadMet ? 'text-emerald-500' : 'text-rose-500')}>
              {market.spreadActualBps ?? '—'} bps
            </span>
            <span className="text-[10px] text-app-muted">/ {market.spreadTargetBps ?? '—'}</span>
          </span>
          <span className="font-mono text-base font-bold text-rose-500">{market.bestAsk ? formatPrice(market.bestAsk) : '—'}</span>
        </div>

        <div
          className="relative mt-1.5 flex h-9 w-full items-stretch rounded-card bg-app-surface-muted/30"
          role="img"
          aria-label={`Ladder ${market.pair ?? market.market}: bid ${market.bestBid ?? '—'}, ask ${market.bestAsk ?? '—'}, spread ${market.spreadActualBps ?? '—'} bps (target ${market.spreadTargetBps ?? '—'} bps)`}
        >
          <div className="flex w-1/2 justify-end">
            <div
              className="h-full rounded-l-card bg-gradient-to-l from-emerald-500 to-emerald-400/80"
              style={{ width: `${Math.min(100, (bidNum / maxNotional) * 100)}%` }}
              title={`Bid: ${market.buyOrderCount ?? '—'} orders · ${market.bidNotional ? formatUsdt(market.bidNotional) : '—'}`}
            />
          </div>
          <div className="absolute left-1/2 top-0 bottom-0 flex -translate-x-1/2 items-center justify-center">
            <div className="h-full w-0.5 bg-app-text/70" />
          </div>
          <div className="flex w-1/2 justify-start">
            <div
              className="h-full rounded-r-card bg-gradient-to-r from-rose-500 to-rose-400/80"
              style={{ width: `${Math.min(100, (askNum / maxNotional) * 100)}%` }}
              title={`Ask: ${market.sellOrderCount ?? '—'} orders · ${market.askNotional ? formatUsdt(market.askNotional) : '—'}`}
            />
          </div>
        </div>

        <div className="mt-1.5 flex flex-wrap items-center justify-between gap-1 text-[11px] text-app-muted">
          <span className="font-mono">{market.buyOrderCount ?? '—'} orders · {market.bidNotional ? formatUsdt(market.bidNotional) : '—'}</span>
          {/* Depth ladder từ supplement — có thì liệt kê bậc, không thì chip "chưa có" */}
          {depthLevels && depthLevels.length > 0 ? (
            <span className="font-mono" title={`Own-order depth by distance from mid (basis: ${market.depth.available ? market.depth.value.reference_mid : ''})`}>
              {depthLevels.map((level) => `±${level.offset_bps}bps ${formatUsdt(level.bid_usdt)}/${formatUsdt(level.ask_usdt)}`).join(' · ')}
            </span>
          ) : (
            <BackendGapChip detail={market.depth.available ? 'No depth levels inside the observation window.' : (market.depth.reason ?? 'Depth ladder data unavailable (OverviewSupplement.markets[].depth).')} />
          )}
          <span className="font-mono">{market.sellOrderCount ?? '—'} orders · {market.askNotional ? formatUsdt(market.askNotional) : '—'}</span>
        </div>
      </div>

      {/* Sub-panel "vì sao chưa đạt" — chỉ hiện khi chưa đạt */}
      {!overallMet && (
        <div className="mt-3 rounded-card bg-app-surface-muted/30 p-3">
          <button
            type="button"
            onClick={() => setWhyOpen((v) => !v)}
            aria-expanded={whyOpen}
            className="flex w-full items-center justify-between text-left text-xs font-semibold text-app-text"
          >
            <span>Why not met — {mode === 'operator' ? 'session time breakdown & diagnosis' : 'main cause'}</span>
            <ChevronDown size={13} className={cn('transition-transform', whyOpen && 'rotate-180')} />
          </button>

          {mode === 'operator' && (
            <>
              <div className="mt-2 flex h-2 w-full overflow-hidden rounded-pill bg-app-surface-muted" aria-hidden="true">
                <div className="h-full bg-app-action" style={{ width: seg(w.eligibleSeconds) }} title={`Eligible: ${formatDuration(w.eligibleSeconds)}`} />
                <div className="h-full bg-amber-400" style={{ width: seg(w.notEligibleSeconds) }} title={`Not eligible: ${formatDuration(w.notEligibleSeconds)}`} />
                <div className="h-full bg-app-border" style={{ width: seg(w.unknownSeconds) }} title={`Unverified: ${formatDuration(w.unknownSeconds)}`} />
                {unobserved > 0 && <div className="h-full border-l border-dashed border-app-border/60" style={{ width: seg(unobserved) }} title={`Unobserved: ${formatDuration(unobserved)}`} />}
              </div>
              <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-app-muted">
                <span>Eligible <span className="font-mono text-app-text">{formatDuration(w.eligibleSeconds)}</span></span>
                <span className="text-amber-400">Not eligible <span className="font-mono">{formatDuration(w.notEligibleSeconds)}</span></span>
                <span>Unverified <span className="font-mono text-app-text">{formatDuration(w.unknownSeconds)}</span></span>
                {unobserved > 0 && <span>Unobserved <span className="font-mono text-app-text">{formatDuration(unobserved)}</span></span>}
              </div>
            </>
          )}

          {whyOpen && (
            <div className="mt-2 space-y-1.5 border-t border-app-border/40 pt-2 text-[11px]">
              <p>
                Continuous quoting gap: <span className={cn('font-mono font-bold', (w.quotingAbsentSeconds ?? 0) > 60 ? 'text-app-danger' : (w.quotingAbsentSeconds ?? 0) > 20 ? 'text-amber-400' : 'text-app-text')}>
                  {w.quotingAbsentSeconds === null ? '—' : `${w.quotingAbsentSeconds}s`}
                </span>
                <span className="text-app-muted"> (thresholds 20s / 60s)</span>
              </p>
              {w.diagnosis && <p className="text-app-text">{w.diagnosis}</p>}
              {mode === 'operator' && !market.rollingCoverageAvailable && (
                <p className="text-[10px] text-app-muted">Coverage shown as session cumulative — rolling 30-minute data unavailable.</p>
              )}
            </div>
          )}
        </div>
      )}
    </Surface>
  )
}

/**
 * Q3+Q4 — "Bot có cung cấp đúng mức thanh khoản đã giao không? Duy trì đến mức nào, vì sao chưa đạt?"
 * Market CHƯA ĐẠT xếp trước; mode operator hiện thêm chi tiết kỹ thuật.
 */
export function LiquidityVerdictCard({ markets, mode }: { markets: LiquidityMarketView[]; mode: DetailMode }) {
  const sorted = [...markets].sort((a, b) => Number(isMet(a)) - Number(isMet(b)))
  return (
    <div className="grid grid-cols-1 gap-4 sm:gap-5 xl:grid-cols-2">
      {sorted.map((market) => (
        <MarketVerdict key={market.market} market={market} mode={mode} />
      ))}
    </div>
  )
}
