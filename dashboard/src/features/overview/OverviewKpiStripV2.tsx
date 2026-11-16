import { Surface } from '../../shared/components/Surface'
import { cn } from '../../shared/utils/cn'
import { formatUsdt } from '../../shared/utils/formatters'
import { BackendGapChip } from './BackendGapChip'
import { parseAmount } from './parseAmount'
import type { CapitalRiskView, LiquidityMarketView } from './supplement'

function KpiTile({ label, className, children }: { label: string; className?: string; children: React.ReactNode }) {
  return (
    <Surface className={cn('p-3.5 sm:p-4', className)}>
      <span className="text-[11px] font-medium uppercase tracking-wider text-app-muted">{label}</span>
      <div className="mt-2">{children}</div>
    </Surface>
  )
}

function BigValue({ children, tone }: { children: React.ReactNode; tone?: 'default' | 'positive' | 'negative' }) {
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

function TargetLine({ children, met }: { children: React.ReactNode; met?: boolean }) {
  return <p className={cn('text-xs', met === undefined ? 'text-app-muted' : met ? 'text-app-success' : 'text-app-danger')}>{children}</p>
}

function MetricRow({
  label,
  target,
  onClick,
  children,
}: {
  label: string
  target: string
  onClick?: () => void
  children: React.ReactNode
}) {
  const content = (
    <>
      <span className="min-w-0 shrink text-[11px] font-medium uppercase tracking-wider text-app-muted">{label}</span>
      <span className="flex shrink-0 items-baseline gap-1.5 font-mono">
        {children}
        <span className="text-[10px] font-normal text-app-muted">/ {target}</span>
      </span>
    </>
  )
  return onClick ? (
    <button
      type="button"
      onClick={onClick}
      className="flex w-full items-baseline justify-between gap-2 rounded px-0.5 text-left hover:bg-app-surface-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-app-action"
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
 * KPI strip — 4 tile số to, mỗi chỉ số có target cạnh actual (bố cục theo trục câu hỏi).
 */
export function OverviewKpiStripV2({ liquidity, capital }: { liquidity: LiquidityMarketView[]; capital: CapitalRiskView }) {
  const spreads = liquidity.filter((m) => m.spreadActualBps !== null)
  const allSpreadsMet = spreads.length > 0 && spreads.every((m) => (m.spreadActualBps ?? 0) <= (m.spreadTargetBps ?? Infinity))
  const coverageKnown = liquidity.length > 0 && liquidity.every((m) => m.coverageTargetFraction !== null && m.coverageFraction !== null && m.rollingCoverageAvailable && m.fullWindow === true)
  const allCoverageMet = coverageKnown ? liquidity.every((m) => m.coverageTargetFraction !== null && (m.coverageFraction ?? 0) >= m.coverageTargetFraction) : undefined
  const rollingAny = liquidity.some((m) => m.rollingCoverageAvailable)

  const net = capital.netPosition
  const portfolio = capital.portfolio.available ? capital.portfolio.value : null
  const delta = capital.sessionDelta.available ? capital.sessionDelta.value : null

  return (
    <div className="grid grid-cols-1 gap-3 sm:gap-4 md:grid-cols-2 xl:grid-cols-4">
      <KpiTile label="Spread vs target">
        <div className="space-y-2.5">
          {liquidity.map((m) => {
            const met = m.spreadTargetBps !== null && (m.spreadActualBps ?? Infinity) <= m.spreadTargetBps
            return (
              <MetricRow
                key={m.market}
                label={m.market === 'spot' ? 'Spot' : 'Perp'}
                target={`${m.spreadTargetBps ?? '—'} bps`}
                onClick={() => scrollToAndFlash(`market-card-${m.market}`)}
              >
                <BigValue tone={met ? 'positive' : 'negative'}>{m.spreadActualBps ?? '—'}</BigValue>
                <span className="text-[10px] font-normal text-app-muted">bps</span>
              </MetricRow>
            )
          })}
          <TargetLine met={allSpreadsMet}>{allSpreadsMet ? 'All markets within spread target' : 'Some markets exceed spread target'}</TargetLine>
        </div>
      </KpiTile>

      <KpiTile label="Session coverage (two-sided)">
        <div className="space-y-2.5">
          {liquidity.map((m) => {
            const known = m.coverageTargetFraction !== null && m.coverageFraction !== null && m.rollingCoverageAvailable && m.fullWindow === true
            const met = m.coverageTargetFraction !== null && (m.coverageFraction ?? 0) >= m.coverageTargetFraction
            return (
              <MetricRow
                key={m.market}
                label={m.market === 'spot' ? 'Spot' : 'Perp'}
                target={m.coverageTargetFraction === null ? '—' : `${Math.round(m.coverageTargetFraction * 100)}%`}
                onClick={() => scrollToAndFlash(`market-card-${m.market}`)}
              >
                <BigValue tone={!known ? 'default' : met ? 'positive' : 'negative'}>
                  {m.coverageFraction !== null ? `${(m.coverageFraction * 100).toFixed(1)}%` : '—'}
                </BigValue>
              </MetricRow>
            )
          })}
          <div className="flex items-center gap-1.5">
            <TargetLine met={allCoverageMet}>{rollingAny ? 'Rolling 30m' : 'Cumulative session'}</TargetLine>
            {!rollingAny && <BackendGapChip detail="Rolling 30-minute SLA data unavailable — showing cumulative session coverage (OverviewSupplement.markets[].coverage)." />}
          </div>
        </div>
      </KpiTile>

      <KpiTile label="Net position & PnL">
        <div className="space-y-2.5">
          <MetricRow label={net.pair ?? 'Perp'} target="perpetual" onClick={() => scrollToAndFlash('capital-risk-card')}>
            <BigValue tone={net.side === 'LONG' ? 'positive' : net.side === 'SHORT' ? 'negative' : 'default'}>
              {net.side === 'FLAT' ? 'FLAT' : `${net.side} ${net.amount}`}
            </BigValue>
          </MetricRow>
          <div className="flex items-baseline gap-1.5 text-xs">
            <span className="text-app-muted">PnL:</span>
            <span className={cn('font-mono font-bold tabular-nums', (parseAmount(net.pnlUsdt) ?? 0) >= 0 ? 'text-emerald-500' : 'text-app-danger')}>
              {net.pnlUsdt !== null ? `${net.pnlUsdt} USDT` : '—'}
            </span>
          </div>
          {!net.observed && <TargetLine>Positions not observed — figures may be stale</TargetLine>}
        </div>
      </KpiTile>

      <KpiTile label="Total assets">
        <div className="space-y-2.5">
          <div className="flex items-baseline justify-between gap-2">
            <BigValue>{portfolio ? formatUsdt(portfolio.total_usdt) : '—'}</BigValue>
            {delta?.pct != null && (
              <span className={cn('font-mono text-xs font-bold', (parseAmount(delta.pct) ?? 0) >= 0 ? 'text-emerald-500' : 'text-app-danger')}>
                {Number(delta.pct) >= 0 ? '+' : ''}{Number(delta.pct).toFixed(2)}%
              </span>
            )}
          </div>
          {portfolio ? (
            <>
              <div>
                <div className="flex h-2 w-full overflow-hidden rounded-pill bg-app-surface-muted" aria-hidden="true">
                  <div
                    className="h-full bg-app-action"
                    style={{ width: `${((parseAmount(portfolio.life_usdt) ?? 0) / Math.max(parseAmount(portfolio.total_usdt) ?? 1, 1)) * 100}%` }}
                  />
                </div>
                <div className="mt-1 flex justify-between text-[10px] text-app-muted">
                  <span>LIFE <span className="font-mono text-app-text">{formatUsdt(portfolio.life_usdt)}</span></span>
                  <span>USDT <span className="font-mono text-app-text">{formatUsdt(portfolio.usdt)}</span></span>
                </div>
              </div>
              <TargetLine>Valued at {portfolio.valuation.valuation_mark}{delta && !delta.adjusted_for_transfers && ' · transfers not adjusted'}</TargetLine>
            </>
          ) : (
            <TargetLine>Portfolio snapshot unavailable <BackendGapChip detail="OverviewSupplement.portfolio has no value yet." /></TargetLine>
          )}
        </div>
      </KpiTile>
    </div>
  )
}
