/**
 * Map `status.overview` supplement (OverviewSupplementOutput) + model có sẵn
 * thành props cho các block trang Overview theo trục câu hỏi.
 * Thuần, không side effect; mọi observation wrapper được mở tại đây MỘT lần —
 * component chỉ nhận value hoặc `available: false`.
 *
 * Quy ước: thiếu dữ liệu → null/unavailable, không bao giờ biến thành 0 (UI-STRUCTURE-PLAN 5.3).
 */
import type { OverviewSupplementSchemas } from './supplement-contract'
import type { OverviewModel } from './model'
import { parseAmount } from './parseAmount'
import { formatAmount, formatPairAmount, sumDecimalAmounts } from '../../shared/utils/formatters'
import { summarizePositions } from './position-summary'
import { formatRiskValueForDisplay } from './formatNumbers'

type Supplement = OverviewSupplementSchemas['OverviewSupplementOutput']
type RiskRule = OverviewSupplementSchemas['OverviewRiskRuleOutput']
type CapitalAllocated = OverviewSupplementSchemas['OverviewCapitalAllocatedOutput']
type SessionDelta = OverviewSupplementSchemas['OverviewSessionDeltaOutput']
type SessionFees = OverviewSupplementSchemas['OverviewSessionFeesOutput']
type HoldBenchmark = OverviewSupplementSchemas['OverviewHoldBenchmarkOutput']
type Portfolio = OverviewSupplementSchemas['OverviewPortfolioOutput']

/** Observation wrapper từ backend: value null hoặc unavailable_reason → unavailable. */
export type Observed<T> =
  | { available: true; value: T; stale: boolean; observedAt: string | null }
  | { available: false; reason: string | null }

function observe<T>(o: { observed_at: string | null; stale: boolean; unavailable_reason: string | null; value: T | null }, retainStale = false): Observed<T> {
  return o.value !== null && o.observed_at !== null && ((!o.stale && o.unavailable_reason === null) || (retainStale && o.stale))
    ? { available: true, value: o.value, stale: o.stale, observedAt: o.observed_at }
    : { available: false, reason: o.unavailable_reason ?? (o.stale ? 'stale_observation' : o.value !== null ? 'observation_time_unavailable' : null) }
}

// ---------- Q1: Mandate ----------

export interface MandateLimitRow {
  ruleId: string
  controllerId?: string | null
  market?: string | null
  unavailableReason?: string | null
  current: string | null
  limit: string | null
  status: RiskRule['status']
  unit: RiskRule['unit']
}

export interface MandateView {
  profileId: string | null
  selection: 'fixed' | 'random'
  /** Random cycle: "#2" — bước hiện tại của chu kỳ random (null khi fixed) */
  randomStep: string | null
  marketMode: string
  durationMode: 'timed' | 'until_stopped'
  timeRemainingSeconds: number | null
  totalSessionSeconds: number | null
  running: boolean | null
  operationalStatus: 'RUNNING' | 'STARTING' | 'STOPPING' | 'STOPPED' | 'HALTED' | 'UNKNOWN' | 'STALE'
  observedAt: string | null
  /** Vốn phân bổ — observation (backend: session_configuration) */
  quoteBudget?: Observed<OverviewSupplementSchemas['OverviewQuoteBudgetOutput']>
  capitalAllocated: Observed<CapitalAllocated>
  riskLimits: MandateLimitRow[]
}

// ---------- Q3+Q4: Liquidity ----------

export interface LiquidityWhy {
  elapsedSeconds: number
  eligibleSeconds: number
  notEligibleSeconds: number
  unknownSeconds: number
  quotingAbsentSeconds: number | null
  diagnosis: string | null
  perMinute?: OverviewSupplementSchemas['OverviewCoverageMinuteOutput'][] | null
}

export interface LiquidityMarketView {
  market: string
  pair: string | null
  twoSided: boolean
  spreadActualBps: number | null
  spreadTargetBps: number | null
  bestBid: string | null
  bestAsk: string | null
  bidNotional: string | null
  askNotional: string | null
  buyOrderCount: number | null
  sellOrderCount: number | null
  coverageFraction: number | null
  /** Total-time sampled coverage, distinct from the eligible SLO denominator. */
  totalTimeCoverageFraction: number | null
  /** null khi backend chưa có SLA target được cấu hình. */
  coverageTargetFraction: number | null
  /** rolling 30m từ supplement; null → UI hiện "chưa có" và dùng cumulative */
  rollingCoverageAvailable: boolean
  measurementReady: boolean
  eligibleSeconds: number
  restrictedSeconds: number
  eligibilityUnknownSeconds: number
  measurementReason: string | null
  fullWindow: boolean | null
  why: LiquidityWhy
  depth: Observed<OverviewSupplementSchemas['OverviewDepthOutput']>
  slippage: Observed<OverviewSupplementSchemas['OverviewSlippageOutput']>
}

// ---------- Q5: Capital & risk ----------

export interface CapitalPositionView {
  market: string
  pair: string
  side: 'LONG' | 'SHORT'
  amount: string
  entry: string
  pnlUsdt: string | null
  marginUsdt: string | null
  liqPrice: string | null
  inventoryLimit: string | null
}

export interface CapitalBalanceRow {
  asset: string
  free: string | null
  reservedForOrders: string | null
}

export interface NetPositionView {
  pair: string | null
  side: 'LONG' | 'SHORT' | 'FLAT'
  amount: string
  /** Hạn mức theo side của net position: perpetual trả long_limit/short_limit (inventory_limit chỉ có ở spot); null khi supplement chưa có. */
  inventoryLimit: string | null
  pnlUsdt: string | null
  observed: boolean
}

export interface CapitalRiskView {
  positions: CapitalPositionView[]
  netPosition: NetPositionView
  accountEquity?: Observed<OverviewSupplementSchemas['OverviewAccountEquityOutput'] | { currency: 'USD'; source: string; total_usd: string }>
  portfolio: Observed<Portfolio>
  sessionDelta: Observed<SessionDelta>
  fees: Observed<SessionFees>
  holdBenchmark: Observed<HoldBenchmark>
  balances: CapitalBalanceRow[]
  guardsSummary: GuardsSummary
}

export interface GuardsSummary {
  status: 'ok' | 'attention' | 'stop'
  headline: string
  violationCount: number
  unknownCount?: number
  checkedCount?: number
  totalCount: number
  /** false khi supplement chưa implement (backend trả null) */
  available: boolean
}

// ---------- derive ----------

function guardsFromRules(rules: RiskRule[], complete: boolean): GuardsSummary {
  const active = rules.filter((rule) => rule.status !== 'disabled')
  const violated = active.filter((rule) => rule.status === 'violated')
  const unknown = active.filter((rule) => rule.status === 'unknown')
  const first = violated[0] ?? unknown[0]
  const headline = first
    ? `${first.rule_id}: current ${formatRiskValueForDisplay(first.current, first.unit)} / limit ${formatRiskValueForDisplay(first.upper_limit ?? first.lower_limit, first.unit)} (${first.unit})`
    : complete ? 'All limits are stable' : 'Risk limit data incomplete'
  return {
    status: active.some((r) => r.status === 'violated') ? 'stop' : unknown.length > 0 || !complete ? 'attention' : 'ok',
    headline,
    violationCount: violated.length,
    unknownCount: unknown.length,
    checkedCount: active.length - unknown.length,
    totalCount: active.length,
    available: complete && active.length > 0,
  }
}

const EMPTY_GUARDS: GuardsSummary = {
  status: 'attention',
  headline: 'Risk rules data unavailable (supplement not implemented)',
  violationCount: 0,
  unknownCount: 0,
  checkedCount: 0,
  totalCount: 0,
  available: false,
}

/** Runtime evidence takes precedence over the requested session state. */
function operationalStatus(model: OverviewModel): MandateView['operationalStatus'] {
  if (model.observation.status === 'stale') return 'STALE'
  if (model.observation.status !== 'fresh') return 'UNKNOWN'
  const recovery = model.risk.recovery
  if (recovery?.halted && recovery.next_action === 'wait') return 'STOPPING'
  if (recovery?.halted || model.task.runtime.phase === 'halted') return 'HALTED'
  switch (model.task.runtime.phase) {
    case 'running': return model.task.runtime.running === true ? 'RUNNING' : 'UNKNOWN'
    case 'starting': return 'STARTING'
    case 'stopping': return 'STOPPING'
    case 'stopped': return 'STOPPED'
    default: return 'UNKNOWN'
  }
}

export function deriveMandate(model: OverviewModel, supp: Supplement | null): MandateView {
  const requested = model.task.requested
  const observed = model.task.observed
  const selection = (requested?.request.selection ?? 'fixed') as 'fixed' | 'random'
  const modeLabels: Record<string, string> = { spot: 'Spot', perpetual: 'Perp', both: 'Spot + Perp' }
  return {
    profileId: observed?.profile_id ?? requested?.request.profile_id ?? null,
    selection,
    randomStep: selection === 'random' && model.task.randomCycle?.stepIndex != null
      ? `#${model.task.randomCycle.stepIndex}`
      : null,
    marketMode: modeLabels[requested?.request.market_mode ?? 'both'] ?? 'Spot + Perp',
    durationMode: requested?.request.duration_mode ?? 'timed',
    timeRemainingSeconds: model.task.timeRemainingSeconds,
    totalSessionSeconds: requested?.request.duration_seconds ?? null,
    running: model.task.runtime.running,
    operationalStatus: operationalStatus(model),
    observedAt: model.observation.status === 'fresh' || model.observation.status === 'stale' ? model.observation.observedAt : null,
    quoteBudget: supp?.session.quote_budget ? observe(supp.session.quote_budget) : { available: false as const, reason: null },
    capitalAllocated: supp ? observe(supp.session.capital_allocated) : { available: false as const, reason: null },
    riskLimits: (supp?.risk_rules.value ?? []).map((rule) => ({
      ruleId: rule.rule_id,
      controllerId: rule.controller_id,
      market: rule.market,
      unavailableReason: rule.unavailable_reason,
      current: rule.current,
      limit: rule.upper_limit ?? rule.lower_limit,
      status: rule.status,
      unit: rule.unit,
    })),
  }
}

export function deriveLiquidity(model: OverviewModel, supp: Supplement | null): LiquidityMarketView[] {
  const suppMarkets = new Map((supp?.markets ?? []).map((m) => [m.market, m]))
  return model.markets.map((market) => {
    const suppMarket = suppMarkets.get(market.market as 'spot' | 'perpetual')
    const rolling = suppMarket ? observe(suppMarket.coverage) : { available: false as const, reason: null }
    const rollingValue = rolling.available ? rolling.value : null
    const buy = market.sides.find((s) => s.side === 'buy')
    const sell = market.sides.find((s) => s.side === 'sell')
    const diagnosisSide = [buy, sell].find((s) => s?.diagnosis?.next_action)
    const spreadPlan = suppMarket?.spread_target ? observe(suppMarket.spread_target) : { available: false as const, reason: null }
    return {
      market: market.market,
      pair: market.sample.pair,
      twoSided: market.sample.two_sided,
      spreadActualBps: parseAmount(market.ownSpreadBps ?? null),
      spreadTargetBps: spreadPlan.available ? parseAmount(spreadPlan.value.target_bps) : null,
      bestBid: buy?.price ?? null,
      bestAsk: sell?.price ?? null,
      bidNotional: buy?.notional ?? null,
      askNotional: sell?.notional ?? null,
      buyOrderCount: buy?.orderCount ?? null,
      sellOrderCount: sell?.orderCount ?? null,
      coverageFraction: rollingValue?.conservative_eligible_fraction ?? null,
      totalTimeCoverageFraction: rollingValue?.rolling_30m_fraction ?? null,
      coverageTargetFraction: rollingValue?.target_fraction ?? null,
      rollingCoverageAvailable: rolling.available,
      measurementReady: rolling.available && rollingValue !== null && rollingValue.measurement_ready,
      eligibleSeconds: rollingValue?.eligible_seconds ?? 0,
      restrictedSeconds: rollingValue?.restricted_seconds ?? 0,
      eligibilityUnknownSeconds: rollingValue?.eligibility_unknown_seconds ?? 0,
      measurementReason: rollingValue?.measurement_reason ?? null,
      fullWindow: rollingValue?.full_window ?? null,
      why: {
        elapsedSeconds: rollingValue?.elapsed_seconds ?? market.coverageWindow.elapsedSeconds,
        eligibleSeconds: rollingValue?.eligible_seconds ?? market.coverageWindow.eligibleSeconds,
        notEligibleSeconds: rollingValue?.restricted_seconds ?? market.coverageWindow.readinessIneligibleObservedSeconds,
        unknownSeconds: rollingValue?.eligibility_unknown_seconds ?? market.coverageWindow.unknownSeconds,
        quotingAbsentSeconds: market.noQuoteSeconds,
        diagnosis: diagnosisSide?.diagnosis?.next_action ?? null,
        perMinute: rollingValue?.per_minute ?? null,
      },
      depth: suppMarket ? observe(suppMarket.depth) : { available: false as const, reason: null },
      slippage: suppMarket ? observe(suppMarket.slippage_estimate) : { available: false as const, reason: null },
    }
  })
}

export function deriveCapitalRisk(model: OverviewModel, supp: Supplement | null): CapitalRiskView {
  const inventoryLimits = new Map((supp?.inventory_limits.value ?? []).map((l) => [l.market, l]))
  // Perpetual trả long/short/gross limit (inventory_limit chỉ có ở spot) — chọn theo side của vị thế.
  const perpLimits = inventoryLimits.get('perpetual')
  const perpLimitFor = (side: 'LONG' | 'SHORT') => (side === 'SHORT' ? perpLimits?.short_limit : perpLimits?.long_limit) ?? perpLimits?.inventory_limit ?? null
  const positions: CapitalPositionView[] = model.positionMarkets
    .filter((m) => m.market === 'perpetual' && m.coverage !== 'not_applicable')
    .flatMap((m) =>
      (m.positions ?? []).map((p) => ({
        market: 'perpetual',
        pair: p.pair,
        side: p.side,
        amount: p.amount,
        entry: p.entry_price,
        pnlUsdt: p.unrealized_pnl ?? null,
        marginUsdt: p.margin ?? null,
        liqPrice: p.liquidation_price ?? null,
        inventoryLimit: perpLimitFor(p.side),
      })),
    )

  const summary = summarizePositions(model.positionMarkets.filter(market => market.market === 'perpetual'))
  const net = summary.observed && summary.sameInstrument ? positions.length ? sumDecimalAmounts(positions.map(p =>
    `${p.side === 'SHORT' ? '-' : ''}${p.amount.trim().replace(/^[+-]/, '')}`)) : '0' : null
  const pnlSum = summary.observed && positions.every(p => p.pair.endsWith('-USDT'))
    ? positions.length ? sumDecimalAmounts(positions.map(p => p.pnlUsdt)) : '0' : null
  const pnlDisplay = pnlSum === null ? null : `${pnlSum.startsWith('-') ? '' : '+'}${formatAmount(pnlSum, 2).replaceAll(',', '')}`

  return {
    positions,
    netPosition: {
      pair: summary.pair,
      side: net === null || !/[1-9]/.test(net) ? 'FLAT' : net.startsWith('-') ? 'SHORT' : 'LONG',
      inventoryLimit: perpLimitFor(net && net.startsWith('-') ? 'SHORT' : 'LONG'),
      amount: net === null ? '—' : summary.pair ? formatPairAmount(net.replace(/^-/, ''), summary.pair) : '0',
      pnlUsdt: pnlDisplay,
      observed: summary.observed && summary.sameInstrument,
    },
    accountEquity: supp?.account_equity ? observe(supp.account_equity) : { available: false as const, reason: null },
    portfolio: supp ? observe(supp.portfolio) : { available: false as const, reason: null },
    sessionDelta: supp ? observe(supp.session.delta) : { available: false as const, reason: null },
    fees: supp ? observe(supp.session.fees, true) : { available: false as const, reason: null },
    holdBenchmark: supp ? observe(supp.session.benchmark_hold, true) : { available: false as const, reason: null },
    balances: (supp?.balances.value ?? []).map((b) => ({ asset: b.asset, free: b.free, reservedForOrders: b.reserved_for_open_orders })),
    guardsSummary: supp?.risk_rules.value ? guardsFromRules(supp.risk_rules.value,
      supp.risk_rules.observed_at !== null && !supp.risk_rules.stale && supp.risk_rules.unavailable_reason === null) : EMPTY_GUARDS,
  }
}

/** Available only after the reporting contract is published and integrated. */
export function supplementAvailable(model: OverviewModel): boolean {
  return model.overviewSupplement != null
}
