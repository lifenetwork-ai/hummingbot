import { AlertCircle, AlertTriangle, ChevronDown, Info, ShieldCheck } from 'lucide-react'
import type { components } from '../../core/api/schema'
import { quoteSideActivity, type ObservationState, type OverviewModel } from './model'
import { assessRiskRules, type RiskRuleState } from './risk-assessment'
import { ThresholdBar } from './visuals/ThresholdBar'
import { Badge } from '../../shared/components/Badge'
import { cn } from '../../shared/utils/cn'
import { formatSpread, formatUsdt } from '../../shared/utils/formatters'

export interface RiskGuardsCardProps {
  status?: components['schemas']['StatusResponseOutput'] | null
  observation: ObservationState
  isLoading?: boolean
  className?: string
  marketPolicies?: OverviewModel['risk']['marketPolicies']
  risk?: OverviewModel['risk']
  positions?: OverviewModel['positionMarkets']
  /** Limit → current list từ OverviewSupplement.risk_rules, render ở đầu dialog. */
  ruleLimits?: React.ReactNode
}

function parseOrNull(value: string | null | undefined): number | null {
  if (value === null || value === undefined || value === '') return null
  const num = parseFloat(value)
  return Number.isFinite(num) ? num : null
}

function riskStateLabel(state: RiskRuleState): string {
  if (state === 'blocked') return 'Blocker'
  if (state === 'clear') return 'No blocker reported'
  return 'Unverified'
}

/**
 * Risk policy observations. Per-market policy details remain available in an accordion.
 */
export function RiskGuardsCard({
  status,
  observation,
  isLoading,
  className,
  marketPolicies,
  risk,
  positions,
  ruleLimits,
}: RiskGuardsCardProps) {
  const assessment = assessRiskRules(status, observation, {
    drawdown: risk?.drawdown ?? status?.drawdown,
    spotOutflow: risk?.spotOutflow ?? status?.spot_outflow,
  })
  const markets = status?.markets ?? []
  const quoteRules = assessment.rules.filter((rule) => rule.kind === 'quote_guard')
  const drawdownRule = assessment.rules.find((rule) => rule.kind === 'drawdown')!
  const outflowRule = assessment.rules.find((rule) => rule.kind === 'spot_outflow')!
  const drawdown = risk?.drawdown ?? status?.drawdown
  const outflow = risk?.spotOutflow ?? status?.spot_outflow

  const policies = marketPolicies !== undefined
    ? marketPolicies
    : risk?.marketPolicies !== undefined
      ? risk.marketPolicies
      : (status?.markets ?? []).map((market) => ({
          market: market.market,
          pair: market.pair,
          connector: market.connector,
          observation: observation === 'fresh' ? ('fresh' as const) : ('unknown' as const),
          ready: market.ready,
          admitted: market.admitted,
          reason: market.reason,
          inventory: market.inventory_skew,
          guard: market.quote_guard,
          spreadPolicy: market.spread_policy,
          sideEvidence: status?.quote_activity?.[market.market]?.sides ?? null,
          positions: (positions ?? []).filter((pos) => pos.market === market.market),
        }))

  const drawdownCurrent = drawdownRule.state !== 'unverified'
  const outflowCurrent = outflowRule.state !== 'unverified'
  const drawdownBlocked = drawdownRule.state === 'blocked'
  const outflowBlocked = outflowRule.state === 'blocked'

  const drawdownCurrentNum = drawdownCurrent ? parseOrNull(drawdown?.global_drawdown_quote) : null
  const drawdownMaxNum = parseOrNull(drawdown?.max_global_drawdown_quote)
  const drawdownRatio = drawdownCurrentNum !== null && drawdownMaxNum !== null && drawdownMaxNum > 0
    ? drawdownCurrentNum / drawdownMaxNum
    : 0
  const isDrawdownWarn = drawdownRatio >= 0.7 && !drawdownBlocked

  const outflowCurrentNum = outflowCurrent ? parseOrNull(outflow?.recorded_net_outflow_quote) : null
  const outflowMaxNum = parseOrNull(outflow?.policy?.max_net_outflow_quote)
  const outflowRatio = outflowCurrentNum !== null && outflowMaxNum !== null && outflowMaxNum > 0
    ? outflowCurrentNum / outflowMaxNum
    : 0
  const isOutflowWarn = outflowRatio >= 0.7 && !outflowBlocked

  const hasViolations = assessment.violationCount > 0
  const hasWarnings = isOutflowWarn || isDrawdownWarn

  const warningLabel = isOutflowWarn
    ? `Spot Outflow at ${(outflowRatio * 100).toFixed(1)}% of limit`
    : isDrawdownWarn
      ? `Global Drawdown at ${(drawdownRatio * 100).toFixed(1)}% of limit`
      : null

  return (
    <div className={cn('min-w-0 space-y-4', className)}>
      {/* Top Health Status Banner */}
      <div
        className={cn(
          'rounded-card border p-3.5 transition-colors',
          hasViolations
            ? 'border-app-danger/40 bg-app-danger/10'
            : hasWarnings
              ? 'border-app-warning/40 bg-app-warning/10'
              : 'border-app-border/40 bg-app-surface-muted/20'
        )}
      >
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <span
              className={cn(
                'flex h-6 w-6 shrink-0 items-center justify-center rounded-full',
                hasViolations
                  ? 'bg-app-danger/20 text-app-danger'
                  : hasWarnings
                    ? 'bg-app-warning/20 text-app-warning'
                    : 'bg-app-success/20 text-app-success'
              )}
            >
              {hasViolations ? (
                <AlertCircle size={14} />
              ) : hasWarnings ? (
                <AlertTriangle size={14} />
              ) : (
                <ShieldCheck size={14} />
              )}
            </span>
            <span className="text-sm font-bold text-app-text">
              {hasViolations
                ? `${assessment.violationCount} Policy Violation${assessment.violationCount > 1 ? 's' : ''}`
                : hasWarnings
                  ? 'Policy Limit Attention'
                  : 'All Policies Protected'}
            </span>
          </div>

          {warningLabel && (
            <span className="rounded-full bg-app-warning/20 px-2.5 py-0.5 text-xs font-semibold text-app-warning">
              {warningLabel}
            </span>
          )}
        </div>

        <p className="mt-1.5 text-xs font-medium text-app-muted">
          {`${assessment.checkedCount === 0 ? '—' : assessment.violationCount} ${assessment.violationCount === 1 ? 'violation' : 'violations'} · ${assessment.checkedCount} of ${assessment.totalCount} rules checked · ${assessment.unverifiedCount} unverified`}
          {isLoading && <span className="sr-only"> Checking observations.</span>}
        </p>
      </div>

      {ruleLimits && (
        <details className="group rounded-card border border-app-border/40 bg-app-surface-muted/20 p-3" open>
          <summary className="flex cursor-pointer select-none items-center justify-between text-xs font-bold uppercase tracking-wider text-app-muted hover:text-app-text">
            <span>Configured limits (limit → current)</span>
            <ChevronDown size={14} className="shrink-0 transition-transform group-open:rotate-180" />
          </summary>
          <div className="mt-3">
            {ruleLimits}
          </div>
        </details>
      )}

      <div className="space-y-3">
        {markets.map((market, index) => {
          const guard = market.quote_guard
          const rule = quoteRules[index]!
          const guardCurrent = rule.state !== 'unverified'
          const blocked = rule.state === 'blocked'
          const deviation = guard?.deviation_bps ?? null
          return (
            <section
              key={rule.id}
              aria-labelledby={`risk-rule-${rule.id}`}
              className={cn(
                'rounded-card border p-3.5 transition-colors',
                blocked
                  ? 'border-app-danger/40 bg-app-danger/5'
                  : 'border-app-border/40 bg-app-surface/40'
              )}
            >
              <div className="flex min-w-0 flex-wrap items-center justify-between gap-2">
                <h3 id={`risk-rule-${rule.id}`} className="min-w-0 break-words font-heavy text-base font-bold text-app-text">
                  Rule {index + 1} · {rule.label}
                </h3>
                <span
                  className={cn(
                    'inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-semibold',
                    !guardCurrent
                      ? 'bg-app-surface-muted text-app-muted'
                      : blocked
                        ? 'bg-app-danger/20 text-app-danger'
                        : 'bg-app-success/20 text-app-success'
                  )}
                >
                  <span
                    className={cn(
                      'h-1.5 w-1.5 shrink-0 rounded-full',
                      !guardCurrent ? 'bg-app-muted/60' : blocked ? 'bg-app-danger' : 'bg-app-success'
                    )}
                    aria-hidden="true"
                  />
                  {riskStateLabel(rule.state)}
                </span>
              </div>

              {guard ? (
                <div className="mt-3">
                  <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                    <div className="rounded-md border border-app-border/30 bg-app-surface-muted/30 p-2">
                      <span className="text-[10px] uppercase tracking-wider text-app-muted">Deviation</span>
                      <p className="mt-0.5 font-mono text-sm font-bold tabular-nums text-app-text">
                        {deviation !== null ? formatSpread(deviation) : '—'}
                      </p>
                    </div>
                    <div className="rounded-md border border-app-border/30 bg-app-surface-muted/30 p-2">
                      <span className="text-[10px] uppercase tracking-wider text-app-muted">Mode</span>
                      <p className="mt-0.5 font-mono text-sm font-semibold text-app-text">
                        {guard?.mode ?? 'unknown'}
                      </p>
                    </div>
                    {guard?.detail && (
                      <div className="col-span-2 rounded-md border border-app-border/30 bg-app-surface-muted/30 p-2 sm:col-span-1">
                        <span className="text-[10px] uppercase tracking-wider text-app-muted">Boundary Detail</span>
                        <p className="mt-0.5 truncate font-mono text-xs text-app-muted" title={guard.detail}>
                          {guard.detail}
                        </p>
                      </div>
                    )}
                  </div>

                  <div className="mt-2.5 flex items-start gap-1.5 rounded bg-app-surface-muted/20 px-2.5 py-1.5 text-xs leading-5 text-app-muted">
                    <Info size={13} className="mt-0.5 shrink-0 text-app-muted/80" aria-hidden="true" />
                    <span>Maximum deviation threshold is not provided by the backend; the status reflects blockers only.</span>
                  </div>
                </div>
              ) : (
                <p className="mt-2 text-xs text-app-muted">No quote guard observation for this market.</p>
              )}

              {rule.reasonCode && (
                <p className="mt-2 rounded bg-app-danger/10 px-2 py-1 font-mono text-xs font-semibold text-app-danger">
                  Reported reason: {rule.reasonCode}
                </p>
              )}
            </section>
          )
        })}

        {/* Drawdown */}
        <section
          aria-labelledby="risk-drawdown-heading"
          className={cn(
            'rounded-card border p-3.5 transition-colors',
            drawdownBlocked
              ? 'border-app-danger/40 bg-app-danger/5'
              : isDrawdownWarn
                ? 'border-app-warning/40 bg-app-warning/5 ring-1 ring-app-warning/30'
                : 'border-app-border/40 bg-app-surface/40'
          )}
        >
          <div className="flex min-w-0 flex-wrap items-center justify-between gap-2">
            <h3 id="risk-drawdown-heading" className="font-heavy text-base font-bold text-app-text">
              Rule {markets.length + 1} · Drawdown
            </h3>
            <span
              className={cn(
                'inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-semibold',
                !drawdownCurrent
                  ? 'bg-app-surface-muted text-app-muted'
                  : drawdownBlocked
                    ? 'bg-app-danger/20 text-app-danger'
                    : isDrawdownWarn
                      ? 'bg-app-warning/20 text-app-warning'
                      : 'bg-app-success/20 text-app-success'
              )}
            >
              <span
                className={cn(
                  'h-1.5 w-1.5 shrink-0 rounded-full',
                  !drawdownCurrent
                    ? 'bg-app-muted/60'
                    : drawdownBlocked
                      ? 'bg-app-danger'
                      : isDrawdownWarn
                        ? 'bg-app-warning'
                        : 'bg-app-success'
                )}
                aria-hidden="true"
              />
              {riskStateLabel(drawdownRule.state)}
            </span>
          </div>

          {drawdownBlocked && (
            <p className="mt-2 break-words text-xs font-semibold text-app-danger">
              Blocker: {drawdown?.reason_code ?? 'Blocker reported'}
            </p>
          )}

          <ThresholdBar
            current={drawdownCurrentNum}
            max={drawdownMaxNum}
            currentLabel={drawdownCurrent ? formatUsdt(drawdown?.global_drawdown_quote) : '—'}
            maxLabel={drawdown?.max_global_drawdown_quote ? formatUsdt(drawdown.max_global_drawdown_quote) : null}
            className="mt-3"
          />

          <div className="mt-2.5 flex items-start gap-1.5 rounded bg-app-surface-muted/20 px-2.5 py-1.5 text-sm leading-5 text-app-muted">
            <Info size={13} className="mt-0.5 shrink-0 text-app-muted/80" aria-hidden="true" />
            <span>Global drawdown uses the covered bot V2 report peak; it does not represent the full exchange account loss.</span>
          </div>

          {drawdown?.reason_code && !drawdownBlocked && (
            <p className="mt-1.5 font-mono text-xs text-app-muted">Reported reason: {drawdown.reason_code}</p>
          )}
          {drawdownCurrent && drawdown?.enabled === false && (
            <p className="mt-1.5 text-xs text-app-muted">Policy disabled.</p>
          )}
          {!drawdownCurrent && (
            <p className="mt-1.5 text-xs text-app-muted">Current state unverified.</p>
          )}
        </section>

        {/* Spot Outflow */}
        <section
          aria-labelledby="risk-outflow-heading"
          className={cn(
            'rounded-card border p-3.5 transition-colors',
            outflowBlocked
              ? 'border-app-danger/40 bg-app-danger/5'
              : isOutflowWarn
                ? 'border-app-warning/40 bg-app-warning/5 ring-1 ring-app-warning/30'
                : 'border-app-border/40 bg-app-surface/40'
          )}
        >
          <div className="flex min-w-0 flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <h3 id="risk-outflow-heading" className="font-heavy text-base font-bold text-app-text">
                Rule {markets.length + 2} · Spot Outflow
              </h3>
              {isOutflowWarn && (
                <span className="rounded bg-app-warning/20 px-1.5 py-0.5 text-[10px] font-bold text-app-warning">
                  Near Limit ({Math.round(outflowRatio * 100)}%)
                </span>
              )}
            </div>
            <span
              className={cn(
                'inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-semibold',
                !outflowCurrent
                  ? 'bg-app-surface-muted text-app-muted'
                  : outflowBlocked
                    ? 'bg-app-danger/20 text-app-danger'
                    : isOutflowWarn
                      ? 'bg-app-warning/20 text-app-warning'
                      : 'bg-app-success/20 text-app-success'
              )}
            >
              <span
                className={cn(
                  'h-1.5 w-1.5 shrink-0 rounded-full',
                  !outflowCurrent
                    ? 'bg-app-muted/60'
                    : outflowBlocked
                      ? 'bg-app-danger'
                      : isOutflowWarn
                        ? 'bg-app-warning'
                        : 'bg-app-success'
                )}
                aria-hidden="true"
              />
              {riskStateLabel(outflowRule.state)}
            </span>
          </div>

          {outflowBlocked && (
            <p className="mt-2 break-words text-xs font-semibold text-app-danger">
              Buy restricted: {outflow?.reason_code ?? 'per policy'}
            </p>
          )}

          <ThresholdBar
            current={outflowCurrentNum}
            max={outflowMaxNum}
            currentLabel={outflowCurrent ? formatUsdt(outflow?.recorded_net_outflow_quote) : '—'}
            maxLabel={outflow?.policy?.max_net_outflow_quote ? formatUsdt(outflow.policy.max_net_outflow_quote) : null}
            className="mt-3"
          />

          <div className="mt-2.5 flex items-start gap-1.5 rounded bg-app-surface-muted/20 px-2.5 py-1.5 text-sm leading-5 text-app-muted">
            <Info size={13} className="mt-0.5 shrink-0 text-app-muted/80" aria-hidden="true" />
            <span>Recorded net outflow within the policy window; this is not the remaining allowance.</span>
          </div>

          {!outflowBlocked && (
            <p className="mt-1.5 text-xs text-app-muted">
              {outflowCurrent ? 'Backend permits buying.' : 'Current state unverified.'}
            </p>
          )}
        </section>
      </div>

      {/* Market policies accordion */}
      {policies.length > 0 && (
        <details className="group mt-2 min-w-0 rounded-card bg-app-surface-muted/30 p-3 sm:p-4">
          <summary className="flex min-h-10 cursor-pointer select-none items-center justify-between gap-3 text-sm font-semibold text-app-text hover:text-app-action focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-app-action">
            <span className="min-w-0 break-words">Supplemental market policy details</span>
            <ChevronDown size={16} className="shrink-0 transition-transform group-open:rotate-180" />
          </summary>
          <p className="mt-1 text-xs leading-5 text-app-muted">Additional context only; these details do not add counted rules.</p>
          <div className="mt-3 min-w-0 space-y-4 border-t border-app-border/40 pt-3">
            {policies.map((policy) => {
              const inv = policy.inventory
              const guard = policy.guard
              const buyEvidence = quoteSideActivity(policy.sideEvidence, 'buy')
              const sellEvidence = quoteSideActivity(policy.sideEvidence, 'sell')
              return (
                <div key={`${policy.market}:${policy.pair}`} className="min-w-0 space-y-3 border-b border-app-border/40 pb-4 last:border-0 last:pb-0">
                  <div className="flex min-w-0 flex-wrap items-start justify-between gap-2">
                    <span className="min-w-0 break-words text-sm font-bold text-app-text">
                      {policy.pair}
                      <span className="ml-1.5 break-words font-normal uppercase text-app-muted">({policy.market} · {policy.connector})</span>
                    </span>
                    <div className="flex min-w-0 flex-wrap items-center gap-2">
                      <Badge tone={policy.observation !== 'fresh' ? 'neutral' : policy.ready ? 'success' : policy.admitted ? 'brand' : 'warning'} className="text-[10px]">
                        {policy.observation !== 'fresh' ? 'NO DATA' : policy.ready ? 'Ready' : policy.admitted ? 'Admitted' : 'Not admitted'}
                      </Badge>
                      {policy.reason && <span className="break-words text-xs text-app-warning">blocker</span>}
                    </div>
                  </div>

                  {policy.reason && (
                    <p className="break-words text-sm leading-5 text-app-warning">Restriction reason: {policy.reason}</p>
                  )}

                  {inv && (
                    <div className="min-w-0 space-y-2 text-sm text-app-muted">
                      <p className="break-words font-semibold text-app-text">Inventory Skew <span className="font-normal text-app-muted">(scope: {inv.scope ?? 'unknown'})</span></p>
                      <dl className="grid min-w-0 grid-cols-1 gap-x-4 gap-y-2 sm:grid-cols-2">
                        <div className="min-w-0"><dt className="text-xs">Base cash</dt><dd className="break-all font-mono text-app-text">{inv.base_cash ?? '—'}</dd></div>
                        <div className="min-w-0"><dt className="text-xs">Cash quote</dt><dd className="break-all font-mono text-app-text">{inv.cash_quote ? formatUsdt(inv.cash_quote) : '—'}</dd></div>
                        <div className="min-w-0"><dt className="text-xs">Buy multiplier</dt><dd className="break-all font-mono text-app-text">{inv.buy_multiplier ?? '—'}x</dd></div>
                        <div className="min-w-0"><dt className="text-xs">Sell multiplier</dt><dd className="break-all font-mono text-app-text">{inv.sell_multiplier ?? '—'}x</dd></div>
                        {inv.valuation_price && <div className="min-w-0"><dt className="text-xs">Valuation price</dt><dd className="break-all font-mono text-app-text">{inv.valuation_price}</dd></div>}
                      </dl>
                      {inv.reason_code && <p className="break-words text-app-warning">Inventory blocker: {inv.reason_code}</p>}
                    </div>
                  )}

                  {guard && (
                    <div className="break-words text-sm leading-5 text-app-muted">
                      <span className="font-semibold text-app-text">Quote Guard: </span>
                      {policy.observation === 'fresh' && guard.ready ? 'ready' : 'unverified'} · Deviation {guard.deviation_bps !== null && guard.deviation_bps !== undefined ? formatSpread(guard.deviation_bps) : '—'} · {guard.mode ?? 'unknown'}
                      {guard.reason_code && <span className="text-app-warning"> · Blocker: {guard.reason_code}</span>}
                    </div>
                  )}

                  {(buyEvidence?.diagnosis || sellEvidence?.diagnosis) && (
                    <div className="break-words text-sm leading-5 text-app-muted">
                      <span className="font-semibold text-app-action">Backend guidance: </span>
                      {buyEvidence?.diagnosis?.next_action && <span>Buy: {buyEvidence.diagnosis.next_action}. </span>}
                      {sellEvidence?.diagnosis?.next_action && <span>Sell: {sellEvidence.diagnosis.next_action}.</span>}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </details>
      )}
    </div>
  )
}
