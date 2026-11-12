import type { components } from '../../core/api/schema'
import type { ObservationState } from './model'

export type RiskRuleState = 'blocked' | 'clear' | 'unverified'
export type RiskRuleKind = 'quote_guard' | 'drawdown' | 'spot_outflow'

export interface RiskRuleAssessment {
  id: string
  kind: RiskRuleKind
  label: string
  state: RiskRuleState
  reasonCode: string | null
}

export interface RiskAssessment {
  rules: RiskRuleAssessment[]
  checkedCount: number
  totalCount: number
  violationCount: number
  unverifiedCount: number
}

/** One quote guard per market, followed by the global drawdown and spot outflow rules. */
export function assessRiskRules(
  status: components['schemas']['StatusResponseOutput'] | null | undefined,
  observation: ObservationState,
  overrides?: {
    drawdown?: components['schemas']['DrawdownStatusOutput'] | null
    spotOutflow?: components['schemas']['SpotOutflowStatusOutput'] | null
  },
): RiskAssessment {
  const current = observation === 'fresh'
  const rules: RiskRuleAssessment[] = (status?.markets ?? []).map((market, index) => {
    const guard = market.quote_guard
    const verified = current && Boolean(guard?.ready && guard.data_quality !== 'unavailable' && guard.evaluated_at)
    return {
      id: `quote_guard:${index}`,
      kind: 'quote_guard',
      label: `Quote Guard${market.market ? ` — ${market.market}` : ''}${market.pair ? ` (${market.pair})` : ''}`,
      state: !verified ? 'unverified' : guard?.reason_code ? 'blocked' : 'clear',
      reasonCode: guard?.reason_code ?? null,
    }
  })

  const drawdown = overrides?.drawdown ?? status?.drawdown
  const drawdownVerified = current && Boolean(drawdown?.ready && !drawdown.stale && drawdown.observed_at)
  rules.push({
    id: 'drawdown',
    kind: 'drawdown',
    label: 'Drawdown',
    state: !drawdownVerified ? 'unverified' : drawdown?.enabled && drawdown.reason_code ? 'blocked' : 'clear',
    reasonCode: drawdown?.reason_code ?? null,
  })

  const outflow = overrides?.spotOutflow ?? status?.spot_outflow
  const outflowVerified = current && Boolean(outflow?.ready && !outflow.stale && outflow.observed_at)
  rules.push({
    id: 'spot_outflow',
    kind: 'spot_outflow',
    label: 'Spot Outflow',
    state: !outflowVerified ? 'unverified' : outflow?.buy_allowed ? 'clear' : 'blocked',
    reasonCode: outflow?.reason_code ?? (outflowVerified && !outflow?.buy_allowed ? 'buy_denied' : null),
  })

  const checkedCount = rules.filter((rule) => rule.state !== 'unverified').length
  const violationCount = rules.filter((rule) => rule.state === 'blocked').length
  return {
    rules,
    checkedCount,
    totalCount: rules.length,
    violationCount,
    unverifiedCount: rules.length - checkedCount,
  }
}
