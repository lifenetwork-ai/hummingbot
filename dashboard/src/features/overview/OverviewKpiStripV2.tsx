import { Link } from '@tanstack/react-router'
import { Info } from 'lucide-react'
import { Surface } from '../../shared/components/Surface'
import { cn } from '../../shared/utils/cn'
import { formatAmount } from '../../shared/utils/formatters'

import { BackendGapChip } from './BackendGapChip'
import { formatQuantityForDisplay, formatUsdtForDisplay } from './formatNumbers'
import { parseAmount } from './parseAmount'
import type { OverviewModel } from './model'
import type { CapitalRiskView, LiquidityMarketView } from './supplement'

function KpiTile({ label, tooltip, className, children }: { label: string; tooltip?: string; className?: string; children: React.ReactNode }) {
  return (
    <Surface className={cn('p-3.5 sm:p-4', className)}>
      <div className="flex items-center gap-1.5">
        <span className="text-[11px] font-medium uppercase tracking-wider text-app-muted">{label}</span>
        {tooltip && (
          <span className="cursor-help text-app-muted/70 hover:text-app-text" title={tooltip}>
            <Info size={11} aria-hidden="true" />
          </span>
        )}
      </div>
      <div className="mt-2">{children}</div>
    </Surface>
  )
}

function BigValue({ children, tone }: { children: React.ReactNode; tone?: 'default' | 'positive' | 'negative' }) {
  return (
    <span
      className={cn(
        'font-mono text-xl font-bold tabular-nums sm:text-2xl',
        tone === 'positive' ? 'text-app-success' : tone === 'negative' ? 'text-app-danger' : 'text-app-text',
      )}
    >
      {children}
    </span>
  )
}

function TargetLine({ children, met }: { children: React.ReactNode; met?: boolean }) {
  return <p className={cn('text-xs', met === undefined ? 'text-app-muted' : met ? 'text-app-success' : 'text-app-danger')}>{children}</p>
}

function resolveVenueLabel(source?: string | null, connector?: string | null): string {
  if (source?.startsWith('okx')) return 'OKX'
  if (connector) return connector.toUpperCase()
  if (source) {
    const prefix = source.split('_')[0]
    if (prefix && prefix !== 'account' && prefix !== 'balance') {
      return prefix.toUpperCase()
    }
  }
  return 'Exchange'
}

function MetricRow({
  label,
  target,
  onClick,
  children,
  stacked = false,
}: {
  stacked?: boolean
  label: string
  target: string
  onClick?: () => void
  children: React.ReactNode
}) {
  const content = (
    <>
      <span className="min-w-0 shrink text-[11px] font-medium uppercase tracking-wider text-app-muted">{label}</span>
      <span className="flex min-w-0 flex-wrap items-baseline gap-1.5 font-mono">
        {children}
        <span className="text-[10px] font-normal text-app-muted">/ {target}</span>
      </span>
    </>
  )
  return onClick ? (
    <button
      type="button"
      onClick={onClick}
      className={cn('flex w-full min-w-0 flex-wrap items-baseline justify-between gap-2 rounded px-0.5 text-left hover:bg-app-surface-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-app-action', stacked && 'flex-col items-start')}
      title={`View ${label.toLowerCase()} details`}
    >
      {content}
    </button>
  ) : (
    <div className="flex w-full items-baseline justify-between gap-2">{content}</div>
  )
}

function scrollToAndFlash(id: string) {
  const el = document.getElementById(id)
  if (!el) return
  el.scrollIntoView({ behavior: 'smooth', block: 'center' })
  el.classList.add('ring-2', 'ring-app-action', 'rounded-card')
  window.setTimeout(() => el.classList.remove('ring-2', 'ring-app-action', 'rounded-card'), 1600)
}

/**
 * KPI strip — 4 tile số to, mỗi chỉ số có target cạnh actual.
 * Chỉ trình bày số liệu; verdict đạt/chưa đạt nằm duy nhất ở LiquidityVerdictCard
 * (UI-STRUCTURE-PLAN mục 3.2 quy tắc 2 — KPI không lặp headline verdict).
 */
export function OverviewKpiStripV2({
  liquidity,
  capital,
  accounts,
  onViewBalances,
}: {
  liquidity: LiquidityMarketView[]
  capital: CapitalRiskView
  accounts?: OverviewModel['accounts']
  onViewBalances?: () => void
}) {
  const spreadsKnown = liquidity.length > 0 && liquidity.every(m => m.spreadActualBps !== null && m.spreadTargetBps !== null)
  const anySpreadExceeded = liquidity.some(m => m.spreadActualBps !== null && m.spreadTargetBps !== null && m.spreadActualBps > m.spreadTargetBps)
  const allSpreadsMet = anySpreadExceeded ? false : spreadsKnown ? true : undefined
  const spreadUnknownDetail = liquidity.length === 0 || liquidity.some(m => m.spreadActualBps === null)
    ? 'Spread unavailable — two working sides required' : 'Spread target unavailable'

  const rollingAny = liquidity.some((m) => m.rollingCoverageAvailable)

  const net = capital.netPosition
  const portfolio = capital.portfolio.available ? capital.portfolio.value : null
  const accountEquity = capital.accountEquity?.available ? capital.accountEquity.value : null
  const delta = capital.sessionDelta.available ? capital.sessionDelta.value : null
  const pair = liquidity[0]?.pair ?? capital.netPosition.pair ?? 'LIFE-USDT'
  const [baseSymbol = 'LIFE', quoteSymbol = 'USDT'] = pair.split('-')

  const allAccountBalances = accounts?.[0]?.balances ?? accounts?.flatMap((a) => a.balances) ?? []
  const usdtBal = allAccountBalances.find((b) => b.currency.toUpperCase() === quoteSymbol.toUpperCase())
  const baseBal = allAccountBalances.find((b) => b.currency.toUpperCase() === baseSymbol.toUpperCase())

  const usdtFallback = capital.balances.find((b) => b.asset.toUpperCase() === quoteSymbol.toUpperCase())
  const baseFallback = capital.balances.find((b) => b.asset.toUpperCase() === baseSymbol.toUpperCase())

  const usdtAvail = usdtBal?.available_cash ?? usdtFallback?.free ?? portfolio?.usdt ?? null
  const usdtTotal = usdtBal?.cash_balance ?? (usdtFallback ? usdtFallback.free : null) ?? portfolio?.total_usdt ?? null

  // Token cơ sở (LIFE): chỉ lấy số lượng từ balances/account snapshot,
  // không fallback sang portfolio.life_usdt vì đó là giá trị định giá bằng USDT, không phải số lượng token.
  const baseAvail = baseBal?.available_cash ?? baseFallback?.free ?? null
  const baseTotal = baseBal?.cash_balance ?? (baseFallback ? baseFallback.free : null) ?? null

  const isStale = Boolean((usdtBal && (usdtBal.stale || usdtBal.observation === 'stale')) || (baseBal && (baseBal.stale || baseBal.observation === 'stale')))
  const venueLabel = resolveVenueLabel(accountEquity?.source, allAccountBalances[0]?.connector)

  return (
    <div className="grid grid-cols-1 gap-3 sm:gap-4 md:grid-cols-2 xl:grid-cols-4">
      <KpiTile
        label="Own spread vs planned width"
        tooltip="Difference between best bid and best ask compared to session target. 1 bps = 0.01% (0.0001)."
      >
        <div className="space-y-2.5">
          {liquidity.map((m) => {
            const met = m.spreadTargetBps !== null && m.spreadActualBps !== null ? m.spreadActualBps <= m.spreadTargetBps : undefined
            return (
              <MetricRow
                key={m.market}
                label={m.market === 'spot' ? 'Spot' : 'Perp'}
                target={m.spreadTargetBps !== null ? `${formatAmount(m.spreadTargetBps, 2)} bps` : '—'}
                onClick={() => scrollToAndFlash(`market-card-${m.market}`)}
              >
                <BigValue tone={met === undefined ? 'default' : met ? 'positive' : 'negative'}>
                  {formatAmount(m.spreadActualBps, 2)}
                </BigValue>
                {m.spreadActualBps !== null && (
                  <span className="text-[10px] font-normal text-app-muted cursor-help" title="Basis points (1 bps = 0.01% = 0.0001)">bps</span>
                )}
              </MetricRow>
            )
          })}
          <TargetLine met={allSpreadsMet}>
            {allSpreadsMet === undefined ? spreadUnknownDetail : allSpreadsMet ? 'Own spreads within planned width' : 'Own spreads wider than planned width'}
          </TargetLine>
        </div>
      </KpiTile>

      <KpiTile
        label="Eligible coverage (two-sided)"
        tooltip="Two-sided quote time during eligible periods in the last 30 minutes. Unverified time lowers coverage."
      >
        <div className="space-y-2.5">
          {liquidity.map((m) => {
            const missingReason = m.coverageFraction === null && m.measurementReason === 'no_eligible_time'
              ? m.market === 'perpetual' && capital.guardsSummary.perpetualBuyPaused
                ? 'BUY paused by long inventory limit'
                : 'No eligible time in this window'
              : null
            return (
              <div key={m.market}>
                <MetricRow
                  label={m.market === 'spot' ? 'Spot' : 'Perp'}
                  target={m.coverageTargetFraction === null ? '—' : `${Math.round(m.coverageTargetFraction * 100)}%`}
                  onClick={() => scrollToAndFlash(`market-card-${m.market}`)}
                >
                  <BigValue>
                    {m.coverageFraction !== null ? `${(m.coverageFraction * 100).toFixed(1)}%` : '—'}
                  </BigValue>
                </MetricRow>
                {missingReason && <TargetLine>{missingReason}</TargetLine>}
              </div>
            )
          })}
          {liquidity.filter(m => m.rollingCoverageAvailable && m.fullWindow === false).map(m => (
            <TargetLine key={m.market}>{m.market === 'spot' ? 'Spot' : 'Perp'}: collecting {Math.floor(m.why.elapsedSeconds / 60)} / 30 min</TargetLine>
          ))}
          <div className="flex items-center gap-1.5">
            <TargetLine>{rollingAny ? 'Eligible rolling 30m' : 'Eligible rolling coverage unavailable'}</TargetLine>
            {!rollingAny && <BackendGapChip detail="Eligible rolling coverage data unavailable (status.overview.markets[].coverage)." />}
          </div>
        </div>
      </KpiTile>

      <KpiTile
        label="Net position & PnL"
        tooltip="Directional inventory exposure across perpetual positions and unrealized profit/loss."
      >
        <div className="space-y-2.5">
          <MetricRow
            label={net.pair ?? 'Perp'}
            target={net.inventoryLimit === null ? '—' : `limit ${formatQuantityForDisplay(net.inventoryLimit) ?? net.inventoryLimit}`}
            onClick={() => scrollToAndFlash('capital-risk-card')}
          >
            <BigValue tone={net.side === 'LONG' ? 'positive' : net.side === 'SHORT' ? 'negative' : 'default'}>
              {!net.observed || net.amount === '—' ? '—' : net.side === 'FLAT' ? 'FLAT' : `${net.side} ${net.amount}`}
            </BigValue>
          </MetricRow>
          <div className="flex items-baseline justify-between gap-1.5 text-xs">
            <div className="flex items-baseline gap-1.5">
              <span className="text-app-muted">PnL:</span>
              <span className={cn('font-mono font-bold tabular-nums', (parseAmount(net.pnlUsdt) ?? 0) >= 0 ? 'text-app-success' : 'text-app-danger')}>
                {net.pnlUsdt !== null ? `${net.pnlUsdt} USDT` : '—'}
              </span>
            </div>
            <Link
              to="/statistics"
              className="text-[11px] font-medium text-app-action hover:underline focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-app-action rounded-xs"
              aria-label="View detailed statistics report"
            >
              View report →
            </Link>
          </div>
          {!net.observed && <TargetLine>Net position unavailable — check individual positions</TargetLine>}
        </div>
      </KpiTile>

      <KpiTile
        label="Trading balances"
        tooltip="Available vs total cash balances across shared spot and perpetual accounts."
      >
        <div className="space-y-2.5">
          {accountEquity ? (
            <>
              <div className="flex items-baseline justify-between gap-2">
                <BigValue>{`${formatAmount(accountEquity.total_usd, 2)} USD`}</BigValue>
                {delta?.pct != null && (
                  <span className={cn('font-mono text-xs font-bold', (parseAmount(delta.pct) ?? 0) >= 0 ? 'text-app-success' : 'text-app-danger')}>
                    {Number(delta.pct) >= 0 ? '+' : ''}{Number(delta.pct).toFixed(2)}%
                  </span>
                )}
              </div>
              <TargetLine>{venueLabel} account equity · includes position PnL</TargetLine>
              {onViewBalances && (
                <div className="flex justify-end pt-0.5">
                  <button
                    type="button"
                    onClick={onViewBalances}
                    className="text-[11px] font-medium text-app-action hover:underline focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-app-action rounded-xs"
                  >
                    View balances →
                  </button>
                </div>
              )}
            </>
          ) : (
            <>
              <MetricRow
                label={quoteSymbol}
                target={`${formatUsdtForDisplay(usdtTotal) ?? '—'} total`}
                onClick={onViewBalances}
              >
                <BigValue>{formatUsdtForDisplay(usdtAvail) ?? '—'}</BigValue>
              </MetricRow>
              <MetricRow
                label={baseSymbol}
                target={`${formatQuantityForDisplay(baseTotal) ?? '—'} total`}
                onClick={onViewBalances}
              >
                <BigValue>{formatQuantityForDisplay(baseAvail) ?? '—'}</BigValue>
              </MetricRow>
              <TargetLine>
                {!portfolio
                  ? 'Account valuation unavailable · check balances'
                  : isStale
                    ? 'Balances unverified / stale'
                    : 'Spot & Perp shared balance'}
              </TargetLine>
              {onViewBalances && (
                <div className="flex justify-end pt-0.5">
                  <button
                    type="button"
                    onClick={onViewBalances}
                    className="text-[11px] font-medium text-app-action hover:underline focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-app-action rounded-xs"
                  >
                    View balances →
                  </button>
                </div>
              )}
            </>
          )}
        </div>
      </KpiTile>
    </div>
  )
}
