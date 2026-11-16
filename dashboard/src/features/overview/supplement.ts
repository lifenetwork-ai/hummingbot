/**
 * Map `status.overview` supplement (OverviewSupplementOutput) + model có sẵn
 * thành props cho các block trang Overview theo trục câu hỏi.
 * Thuần, không side effect; mọi observation wrapper được mở tại đây MỘT lần —
 * component chỉ nhận value hoặc `available: false`.
 *
 * Quy ước: thiếu dữ liệu → null/unavailable, không bao giờ biến thành 0 (UI-STRUCTURE-PLAN 5.3).
 */
import type { components } from '../../core/api/schema'
import type { OverviewModel } from './model'
import { parseAmount } from './parseAmount'

type Supplement = components['schemas']['OverviewSupplementOutput']
type RiskRule = components['schemas']['OverviewRiskRuleOutput']
type CapitalAllocated = components['schemas']['OverviewCapitalAllocatedOutput']
type SessionDelta = components['schemas']['OverviewSessionDeltaOutput']
type SessionFees = components['schemas']['OverviewSessionFeesOutput']
type HoldBenchmark = components['schemas']['OverviewHoldBenchmarkOutput']
type Portfolio = components['schemas']['OverviewPortfolioOutput']

/** Observation wrapper từ backend: value null hoặc unavailable_reason → unavailable. */
export type Observed<T> =
  | { available: true; value: T; stale: boolean; observedAt: string | null }
  | { available: false; reason: string | null }

function observe<T>(o: { observed_at: string | null; stale: boolean; unavailable_reason: string | null; value: T | null }): Observed<T> {
  return o.value !== null
    ? { available: true, value: o.value, stale: o.stale, observedAt: o.observed_at }
    : { available: false, reason: o.unavailable_reason }
}

// ---------- Q1: Mandate ----------

export interface MandateLimitRow {
  ruleId: string
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
  observedAt: string | null
  /** Vốn phân bổ — observation (backend: session_configuration) */
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
  /** null khi backend chưa có SLA target được cấu hình. */
  coverageTargetFraction: number | null
  /** rolling 30m từ supplement; null → UI hiện "chưa có" và dùng cumulative */
  rollingCoverageAvailable: boolean
  fullWindow: boolean | null
  why: LiquidityWhy
  depth: Observed<components['schemas']['OverviewDepthOutput']>
  slippage: Observed<components['schemas']['OverviewSlippageOutput']>
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
  pnlUsdt: string | null
  observed: boolean
}

export interface CapitalRiskView {
  positions: CapitalPositionView[]
  netPosition: NetPositionView
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
  totalCount: number
  /** false khi supplement chưa implement (backend trả null) */
  available: boolean
}

// ---------- derive ----------

function guardsFromRules(rules: RiskRule[], complete: boolean): GuardsSummary {
  const active = rules.filter((rule) => rule.status !== 'disabled')
  const violated = active.filter((rule) => rule.status === 'violated' || rule.status === 'unknown')
  const first = violated[0]
  const headline = first
    ? `${first.rule_id}: current ${first.current ?? '—'} / limit ${first.upper_limit ?? first.lower_limit ?? '—'} (${first.unit})`
    : complete ? 'All limits are stable' : 'Risk limit data incomplete'
  return {
    status: active.some((r) => r.status === 'violated') ? 'stop' : violated.length > 0 || !complete ? 'attention' : 'ok',
    headline,
    violationCount: violated.length,
    totalCount: active.length,
    available: complete && active.length > 0,
  }
}

const EMPTY_GUARDS: GuardsSummary = {
  status: 'ok',
  headline: 'Risk rules data unavailable (supplement not implemented)',
  violationCount: 0,
  totalCount: 0,
  available: false,
}

function fractionToBps(v: string | null | undefined): number | null {
  const n = parseAmount(v ?? null)
  return n !== null ? Math.round(n * 10_000) : null
}

/** Display convention: bps values are always whole numbers. */
function roundBps(v: number | null): number | null {
  return v !== null ? Math.round(v) : null
}

/** Spread target = spread cố định đầu tiên của policy (buy/sell lấy max cho an toàn). */
function spreadTargetBps(policy: OverviewModel['risk']['marketPolicies'][number]['spreadPolicy']): number | null {
  if (!policy || !policy.enabled) return null
  const buy = policy.buy_spreads.length > 0 ? fractionToBps(policy.buy_spreads[0]) : null
  const sell = policy.sell_spreads.length > 0 ? fractionToBps(policy.sell_spreads[0]) : null
  const widest = Math.max(buy ?? 0, sell ?? 0)
  return widest > 0 ? widest : (buy ?? sell ?? null)
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
    observedAt: model.observation.status === 'fresh' || model.observation.status === 'stale' ? model.observation.observedAt : null,
    capitalAllocated: supp ? observe(supp.session.capital_allocated) : { available: false as const, reason: null },
    riskLimits: (supp?.risk_rules.value ?? []).map((rule) => ({
      ruleId: rule.rule_id,
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
    const policy = model.risk.marketPolicies.find((p) => p.market === market.market)
    return {
      market: market.market,
      pair: market.sample.pair,
      twoSided: market.sample.two_sided,
      spreadActualBps: roundBps(parseAmount(market.ownSpreadBps ?? null)),
      spreadTargetBps: policy ? spreadTargetBps(policy.spreadPolicy) : null,
      bestBid: buy?.price ?? null,
      bestAsk: sell?.price ?? null,
      bidNotional: buy?.notional ?? null,
      askNotional: sell?.notional ?? null,
      buyOrderCount: buy?.orderCount ?? null,
      sellOrderCount: sell?.orderCount ?? null,
      coverageFraction: rollingValue?.rolling_30m_fraction ?? market.twoSidedElapsedFraction ?? null,
      coverageTargetFraction: rollingValue?.target_fraction ?? null,
      rollingCoverageAvailable: rolling.available,
      fullWindow: rollingValue?.full_window ?? null,
      why: {
        elapsedSeconds: market.coverageWindow.elapsedSeconds,
        eligibleSeconds: market.coverageWindow.eligibleSeconds,
        notEligibleSeconds: market.coverageWindow.readinessIneligibleObservedSeconds,
        unknownSeconds: market.coverageWindow.unknownSeconds,
        quotingAbsentSeconds: market.noQuoteSeconds,
        diagnosis: diagnosisSide?.diagnosis?.next_action ?? null,
      },
      depth: suppMarket ? observe(suppMarket.depth) : { available: false as const, reason: null },
      slippage: suppMarket ? observe(suppMarket.slippage_estimate) : { available: false as const, reason: null },
    }
  })
}

export function deriveCapitalRisk(model: OverviewModel, supp: Supplement | null): CapitalRiskView {
  const inventoryLimits = new Map((supp?.inventory_limits.value ?? []).map((l) => [l.market, l]))
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
        inventoryLimit: inventoryLimits.get('perpetual')?.inventory_limit ?? null,
      })),
    )

  const long = positions.filter((p) => p.side === 'LONG')
  const short = positions.filter((p) => p.side === 'SHORT')
  const longAmount = long.reduce((sum, p) => sum + (parseAmount(p.amount) ?? 0), 0)
  const shortAmount = short.reduce((sum, p) => sum + (parseAmount(p.amount) ?? 0), 0)
  const net = longAmount - shortAmount
  const pnlSum = positions.reduce((sum, p) => sum + (parseAmount(p.pnlUsdt ?? null) ?? 0), 0)
  const pair = positions[0]?.pair ?? null

  return {
    positions,
    netPosition: {
      pair,
      side: net > 0 ? 'LONG' : net < 0 ? 'SHORT' : positions.length > 0 ? 'FLAT' : 'FLAT',
      amount: Math.abs(net).toLocaleString('en-US', { maximumFractionDigits: 0 }),
      pnlUsdt: positions.length > 0 ? `${pnlSum >= 0 ? '+' : ''}${pnlSum.toFixed(2)}` : null,
      observed: model.positionMarkets.some((m) => m.observation === 'fresh'),
    },
    portfolio: supp ? observe(supp.portfolio) : { available: false as const, reason: null },
    sessionDelta: supp ? observe(supp.session.delta) : { available: false as const, reason: null },
    fees: supp ? observe(supp.session.fees) : { available: false as const, reason: null },
    holdBenchmark: supp ? observe(supp.session.benchmark_hold) : { available: false as const, reason: null },
    balances: (supp?.balances.value ?? []).map((b) => ({ asset: b.asset, free: b.free, reservedForOrders: b.reserved_for_open_orders })),
    guardsSummary: supp?.risk_rules.value ? guardsFromRules(supp.risk_rules.value, !supp.risk_rules.stale && supp.risk_rules.unavailable_reason === null) : EMPTY_GUARDS,
  }
}

/** Cho biết supplement đã được backend triển khai chưa (null cho đến khi collectors xong). */
export function supplementAvailable(model: OverviewModel): boolean {
  return model.status.data?.overview != null
}
