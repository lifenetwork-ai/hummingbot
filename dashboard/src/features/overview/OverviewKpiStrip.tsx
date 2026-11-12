import { ArrowUpRight } from 'lucide-react'
import type { OverviewModel } from './model'
import { summarizePositions } from './position-summary'
import { assessRiskRules } from './risk-assessment'
import { InfoStatusBadge } from './visuals/InfoStatusBadge'
import { Surface } from '../../shared/components/Surface'
import { cn } from '../../shared/utils/cn'
import { formatAssetAmount, formatPrice, formatPercentage, formatUsdt } from '../../shared/utils/formatters'

export interface OverviewKpiStripProps {
  overview: OverviewModel
  isLoading?: boolean
  className?: string
  onOpenRiskGuards?: () => void
}

function KpiTile({ label, children, className }: { label: string; children: React.ReactNode; className?: string }) {
  return (
    <Surface className={cn('rounded-card p-3.5 sm:p-4', className)}>
      <span className="text-[11px] font-medium uppercase tracking-wider text-app-muted">{label}</span>
      <div className="mt-2">{children}</div>
    </Surface>
  )
}

function PrimaryValue({ children, tone }: { children: React.ReactNode; tone?: 'default' | 'positive' | 'negative' }) {
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

/** Four core KPI cards below Control Bar. */
export function OverviewKpiStrip({ overview, isLoading, className, onOpenRiskGuards }: OverviewKpiStripProps) {
  // Account equity is not total portfolio valuation.
  const account = overview.accounts.length === 1 ? overview.accounts[0] : undefined
  const usdt = account?.balances.find((b) => b.currency === 'USDT') ?? null
  const balancesCurrent = overview.balances.phase === 'ready' && usdt?.observation === 'fresh'
  const usdtEquity = balancesCurrent && usdt?.equity !== null ? usdt?.equity ?? null : null
  const multiAccount = overview.accounts.length > 1

  const positionSummary = summarizePositions(overview.positionMarkets)
  const { netExposure, netPnl, pair: positionPair } = positionSummary
  const allPositions = positionSummary.positions
  const positionsCurrent = overview.positions.phase === 'ready' && overview.positions.data?.owner_available === true && positionSummary.observed
  const hasPositions = allPositions.length > 0

  // Session coverage is distinct from an unsupported rolling SLA.
  const coverageByMarket = overview.markets.map((m) => ({
    market: m.market as string,
    fraction: m.twoSidedElapsedFraction,
    targetFraction: m.coverageWindow.targetFraction,
    observation: m.observation,
  }))

  // --- KPI 4: Aggregated Risk Guards (evaluated independently, not collapsed into general safety) ---
  const status = overview.status.data
  const riskAssessment = assessRiskRules(status, overview.observation.risk)

  return (
    <div className={cn('grid grid-cols-1 gap-3 sm:gap-4 md:grid-cols-2 xl:grid-cols-4', className)}>
      {/* Account equity */}
      <KpiTile label="Account USDT equity">
        {overview.balances.phase === 'loading' ? (
          <div className="h-8 w-32 animate-pulse rounded bg-app-surface-muted" />
        ) : multiAccount ? (
          <InfoStatusBadge label="PER ACCOUNT" detail="Multiple accounts observed; see Shared Balances for each account. No accounts are summed." />
        ) : usdtEquity === null ? (
          <div className="flex items-center gap-2">
            <PrimaryValue>—</PrimaryValue>
            <InfoStatusBadge label={overview.balances.phase === 'error' ? 'API ERROR' : usdt?.observation === 'stale' ? 'STALE' : 'NO DATA'} tone="neutral" detail="No current USDT equity observation; see Shared Balances for cash and account details" />
          </div>
        ) : (
          <div className="space-y-2.5">
            <div className="flex items-baseline gap-2">
              <PrimaryValue>{formatAssetAmount(usdtEquity, 'USDT')}</PrimaryValue>
            </div>
            <p className="text-xs text-app-muted">USDT equity · one account</p>
          </div>
        )}
      </KpiTile>

      {/* 2. Net Position & PnL */}
      <KpiTile label={`Net Position${positionPair ? ` (${positionPair.replace(/-USDT$/, '')})` : ''}`}>
        {overview.positions.phase === 'loading' ? (
          <p className="text-xs text-app-muted">Loading positions…</p>
        ) : !positionsCurrent ? (
          <div className="flex items-center gap-2">
            <PrimaryValue>—</PrimaryValue>
            <InfoStatusBadge label={overview.positions.phase === 'error' ? 'API ERROR' : hasPositions ? 'STALE' : 'NO DATA'} detail="No complete current derivative position observation; cannot infer Flat" />
          </div>
        ) : !hasPositions ? (
          <div className="flex items-center gap-2">
            <PrimaryValue>FLAT</PrimaryValue>
            <InfoStatusBadge label="OBSERVED" tone="neutral" detail="No derivative positions in observed scope" />
          </div>
        ) : netExposure === null ? (
          <div className="flex items-center gap-2">
            <PrimaryValue>{allPositions.length} positions</PrimaryValue>
            <InfoStatusBadge label="DIFFERENT INSTRUMENTS" detail="Positions have different instruments; cannot calculate net exposure" />
          </div>
        ) : (
          <div className="space-y-1">
            <div className="flex items-baseline gap-2">
              <PrimaryValue tone={netExposure === 0 ? 'default' : netExposure > 0 ? 'positive' : 'negative'}>
                {netExposure > 0 ? 'LONG' : netExposure < 0 ? 'SHORT' : 'FLAT'} {netExposure !== 0 ? formatPrice(Math.abs(netExposure), 4) : ''}
              </PrimaryValue>
            </div>
            <div className="flex items-center gap-1.5 text-xs">
              <span className="text-app-muted">PnL:</span>
              <span className={cn('font-mono font-bold tabular-nums', netPnl !== null && netPnl >= 0 ? 'text-emerald-500' : 'text-app-danger')}>
                {formatUsdt(netPnl)}
              </span>
            </div>
          </div>
        )}
      </KpiTile>

      {/* Cumulative session coverage */}
      <KpiTile label="Two-Sided Coverage (session)">
        {overview.liquidity.phase === 'loading' ? (
          <div className="h-8 w-32 animate-pulse rounded bg-app-surface-muted" />
        ) : coverageByMarket.length === 0 ? (
          <div className="flex items-center gap-2">
            <PrimaryValue>—</PrimaryValue>
            <InfoStatusBadge label={overview.liquidity.phase === 'error' ? 'API ERROR' : 'NO DATA'} detail="No current cumulative session coverage observation" />
          </div>
        ) : (
          <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
            {coverageByMarket.map((c) => (
              <span key={c.market} className="flex items-baseline gap-1.5">
                <span className="text-[10px] font-medium uppercase tracking-wider text-app-muted">{c.market}</span>
                <PrimaryValue tone={c.fraction !== null && c.fraction >= c.targetFraction ? 'positive' : 'default'}>
                  {formatPercentage(c.observation === 'fresh' ? c.fraction : null)}
                </PrimaryValue>
                <span className="text-[10px] text-app-muted" title="Cumulative session coverage target">/ {Math.round(c.targetFraction * 100)}%</span>
              </span>
            ))}
            {coverageByMarket.some((c) => c.observation !== 'fresh') && (
              <InfoStatusBadge label="NO DATA" detail="Liquidity observation is not fresh" />
            )}
            <p className="w-full text-xs text-app-muted">Cumulative session · rolling SLA unavailable</p>
          </div>
        )}
      </KpiTile>

      {/* 4. Capital Protection (Risk Guards) */}
      <KpiTile label="Capital Protection">
        {isLoading && !status ? (
          <div className="h-8 w-32 animate-pulse rounded bg-app-surface-muted" />
        ) : (
          <div>
            <div className="flex items-center gap-2">
              <PrimaryValue tone={riskAssessment.violationCount > 0 ? 'negative' : 'default'}>
                {riskAssessment.checkedCount === 0 ? '—' : `${riskAssessment.violationCount}/${riskAssessment.totalCount}`}
              </PrimaryValue>
              <span className="text-xs text-app-muted">{riskAssessment.violationCount === 1 ? 'violation' : 'violations'}</span>
            </div>
            <p className="mt-1 text-xs text-app-muted">
              {riskAssessment.checkedCount === 0
                ? 'No rule observations yet'
                : `${riskAssessment.checkedCount} of ${riskAssessment.totalCount} rules checked${riskAssessment.unverifiedCount > 0 ? ` · ${riskAssessment.unverifiedCount} unverified` : ''}`}
            </p>
            <button
              type="button"
              onClick={onOpenRiskGuards}
              aria-haspopup="dialog"
              className={cn(
                'mt-2 inline-flex min-h-10 items-center gap-1 text-[11px] font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-app-action',
                riskAssessment.violationCount > 0 ? 'text-app-danger hover:underline' : 'text-app-action hover:underline',
              )}
            >
              <span>View rule details</span>
              <ArrowUpRight size={13} aria-hidden="true" />
            </button>
          </div>
        )}
      </KpiTile>
    </div>
  )
}
