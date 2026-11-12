import { ChevronDown } from 'lucide-react'
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

  return (
    <div className={cn('min-w-0 space-y-4', className)}>
      <p className="text-sm font-semibold text-app-text">
        {`${assessment.checkedCount === 0 ? '—' : assessment.violationCount} ${assessment.violationCount === 1 ? 'violation' : 'violations'} · ${assessment.checkedCount} of ${assessment.totalCount} rules checked · ${assessment.unverifiedCount} unverified`}
        {isLoading && <span className="sr-only"> Checking observations.</span>}
      </p>

      <div className="divide-y divide-app-border/60">
        {markets.map((market, index) => {
            const guard = market.quote_guard
            const rule = quoteRules[index]!
            const guardCurrent = rule.state !== 'unverified'
            const blocked = rule.state === 'blocked'
            const deviation = guard?.deviation_bps ?? null
            return (
              <section key={rule.id} aria-labelledby={`risk-rule-${rule.id}`} className="min-w-0 py-4 first:pt-0">
                <div className="flex min-w-0 flex-wrap items-center justify-between gap-2">
                  <h3 id={`risk-rule-${rule.id}`} className="min-w-0 break-words font-heavy text-base font-bold text-app-text">Rule {index + 1} · {rule.label}</h3>
                  <span className={cn('inline-flex items-center gap-2 text-xs font-semibold', !guardCurrent ? 'text-app-muted' : blocked ? 'text-app-danger' : 'text-app-success')}>
                    <span className={cn('h-2 w-2 shrink-0 rounded-full', !guardCurrent ? 'bg-app-muted/60' : blocked ? 'bg-rose-500' : 'bg-emerald-500')} aria-hidden="true" />
                    {riskStateLabel(rule.state)}
                  </span>
                </div>
                {guard && <dl className="mt-3 grid min-w-0 gap-x-4 gap-y-2 text-sm sm:grid-cols-2">
                  <div className="min-w-0"><dt className="text-xs text-app-muted">Deviation</dt><dd className="break-words font-mono font-semibold tabular-nums text-app-text">{deviation !== null ? formatSpread(deviation) : '—'}</dd></div>
                  <div className="min-w-0"><dt className="text-xs text-app-muted">Mode</dt><dd className="break-words font-mono text-app-text">{guard?.mode ?? 'unknown'}</dd></div>
                </dl>}
                {!guard && <p className="mt-2 text-sm text-app-muted">No quote guard observation for this market.</p>}
                {guard && <p className="mt-2 text-xs leading-5 text-app-muted">Maximum deviation threshold is not provided by the backend; the status reflects blockers only.</p>}
                {rule.reasonCode && <p className="mt-1 break-words text-sm text-app-danger">Reported reason: {rule.reasonCode}</p>}
                {guard?.detail && <p className="mt-1 break-words text-sm leading-5 text-app-muted">{guard.detail}</p>}
              </section>
            )
        })}

        {/* Drawdown */}
        <section aria-labelledby="risk-drawdown-heading" className="min-w-0 py-4">
          <h3 id="risk-drawdown-heading" className="font-heavy text-base font-bold text-app-text">Rule {markets.length + 1} · Drawdown</h3>
          <p className="mt-1 text-xs font-semibold text-app-muted">{riskStateLabel(drawdownRule.state)}</p>
          {drawdownBlocked && <p className="mt-2 break-words text-sm font-semibold text-app-danger">Blocker: {drawdown?.reason_code ?? 'Blocker reported'}</p>}
          <ThresholdBar
            current={drawdownCurrent ? parseOrNull(drawdown?.global_drawdown_quote) : null}
            max={parseOrNull(drawdown?.max_global_drawdown_quote)}
            currentLabel={drawdownCurrent ? formatUsdt(drawdown?.global_drawdown_quote) : '—'}
            maxLabel={drawdown?.max_global_drawdown_quote ? formatUsdt(drawdown.max_global_drawdown_quote) : null}
            className="mt-3"
          />
          <p className="mt-2 text-sm leading-5 text-app-muted">Global drawdown uses the covered bot V2 report peak; it does not represent the full exchange account loss.</p>
          {drawdown?.reason_code && !drawdownBlocked && <p className="mt-1 break-words text-sm text-app-muted">Reported reason: {drawdown.reason_code}</p>}
          {drawdownCurrent && drawdown?.enabled === false && <p className="mt-1 text-sm text-app-muted">Policy disabled.</p>}
          {!drawdownCurrent && <p className="mt-1 text-sm text-app-muted">Current state unverified.</p>}
        </section>

        {/* Spot Outflow */}
        <section aria-labelledby="risk-outflow-heading" className="min-w-0 py-4 last:pb-0">
          <h3 id="risk-outflow-heading" className="font-heavy text-base font-bold text-app-text">Rule {markets.length + 2} · Spot Outflow</h3>
          <p className="mt-1 text-xs font-semibold text-app-muted">{riskStateLabel(outflowRule.state)}</p>
          {outflowBlocked && (
            <p className="mt-2 break-words text-sm font-semibold text-app-danger">Buy restricted: {outflow?.reason_code ?? 'per policy'}</p>
          )}
          <ThresholdBar
            current={outflowCurrent ? parseOrNull(outflow?.recorded_net_outflow_quote) : null}
            max={parseOrNull(outflow?.policy?.max_net_outflow_quote)}
            currentLabel={outflowCurrent ? formatUsdt(outflow?.recorded_net_outflow_quote) : '—'}
            maxLabel={outflow?.policy?.max_net_outflow_quote ? formatUsdt(outflow.policy.max_net_outflow_quote) : null}
            className="mt-3"
          />
          <p className="mt-2 text-sm leading-5 text-app-muted">Recorded net outflow within the policy window; this is not the remaining allowance.</p>
          {!outflowBlocked && <p className="mt-1 text-sm text-app-muted">{outflowCurrent ? 'Backend permits buying.' : 'Current state unverified.'}</p>}
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
                      {policy.reason && <span className="break-words text-xs text-amber-500">blocker</span>}
                    </div>
                  </div>

                  {policy.reason && (
                    <p className="break-words text-sm leading-5 text-amber-500">Restriction reason: {policy.reason}</p>
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
                      {inv.reason_code && <p className="break-words text-amber-500">Inventory blocker: {inv.reason_code}</p>}
                    </div>
                  )}

                  {guard && (
                    <div className="break-words text-sm leading-5 text-app-muted">
                      <span className="font-semibold text-app-text">Quote Guard: </span>
                      {policy.observation === 'fresh' && guard.ready ? 'ready' : 'unverified'} · Deviation {guard.deviation_bps !== null && guard.deviation_bps !== undefined ? formatSpread(guard.deviation_bps) : '—'} · {guard.mode ?? 'unknown'}
                      {guard.reason_code && <span className="text-amber-500"> · Blocker: {guard.reason_code}</span>}
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
