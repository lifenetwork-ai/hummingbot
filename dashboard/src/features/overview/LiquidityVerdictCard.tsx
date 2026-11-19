import { useState } from 'react'
import { ChevronDown, Info } from 'lucide-react'
import { Badge } from '../../shared/components/Badge'
import { Surface } from '../../shared/components/Surface'
import { cn } from '../../shared/utils/cn'
import { formatAmount, formatDuration, formatPrice, formatUsdt } from '../../shared/utils/formatters'
import { formatUsdtForDisplay } from './formatNumbers'
import { parseAmount } from './parseAmount'
import type { LiquidityMarketView } from './supplement'

export type DetailMode = 'foundation' | 'operator'

interface MinuteSlot {
  index: number
  status: 'eligible' | 'not_eligible' | 'unverified' | 'unobserved'
  tooltip: string
}

function buildMinuteSlots(w: LiquidityMarketView['why'], slotCount = 30): { slots: MinuteSlot[]; hasDiscreteHistory: boolean } {
  const elapsed = Math.max(w.elapsedSeconds, 1)

  if (w.perMinute && Array.isArray(w.perMinute) && w.perMinute.length > 0) {
    const list = w.perMinute.slice(-slotCount)
    const slots = list.map((m, i) => {
      const minAgo = list.length - i
      let status: MinuteSlot['status'] = 'unobserved'
      let desc = 'No observation'

      if (m.eligible_seconds >= 30) {
        status = 'eligible'
        desc = `Eligible (${m.eligible_seconds}s)`
      } else if (m.restricted_seconds > 0) {
        status = 'not_eligible'
        desc = `Not eligible (${m.restricted_seconds}s restricted)`
      } else if (m.eligibility_unknown_seconds > 0) {
        status = 'unverified'
        desc = `Unverified (${m.eligibility_unknown_seconds}s unknown)`
      } else if (m.observed_seconds > 0) {
        status = m.eligible_seconds > 0 ? 'eligible' : 'not_eligible'
        desc = `Observed (${m.observed_seconds}s)`
      }

      return {
        index: i,
        status,
        tooltip: `${minAgo}m ago: ${desc}`,
      }
    })
    return { slots, hasDiscreteHistory: true }
  }

  // Fallback: Proportional distribution across 30 minute slots when discrete minute array is absent
  const eligibleSlots = Math.round((w.eligibleSeconds / elapsed) * slotCount)
  const notEligibleSlots = Math.round((w.notEligibleSeconds / elapsed) * slotCount)
  const unknownSlots = Math.round((w.unknownSeconds / elapsed) * slotCount)
  const slots: MinuteSlot[] = []

  for (let i = 0; i < slotCount; i++) {
    if (i < eligibleSlots) {
      slots.push({ index: i, status: 'eligible', tooltip: 'Proportional estimate: Eligible (discrete per-minute data unavailable)' })
    } else if (i < eligibleSlots + notEligibleSlots) {
      slots.push({ index: i, status: 'not_eligible', tooltip: 'Proportional estimate: Not eligible (discrete per-minute data unavailable)' })
    } else if (i < eligibleSlots + notEligibleSlots + unknownSlots) {
      slots.push({ index: i, status: 'unverified', tooltip: 'Proportional estimate: Unverified (discrete per-minute data unavailable)' })
    } else {
      slots.push({ index: i, status: 'unobserved', tooltip: 'Proportional estimate: Unobserved' })
    }
  }

  return { slots, hasDiscreteHistory: false }
}

function isMet(market: LiquidityMarketView) {
  return (
    market.spreadActualBps !== null && market.spreadTargetBps !== null && market.spreadActualBps <= market.spreadTargetBps &&
    market.coverageTargetFraction !== null && market.fullWindow === true && market.rollingCoverageAvailable && market.measurementReady && market.eligibleSeconds > 0 &&
    (market.coverageFraction ?? 0) >= market.coverageTargetFraction &&
    market.twoSided
  )
}

function MarketVerdict({ market, mode }: { market: LiquidityMarketView; mode: DetailMode }) {
  const w = market.why
  const hasIssueDiagnosis = Boolean(w.diagnosis || (w.quotingAbsentSeconds ?? 0) > 20)
  const [whyOpen, setWhyOpen] = useState(hasIssueDiagnosis)
  const [showSlaHelp, setShowSlaHelp] = useState(false)
  const spreadMet = market.spreadTargetBps !== null && (market.spreadActualBps ?? Infinity) <= market.spreadTargetBps
  const ownSpread = market.spreadActualBps === null ? '—' : `${formatAmount(market.spreadActualBps, 2)} bps`
  const plannedSpread = market.spreadTargetBps === null ? '—' : `${formatAmount(market.spreadTargetBps, 2)} bps`
  const overallMet = isMet(market)
  const evidenceKnown = market.spreadTargetBps !== null && market.spreadActualBps !== null && market.coverageTargetFraction !== null && market.coverageFraction !== null && market.fullWindow === true && market.rollingCoverageAvailable && market.measurementReady && market.eligibleSeconds > 0

  const bidNum = parseAmount(market.bidNotional) ?? 0
  const askNum = parseAmount(market.askNotional) ?? 0
  const maxNotional = Math.max(bidNum, askNum, 1)

  const elapsed = Math.max(w.elapsedSeconds, 1)
  const unobserved = Math.max(0, w.elapsedSeconds - w.eligibleSeconds - w.notEligibleSeconds - w.unknownSeconds)
  const { slots: minuteSlots, hasDiscreteHistory } = buildMinuteSlots(w)

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
          <span
            className={cn('font-mono text-base font-bold cursor-help', market.bestBid ? 'text-app-success' : 'text-app-muted')}
            title="Bot's best active bid price on the exchange"
          >
            {market.bestBid ? formatPrice(market.bestBid) : '—'}
          </span>
          <span className="flex items-baseline gap-1.5 whitespace-nowrap">
            <span
              className="text-[10px] uppercase tracking-wider text-app-muted cursor-help border-b border-dotted border-app-border/80"
              title="Bot's own quotes spread on the exchange (not full market book)"
            >
              Own quotes
            </span>
            <span
              className={cn('font-mono text-xs font-bold cursor-help', market.spreadActualBps === null ? 'text-app-muted' : spreadMet ? 'text-app-success' : 'text-app-danger')}
              title={`Current spread: ${ownSpread} (1 bps = 0.01%)`}
            >
              {ownSpread}
            </span>
            <span
              className="text-[10px] text-app-muted cursor-help"
              title="Session policy spread target (1 bps = 0.01%)"
            >
              / {plannedSpread}
            </span>
          </span>
          <span
            className={cn('font-mono text-base font-bold cursor-help', market.bestAsk ? 'text-app-danger' : 'text-app-muted')}
            title="Bot's best active ask price on the exchange"
          >
            {market.bestAsk ? formatPrice(market.bestAsk) : '—'}
          </span>
        </div>

        <div
          className="relative mt-1.5 flex h-9 w-full items-stretch rounded-card bg-app-surface-muted/30"
          role="img"
          aria-label={`Ladder ${market.pair ?? market.market}: bid ${market.bestBid ?? '—'}, ask ${market.bestAsk ?? '—'}, spread ${ownSpread} (target ${plannedSpread})`}
        >
          <div className="flex w-1/2 justify-end">
            <div
              className="h-full rounded-l-card bg-gradient-to-l from-app-success to-app-success/80"
              style={{ width: `${Math.min(100, (bidNum / maxNotional) * 100)}%` }}
              title={`Bid: ${market.buyOrderCount ?? '—'} orders · ${market.bidNotional ? formatUsdt(market.bidNotional) : '—'}`}
            />
          </div>
          <div className="absolute left-1/2 top-0 bottom-0 flex -translate-x-1/2 items-center justify-center">
            <div className="h-full w-0.5 bg-app-text/70" />
          </div>
          <div className="flex w-1/2 justify-start">
            <div
              className="h-full rounded-r-card bg-gradient-to-r from-app-danger to-app-danger/80"
              style={{ width: `${Math.min(100, (askNum / maxNotional) * 100)}%` }}
              title={`Ask: ${market.sellOrderCount ?? '—'} orders · ${market.askNotional ? formatUsdt(market.askNotional) : '—'}`}
            />
          </div>
        </div>

        <div className="mt-1.5 flex items-center justify-between gap-2 text-[11px] text-app-muted">
          <span className="font-mono">{market.buyOrderCount ?? '—'} orders · {market.bidNotional ? formatUsdt(market.bidNotional) : '—'}</span>
          <span className="font-mono">{market.sellOrderCount ?? '—'} orders · {market.askNotional ? formatUsdt(market.askNotional) : '—'}</span>
        </div>

        {/* Depth ladder từ supplement — hiển thị dạng gia số (Incremental / Delta) theo từng khoảng cự ly */}
        {depthLevels && depthLevels.length > 0 && (
          <div
            className="mt-2.5 rounded-card border border-app-border/40 bg-app-surface-muted/20 p-2"
            title={`Incremental own-order depth by band from mid (basis: ${market.depth.available ? market.depth.value.reference_mid : ''})`}
          >
            <div className="mb-1.5 flex items-center justify-between text-[10px] text-app-muted">
              <span
                className="font-semibold uppercase tracking-wider cursor-help border-b border-dotted border-app-border/80"
                title="Own order depth allocated across distance bands from mid price. Incremental = amount placed specifically within this band; Cumulative = total amount from mid up to this band."
              >
                Depth Ladder
              </span>
              <span
                className="font-mono text-[9px] text-app-muted/70 cursor-help"
                title="Incremental depth per band: Bid USDT / Ask USDT. Hover on any band to see cumulative totals."
              >
                USDT / Band (Bid / Ask)
              </span>
            </div>
            <div
              className="grid gap-1.5"
              style={{ gridTemplateColumns: `repeat(${depthLevels.length}, minmax(0, 1fr))` }}
            >
              {depthLevels.map((level, i) => {
                const prevLevel = i > 0 ? depthLevels[i - 1] : null
                const prevOffset = prevLevel ? prevLevel.offset_bps : 0
                const bandLabel = i === 0 ? `0–${level.offset_bps} bps` : `${prevOffset}–${level.offset_bps} bps`

                const cumBid = parseAmount(level.bid_usdt) ?? 0
                const cumAsk = parseAmount(level.ask_usdt) ?? 0
                const prevBid = prevLevel ? (parseAmount(prevLevel.bid_usdt) ?? 0) : 0
                const prevAsk = prevLevel ? (parseAmount(prevLevel.ask_usdt) ?? 0) : 0

                const rawDeltaBid = cumBid - prevBid
                const rawDeltaAsk = cumAsk - prevAsk
                const deltaBid = Math.abs(rawDeltaBid) < 1e-6 ? 0 : Math.max(0, rawDeltaBid)
                const deltaAsk = Math.abs(rawDeltaAsk) < 1e-6 ? 0 : Math.max(0, rawDeltaAsk)

                const deltaBidStr = formatUsdtForDisplay(deltaBid.toString()) ?? '0.00'
                const deltaAskStr = formatUsdtForDisplay(deltaAsk.toString()) ?? '0.00'
                const cumBidStr = formatUsdtForDisplay(level.bid_usdt) ?? '0.00'
                const cumAskStr = formatUsdtForDisplay(level.ask_usdt) ?? '0.00'

                return (
                  <div
                    key={level.offset_bps}
                    className="flex flex-col items-center justify-center rounded-md border border-app-border/30 bg-app-surface/60 px-1.5 py-1 text-center"
                    title={`Band: ${bandLabel}\nIncremental: ${deltaBidStr} Bid / ${deltaAskStr} Ask\nCumulative: ${cumBidStr} Bid / ${cumAskStr} Ask`}
                  >
                    <span className="text-[10px] font-bold text-app-muted">{bandLabel}</span>
                    <div className="mt-0.5 flex items-center gap-1 font-mono text-[11px]">
                      <span className={cn('font-semibold', deltaBid > 0 ? 'text-app-success' : 'text-app-muted/60')}>
                        {deltaBidStr}
                      </span>
                      <span className="text-[9px] text-app-muted/60">/</span>
                      <span className={cn('font-semibold', deltaAsk > 0 ? 'text-app-danger' : 'text-app-muted/60')}>
                        {deltaAskStr}
                      </span>
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        )}
      </div>

      {/* Sub-panel "vì sao chưa đạt" — chỉ hiện khi chưa đạt */}
      {!overallMet && (
        <div className="mt-3 rounded-card bg-app-surface-muted/30 p-3">
          <div className="flex w-full items-center justify-between text-xs font-semibold text-app-text">
            <div className="flex items-center gap-1.5">
              <button
                type="button"
                onClick={() => setWhyOpen((v) => !v)}
                className="hover:underline flex items-center gap-1.5 text-left"
              >
                <span>Why not met — {mode === 'operator' ? 'session time breakdown & diagnosis' : 'main cause'}</span>
              </button>
              <button
                type="button"
                onClick={() => setShowSlaHelp((v) => !v)}
                aria-label="Toggle SLA state definitions"
                className={cn(
                  'inline-flex items-center justify-center rounded p-0.5 transition-colors',
                  showSlaHelp ? 'bg-app-action/20 text-app-action' : 'text-app-muted hover:text-app-text hover:bg-app-surface-muted',
                )}
                title="Click to view SLA state definitions"
              >
                <Info size={13} />
              </button>
            </div>
            <button
              type="button"
              onClick={() => setWhyOpen((v) => !v)}
              aria-label="Toggle why panel"
              className="p-1 text-app-muted hover:text-app-text transition-colors"
            >
              <ChevronDown size={13} className={cn('transition-transform', whyOpen && 'rotate-180')} />
            </button>
          </div>

          {/* Inline SLA Explanation Guide (toggleable via Info button) */}
          {showSlaHelp && (
            <div className="mt-2.5 rounded-card border border-app-border/70 bg-app-surface p-3 text-[11px] leading-relaxed shadow-sm">
              <div className="flex items-center justify-between pb-1.5 border-b border-app-border/40">
                <span className="font-semibold text-app-text flex items-center gap-1.5 text-xs">
                  <Info size={13} className="text-app-action" />
                  30-Minute Rolling SLA States
                </span>
                <button
                  type="button"
                  onClick={() => setShowSlaHelp(false)}
                  className="text-[10px] text-app-muted hover:text-app-text font-medium"
                >
                  Close
                </button>
              </div>
              <div className="mt-2 space-y-1.5 text-[10px]">
                <div className="flex items-start gap-2">
                  <span className="mt-0.5 h-2 w-2 shrink-0 rounded-full bg-app-action" />
                  <div>
                    <span className="font-semibold text-app-text">Eligible:</span>{' '}
                    <span className="text-app-muted">Two-sided quoting active within spread and depth targets. Counts as compliant SLA.</span>
                  </div>
                </div>
                <div className="flex items-start gap-2">
                  <span className="mt-0.5 h-2 w-2 shrink-0 rounded-full bg-app-warning" />
                  <div>
                    <span className="font-semibold text-app-warning">Not eligible:</span>{' '}
                    <span className="text-app-muted">Bot is running but restricted by risk guards, inventory limits, insufficient balance, or wide spread.</span>
                  </div>
                </div>
                <div className="flex items-start gap-2">
                  <span className="mt-0.5 h-2 w-2 shrink-0 rounded-full bg-app-border" />
                  <div>
                    <span className="font-semibold text-app-text">Unverified:</span>{' '}
                    <span className="text-app-muted">Market data unconfirmed or connection dropped. Cannot verify quoting status, counted against SLA.</span>
                  </div>
                </div>
              </div>
            </div>
          )}

          <div className="mt-2.5">
            {/* SaaS Status Timeline: 30 minute bars */}
            <div
              className="flex h-5 w-full items-stretch gap-[2px] rounded-sm bg-app-surface-muted/20 p-1"
              role="img"
              aria-label="30-minute status timeline"
            >
              {minuteSlots.map((slot) => (
                <div
                  key={slot.index}
                  className={cn(
                    'flex-1 rounded-[1px] transition-all hover:scale-y-125 hover:brightness-110',
                    slot.status === 'eligible' && 'bg-app-action',
                    slot.status === 'not_eligible' && 'bg-app-warning',
                    slot.status === 'unverified' && 'bg-app-border',
                    slot.status === 'unobserved' && 'bg-app-surface-muted/40 border-t border-dashed border-app-border/40',
                  )}
                  title={slot.tooltip}
                />
              ))}
            </div>

            {/* Timeline Axis / Ruler */}
            <div className="mt-1 flex items-center justify-between font-mono text-[9px] text-app-muted">
              <span>30m ago</span>
              <span className="text-[8px] uppercase tracking-wider text-app-muted/60">15m ago</span>
              <span>Now</span>
            </div>
            {!hasDiscreteHistory && (
              <p className="mt-0.5 text-right font-sans text-[8px] italic text-app-muted/70">
                proportional estimate · per-minute history unavailable
              </p>
            )}

            {/* Detailed Breakdown Legend with % and mm:ss + Instant Floating Tooltips */}
            <div className="mt-2 flex flex-wrap items-center justify-between gap-x-3 gap-y-1.5 text-[10px]">
              {/* Eligible */}
              <div className="relative group/tip flex items-center gap-1.5 cursor-help py-0.5">
                <span className="h-2 w-2 rounded-full bg-app-action" />
                <span className="border-b border-dotted border-app-border/80 text-app-muted">Eligible:</span>
                <span className="font-mono font-semibold text-app-text">{formatDuration(w.eligibleSeconds)}</span>
                <span className="font-mono text-app-muted/70">({((w.eligibleSeconds / elapsed) * 100).toFixed(1)}%)</span>
                {/* Floating tooltip */}
                <div className="pointer-events-none absolute bottom-full left-0 mb-2 hidden group-hover/tip:flex flex-col z-50 w-56 rounded-md border border-app-border bg-app-surface p-2.5 shadow-xl text-[11px] leading-relaxed text-app-text">
                  <div className="font-bold text-app-action flex items-center gap-1.5 mb-1 text-[10px]">
                    <span className="h-2 w-2 rounded-full bg-app-action" />
                    Eligible (Compliant)
                  </div>
                  <p className="text-app-muted text-[10px]">
                    Active two-sided quoting within spread and depth targets. Fully qualifies for liquidity SLA.
                  </p>
                </div>
              </div>

              {/* Not eligible */}
              <div className="relative group/tip flex items-center gap-1.5 cursor-help py-0.5">
                <span className="h-2 w-2 rounded-full bg-app-warning" />
                <span className="border-b border-dotted border-app-warning/50 text-app-warning">Not eligible:</span>
                <span className="font-mono font-semibold text-app-warning">{formatDuration(w.notEligibleSeconds)}</span>
                <span className="font-mono text-app-muted/70">({((w.notEligibleSeconds / elapsed) * 100).toFixed(1)}%)</span>
                {/* Floating tooltip */}
                <div className="pointer-events-none absolute bottom-full left-1/2 -translate-x-1/2 mb-2 hidden group-hover/tip:flex flex-col z-50 w-56 rounded-md border border-app-border bg-app-surface p-2.5 shadow-xl text-[11px] leading-relaxed text-app-text">
                  <div className="font-bold text-app-warning flex items-center gap-1.5 mb-1 text-[10px]">
                    <span className="h-2 w-2 rounded-full bg-app-warning" />
                    Not Eligible (Restricted)
                  </div>
                  <p className="text-app-muted text-[10px]">
                    Bot is running but restricted by risk guards, inventory limits, insufficient balance, or wide spread.
                  </p>
                </div>
              </div>

              {/* Unverified */}
              <div className="relative group/tip flex items-center gap-1.5 cursor-help py-0.5">
                <span className="h-2 w-2 rounded-full bg-app-border" />
                <span className="border-b border-dotted border-app-border/80 text-app-muted">Unverified:</span>
                <span className="font-mono font-semibold text-app-text">{formatDuration(w.unknownSeconds)}</span>
                <span className="font-mono text-app-muted/70">({((w.unknownSeconds / elapsed) * 100).toFixed(1)}%)</span>
                {/* Floating tooltip */}
                <div className="pointer-events-none absolute bottom-full right-0 mb-2 hidden group-hover/tip:flex flex-col z-50 w-56 rounded-md border border-app-border bg-app-surface p-2.5 shadow-xl text-[11px] leading-relaxed text-app-text">
                  <div className="font-bold text-app-muted flex items-center gap-1.5 mb-1 text-[10px]">
                    <span className="h-2 w-2 rounded-full bg-app-border" />
                    Unverified (Data Unconfirmed)
                  </div>
                  <p className="text-app-muted text-[10px]">
                    Market data unconfirmed or connection dropped. Cannot verify quoting status, counted against SLA.
                  </p>
                </div>
              </div>

              {/* Unobserved */}
              {unobserved > 0 && (
                <div className="relative group/tip flex items-center gap-1.5 cursor-help py-0.5">
                  <span className="h-2 w-2 rounded-full border border-dashed border-app-border bg-app-surface-muted" />
                  <span className="border-b border-dotted border-app-border/80 text-app-muted">Unobserved:</span>
                  <span className="font-mono font-semibold text-app-text">{formatDuration(unobserved)}</span>
                  {/* Floating tooltip */}
                  <div className="pointer-events-none absolute bottom-full right-0 mb-2 hidden group-hover/tip:flex flex-col z-50 w-52 rounded-md border border-app-border bg-app-surface p-2.5 shadow-xl text-[11px] leading-relaxed text-app-text">
                    <div className="font-bold text-app-muted mb-1 text-[10px]">Unobserved</div>
                    <p className="text-app-muted text-[10px]">
                      Time interval outside the current session observation window.
                    </p>
                  </div>
                </div>
              )}
            </div>
          </div>

          {whyOpen && (
            <div className="mt-2 space-y-1.5 border-t border-app-border/40 pt-2 text-[11px]">
              <p>
                Continuous quoting gap:{' '}
                <span className={cn('font-mono font-bold', (w.quotingAbsentSeconds ?? 0) > 60 ? 'text-app-danger' : (w.quotingAbsentSeconds ?? 0) > 20 ? 'text-app-warning' : 'text-app-text')}>
                  {w.quotingAbsentSeconds === null ? '—' : `${w.quotingAbsentSeconds}s`}
                </span>
                <span
                  className="text-app-muted cursor-help"
                  title="Duration since bot last placed active quotes. Thresholds: >20s warning (replacement delay after fill), >60s danger (quoting disruption)."
                >
                  {' '}(thresholds 20s / 60s)
                </span>
              </p>
              {w.diagnosis && <p className="text-app-text">{w.diagnosis}</p>}
            </div>
          )}
        </div>
      )}
      {mode === 'operator' && market.rollingCoverageAvailable && (
        <div className="mt-3 text-[10px] text-app-muted">
          <p>Total-time coverage {market.totalTimeCoverageFraction !== null ? `${(market.totalTimeCoverageFraction * 100).toFixed(1)}%` : '—'} · eligible coverage {market.coverageFraction !== null ? `${(market.coverageFraction * 100).toFixed(1)}%` : '—'}</p>
          {overallMet && (
            <p>Eligible {formatDuration(market.eligibleSeconds)} · restricted {formatDuration(market.restrictedSeconds)} · eligibility unknown {formatDuration(market.eligibilityUnknownSeconds)}</p>
          )}
          {(!market.measurementReady || market.eligibleSeconds === 0 || market.fullWindow !== true) && (
            <p className="font-medium text-app-warning">Target unqualified{market.measurementReason ? `: ${market.measurementReason}` : ''}</p>
          )}
        </div>
      )}
      {mode === 'operator' && !market.rollingCoverageAvailable && <p className="mt-3 text-[10px] text-app-muted">Eligible rolling coverage data unavailable.</p>}
    </Surface>
  )
}

/**
 * Vùng 3 — Thanh khoản và mức đạt mục tiêu.
 * Đây là vị trí verdict chính (MET / NOT MET) của khối thanh khoản; KPI strip chỉ trình bày số.
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
