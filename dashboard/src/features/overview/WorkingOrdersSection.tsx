import { Link } from '@tanstack/react-router'
import { ArrowUpRight, ChevronDown, Info } from 'lucide-react'
import type { OverviewModel } from './model'
import { classifySpreadTone } from './spread-thresholds'
import { InfoStatusBadge } from './visuals/InfoStatusBadge'
import { Badge } from '../../shared/components/Badge'
import { Surface } from '../../shared/components/Surface'
import { cn } from '../../shared/utils/cn'
import { formatPrice, formatSpread, formatUsdt, localizeSystemMessage } from '../../shared/utils/formatters'

export interface WorkingOrdersSectionProps {
  markets: OverviewModel['markets']
  isLoading?: boolean
  /** Global sync alert active → dim orders area, mark as paused */
  dimmed?: boolean
  className?: string
}

function sideState(market: OverviewModel['markets'][number], side: 'buy' | 'sell') {
  const model = market.sides.find((s) => s.side === side)
  const state = model?.activity?.state ?? null
  const label = side === 'buy' ? 'Buy (Bid)' : 'Sell (Ask)'
  const tone = state === 'working' ? 'success' as const : state === 'blocked' ? 'danger' as const : state === 'pending' ? 'warning' as const : 'neutral' as const
  const shortLabel = state === 'working' ? 'WORKING' : state === 'blocked' ? 'BLOCKED' : state === 'pending' ? 'PENDING' : state === 'idle' ? 'PAUSED' : 'NO DATA'
  return { model, state, label, tone, shortLabel }
}

function MarketLiquidityCard({
  market,
  marketLabel,
  isLoading,
  dimmed,
}: {
  market: OverviewModel['markets'][number]
  marketLabel: string
  isLoading?: boolean
  dimmed?: boolean
}) {
  const data = market.sample
  const observation = market.observation
  const current = observation === 'fresh'
  const isEligible = current && (data?.eligible ?? false)
  const isTwoSided = current && (data?.two_sided ?? false)

  const buy = sideState(market, 'buy')
  const sell = sideState(market, 'sell')

  const bestBid = current ? data?.best_bid : null
  const bestAsk = current ? data?.best_ask : null
  const bidNotional = current ? data?.bid_notional : null
  const askNotional = current ? data?.ask_notional : null
  const buyCount = buy.model?.orderCount
  const sellCount = sell.model?.orderCount
  const spreadBps = current ? data?.spread_bps : null

  const bidNotionalNum = bidNotional ? parseFloat(bidNotional) : 0
  const askNotionalNum = askNotional ? parseFloat(askNotional) : 0
  const paused = current && bidNotionalNum === 0 && askNotionalNum === 0
  const maxNotional = Math.max(bidNotionalNum, askNotionalNum, 1)
  const bidWidthPct = (bidNotionalNum / maxNotional) * 50 // max half left
  const askWidthPct = (askNotionalNum / maxNotional) * 50 // max half right
  const spreadBpsNum = spreadBps ? parseFloat(spreadBps) : null
  const spreadTone = classifySpreadTone(spreadBpsNum)
  const bestBidNum = bestBid ? parseFloat(bestBid) : 0
  const bestAskNum = bestAsk ? parseFloat(bestAsk) : 0
  const midPrice = bestBidNum > 0 && bestAskNum > 0 ? (bestBidNum + bestAskNum) / 2 : null

  return (
    <Surface className={cn('flex flex-col p-4 sm:p-5', dimmed && 'opacity-60 transition-opacity')}>
      {/* Header: market + status badge */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-baseline gap-2">
          <h3 className="font-heavy text-base font-bold text-app-text">{data?.pair ?? 'Unknown pair'}</h3>
          <span className="text-[10px] font-semibold uppercase tracking-wider text-app-muted">{marketLabel}</span>
        </div>
        <div className="flex items-center gap-1.5">
          {isLoading ? (
            <InfoStatusBadge label="SYNCING" detail="Updating order observations" />
          ) : dimmed ? (
            <InfoStatusBadge label="PAUSED PENDING SYNC" tone="warning" detail="Reconciling orders with exchange; quoting paused on both sides" />
          ) : observation !== 'fresh' ? (
            <InfoStatusBadge label="NO DATA" detail="Liquidity observation is not current; current state unverified" />
          ) : isTwoSided ? (
            <InfoStatusBadge label="TWO-SIDED" tone="success" detail="Maintaining two-sided orders" />
          ) : !isEligible ? (
            <InfoStatusBadge label="NOT ELIGIBLE" tone="warning" detail="Market does not meet quoting conditions under policy" />
          ) : (
            <InfoStatusBadge label="ONE-SIDED" tone="warning" detail="Eligible but orders absent on at least one side" />
          )}
        </div>
      </div>

      {/* Visual Price Ladder */}
      <div className="mt-4">
        <div className="flex items-baseline justify-between text-[11px]">
          <span className="font-mono font-bold text-emerald-500">{formatPrice(bestBid)}</span>
          <span className="text-[10px] uppercase tracking-wider text-app-muted">Own orders</span>
          <span className="font-mono font-bold text-rose-500">{formatPrice(bestAsk)}</span>
        </div>

        <div
          className={cn(
            'relative mt-1.5 flex h-9 w-full items-stretch rounded-card',
            paused || !current ? 'border-2 border-dashed border-app-border bg-app-surface-muted/20' : 'bg-app-surface-muted/30',
          )}
          role="img"
          aria-label={`Price ladder ${data?.pair ?? ''}: bid ${formatPrice(bestBid)}, ask ${formatPrice(bestAsk)}, spread ${formatSpread(spreadBps)}`}
        >
          {/* Left half: BID */}
          <div className="flex w-1/2 justify-end">
            <div
              className={cn('h-full rounded-l-card bg-gradient-to-l from-emerald-500 to-emerald-400/80 transition-all duration-300', paused && 'from-app-border to-app-border/60')}
              style={{ width: `${paused ? 0 : Math.min(100, bidWidthPct * 2)}%` }}
              title={`Bid: ${buyCount ?? '—'} orders · ${formatUsdt(bidNotional)}`}
            />
          </div>
          {/* Center: mid-price + spread */}
          <div className="absolute left-1/2 top-0 bottom-0 flex -translate-x-1/2 flex-col items-center justify-center">
            <div className={cn('h-full w-0.5', current ? 'bg-app-text/70' : 'bg-transparent')} />
            <span
              className={cn(
                'absolute -top-0.5 font-mono text-[10px] font-bold',
                spreadTone === 'success' ? 'text-emerald-500' : spreadTone === 'warning' ? 'text-amber-400' : spreadTone === 'danger' ? 'text-rose-500' : 'text-app-muted',
              )}
              style={{ transform: 'translateY(-100%)' }}
            >
              {formatSpread(spreadBps)}
            </span>
          </div>
          {/* Right half: ASK */}
          <div className="flex w-1/2 justify-start">
            <div
              className={cn('h-full rounded-r-card bg-gradient-to-r from-rose-500 to-rose-400/80 transition-all duration-300', paused && 'from-app-border to-app-border/60')}
              style={{ width: `${paused ? 0 : Math.min(100, askWidthPct * 2)}%` }}
              title={`Ask: ${sellCount ?? '—'} orders · ${formatUsdt(askNotional)}`}
            />
          </div>
          {paused && (
            <span className="absolute inset-0 flex items-center justify-center text-[10px] font-semibold uppercase tracking-wider text-app-muted">
              {current ? 'No acknowledged order notional on either side' : 'NO DATA'}
            </span>
          )}
        </div>

        <div className="mt-1.5 flex items-center justify-between text-[11px] text-app-muted">
          <span className="font-mono">{buyCount ?? '—'} orders · {formatUsdt(bidNotional)}</span>
          {midPrice !== null && (
            <span className="font-mono" title="Own mid: average of own best bid/ask orders">Own mid {formatPrice(midPrice)}</span>
          )}
          <span className="font-mono">{sellCount ?? '—'} orders · {formatUsdt(askNotional)}</span>
        </div>
      </div>

      {/* Side statuses */}
      <div className="mt-3 grid grid-cols-2 gap-2">
        {([buy, sell] as const).map((side) => (
          <div key={side.label} className="rounded-card bg-app-surface-muted/30 px-2.5 py-2">
            <div className="flex items-center justify-between gap-2">
              <span className={cn('text-[11px] font-bold uppercase', side.label.startsWith('Buy') ? 'text-emerald-500' : 'text-rose-500')}>
                {side.label}
              </span>
              <InfoStatusBadge
                label={side.shortLabel}
                tone={side.tone}
                detail={[
                  side.model?.activity?.detail ? localizeSystemMessage(side.model.activity.detail) : null,
                  side.model?.diagnosis?.next_action ? `Diagnosis: ${localizeSystemMessage(side.model.diagnosis.next_action)}` : null,
                  `Pending create: ${side.model?.pendingCreateCount ?? '—'} · Pending cancel: ${side.model?.pendingCancelCount ?? '—'}`,
                  side.model?.diagnosis ? `Observed condition for ${side.model.diagnosis.observed_seconds}s` : null,
                ].filter(Boolean).join('\n') || 'No details available'}
              />
            </div>
          </div>
        ))}
      </div>

      {/* Drawer: diagnosis & confirmed orders */}
      <details className="group mt-3 rounded-card bg-app-surface-muted/20 p-2.5 text-[11px]">
        <summary className="flex cursor-pointer select-none items-center justify-between text-app-muted hover:text-app-text">
          <span className="flex items-center gap-1.5">
            <Info size={12} className="shrink-0 text-app-action" />
            <span>Side diagnosis & confirmed orders {market.workingOrders ? `(${market.workingOrders.length})` : ''}</span>
          </span>
          <ChevronDown size={13} className="transition-transform group-open:rotate-180" />
        </summary>
        <div className="mt-2 space-y-2 border-t border-app-border/40 pt-2">
          {([buy, sell] as const).map((side) => (
            <div key={`${side.label}-detail`} className="space-y-1">
              <div className="flex items-center gap-1.5">
                <Badge tone={side.tone} className="text-[9px] uppercase">{side.label}</Badge>
                <span className="text-[10px] text-app-muted">{side.state ?? 'unknown'}</span>
              </div>
              {side.model?.activity?.detail && (
                <p className="text-app-text leading-relaxed">{localizeSystemMessage(side.model.activity.detail)}</p>
              )}
              {side.model?.reasonCodes && side.model.reasonCodes.length > 0 && (
                <div className="flex flex-wrap gap-1">
                  {side.model.reasonCodes.map((code) => (
                    <span key={code} className="rounded bg-app-surface px-1.5 py-0.5 font-mono text-[10px] text-app-muted">{code}</span>
                  ))}
                </div>
              )}
              {side.model?.diagnosis && (
                <p className="text-[10px] text-app-muted">
                  {localizeSystemMessage(side.model.diagnosis.next_action)} · stage {side.model.diagnosis.stage} · observed {side.model.diagnosis.observed_seconds}s
                  {side.model.diagnosis.error_type ? ` · error ${side.model.diagnosis.error_type}` : ''}
                </p>
              )}
              <p className="text-[10px] text-app-muted">
                Pending create: {side.model?.pendingCreateCount ?? '—'} · Pending cancel: {side.model?.pendingCancelCount ?? '—'}
              </p>
            </div>
          ))}

          {market.workingOrders === null ? (
            <p className="text-[10px] text-app-muted">No current order rows in DTO; remaining quantity not inferred from total notional.</p>
          ) : market.workingOrders.length === 0 ? (
            <p className="text-[10px] text-app-muted">Fresh observation confirms no limit orders confirmed by exchange.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[480px] text-left text-[10px]">
                <thead className="text-app-muted">
                  <tr><th className="py-1 pr-2">Side</th><th className="py-1 pr-2">Price</th><th className="py-1 pr-2">Remaining</th><th className="py-1 pr-2">Notional</th><th className="py-1 pr-2">Type</th><th className="py-1">State</th></tr>
                </thead>
                <tbody>
                  {market.workingOrders.map((order, index) => (
                    <tr key={`${order.side}-${order.price}-${index}`} className="border-t border-app-border/40 font-mono">
                      <td className="py-1 pr-2">{order.side}</td>
                      <td className="py-1 pr-2">{formatPrice(order.price)}</td>
                      <td className="py-1 pr-2">{order.remaining_quantity}</td>
                      <td className="py-1 pr-2">{order.remaining_notional}</td>
                      <td className="py-1 pr-2">{order.order_type}</td>
                      <td className="py-1">{order.state}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <p className="flex items-center gap-1 text-[10px] text-app-muted">
            <Info size={11} className="shrink-0" />
            Rows include only own limit orders (OPEN/PARTIALLY_FILLED) with exchange order ID; not the entire order book.
          </p>
        </div>
      </details>

      <div className="mt-3">
        <Link to="/orders" className="inline-flex items-center gap-1 text-xs font-semibold text-app-action hover:underline">
          <span>Order Book & Executions</span>
          <ArrowUpRight size={12} />
        </Link>
      </div>
    </Surface>
  )
}

export function WorkingOrdersSection({ markets, isLoading, dimmed, className }: WorkingOrdersSectionProps) {
  // With more than two markets, stacking tall cards wastes vertical space; pair them up on wide screens.
  const layout = markets.length > 2 ? 'grid grid-cols-1 gap-3 xl:grid-cols-2' : 'flex h-full flex-col gap-3'
  return (
    <div className={cn(layout, className)} aria-label="Active working orders">
      {markets.map((market) => (
        <MarketLiquidityCard
          key={market.market}
          market={market}
          marketLabel={market.market === 'spot' ? 'Spot' : market.market === 'perpetual' ? 'Perp' : market.market}
          isLoading={isLoading}
          dimmed={dimmed}
        />
      ))}
      {markets.length === 0 && (
        <Surface className="flex flex-1 items-center justify-center p-6 text-sm text-app-muted">
          {isLoading ? 'Loading order observations...' : 'No current order observations.'}
        </Surface>
      )}
    </div>
  )
}
