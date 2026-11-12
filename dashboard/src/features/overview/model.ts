import type { ReadResponse } from '../../core/api/admin'

type Status = ReadResponse<'getRuntimeStatus'>
type Liquidity = ReadResponse<'getLiquidity'>
type Balances = ReadResponse<'getBalances'>
type Positions = ReadResponse<'getPositions'>
type QuoteActivity = NonNullable<Status['quote_activity']>[string]
type QuoteSideActivity = NonNullable<NonNullable<QuoteActivity>['sides'][string]>

export function quoteSideActivity(
  sides: NonNullable<QuoteActivity>['sides'] | null | undefined,
  side: 'buy' | 'sell',
): QuoteSideActivity | null {
  const apiSide = side === 'buy' ? 'BUY' : 'SELL'
  return sides?.[apiSide] ?? sides?.[side] ?? null
}
function remainingAtObservation(expiresAt: string | null, observedAt: string | null): number | null {
  const end = Date.parse(expiresAt ?? '')
  const observed = Date.parse(observedAt ?? '')
  return Number.isFinite(end) && Number.isFinite(observed) ? Math.max(0, (end - observed) / 1000) : null
}

export type Source<T> = {
  phase: 'loading' | 'ready' | 'error'
  data?: T
  error: Error | null
  refreshing: boolean
}
export type OverviewInput = {
  status: Source<Status>
  liquidity: Source<Liquidity>
  balances: Source<Balances>
  positions: Source<Positions>
}

export type CoverageAssessment = 'insufficient_window' | 'no_eligible_time' | 'eligible_fraction_unavailable' | 'unknown'
export type WarningThresholdStatus = 'aligned' | 'deployment_threshold_differs' | 'unavailable'
export type ObservationState = 'fresh' | 'stale' | 'unknown' | 'session_mismatch'

/** Keep backend values and each source lifecycle intact for views to render independently. */
export function deriveOverview({ status, liquidity, balances, positions }: OverviewInput) {
  const statusMatchesSession = status.data?.observation?.session_matches
  const statusCurrent = status.phase === 'ready' && status.data?.owner_available === true &&
    status.data.observation?.stale === false && status.data.observation.observed_at !== null && statusMatchesSession === true
  const sessionMismatch = status.phase === 'ready' && statusMatchesSession === false
  const liquiditySessionMismatch = statusCurrent && (liquidity.data?.session_id !== (status.data?.observed_session?.id ?? null) || liquidity.data?.generation !== status.data?.observed_session?.generation)
  const liquidityCurrent = liquidity.phase === 'ready' && liquidity.data?.state === 'observed' && liquidity.data.observed_at !== null && statusCurrent && !liquiditySessionMismatch

  const markets = Object.entries(liquidity.data?.markets ?? {}).map(([market, sample]) => {
    const marketCurrent = liquidityCurrent && sample.valid
    const activity = marketCurrent
      ? sample.quote_activity ?? status.data?.quote_activity?.[market] ?? null
      : statusCurrent ? status.data?.quote_activity?.[market] ?? null : null
    const elapsedSeconds = sample.elapsed_seconds
    const eligibleSeconds = sample.eligible_seconds
    const coverageAssessment: CoverageAssessment = !liquidityCurrent || !sample.valid
      ? 'unknown'
      : elapsedSeconds < 1800
        ? 'insufficient_window'
        : eligibleSeconds <= 0
          ? 'no_eligible_time'
          : 'eligible_fraction_unavailable'
    return {
      market,
      sample,
      activity,
      observation: liquiditySessionMismatch ? 'session_mismatch' as const : !liquidityCurrent || !sample.valid ? 'unknown' as const : 'fresh' as const,
      twoSidedElapsedFraction: sample.two_sided_fraction_of_elapsed,
      ownSpreadBps: sample.spread_bps,
      coverageAssessment,
      coverageWindow: {
        measurement: 'cumulative_session' as const,
        targetFraction: 0.99,
        reviewDurationSeconds: 1800,
        elapsedSeconds,
        observedSeconds: sample.observed_seconds,
        eligibleSeconds,
        unknownSeconds: sample.unknown_seconds,
        readinessIneligibleObservedSeconds: Math.max(0, sample.observed_seconds - eligibleSeconds),
        guardConstrainedSecondsUnavailable: true as const,
        eligibleFractionUnavailable: true as const,
        rollingWindowAvailable: false as const,
      },
      noQuoteSeconds: activity?.no_quote_seconds ?? null,
      workingOrders: marketCurrent ? sample.working_orders ?? null : null,
      warningAfterSeconds: activity?.warning_after_seconds ?? null,
      warningThresholdStatus: !activity ? 'unavailable' as const : activity.warning_after_seconds === 20 ? 'aligned' as const : 'deployment_threshold_differs' as const,
      escalationAfterSeconds: 60,
      sides: (['buy', 'sell'] as const).map((side) => {
        const sideActivity = quoteSideActivity(activity?.sides, side)
        return {
        side,
        orderCount: marketCurrent ? side === 'buy' ? sample.buy_order_count : sample.sell_order_count : statusCurrent ? sideActivity?.order_count ?? null : null,
        notional: marketCurrent ? side === 'buy' ? sample.bid_notional : sample.ask_notional : null,
        price: marketCurrent ? side === 'buy' ? sample.best_bid : sample.best_ask : null,
        activity: sideActivity,
        pending: (liquidityCurrent || statusCurrent) && sideActivity?.state === 'pending',
        pendingCreateCount: sideActivity?.pending_create_count ?? null,
        pendingCancelCount: sideActivity?.pending_cancel_count ?? null,
        reasonCodes: sideActivity?.reason_codes ?? [],
        diagnosis: sideActivity?.diagnosis ?? null,
      }}),
    }
  })

  // Balance entries belong to an account snapshot. Preserve account identity and never
  // sum the same shared wallet across markets or currencies.
  const accounts = (balances.data?.accounts ?? []).map((account) => ({
    accountId: account.account_id,
    identityVerified: account.identity_verified,
    environment: account.environment,
    balances: account.balances.map((balance) => ({
      ...balance,
      observation: balances.phase !== 'ready' || !balances.data?.owner_available || balance.stale || !balance.present_in_latest_snapshot
        ? 'stale' as const : 'fresh' as const,
    })),
  }))

  const positionMarkets = (positions.data?.markets ?? []).map((market) => ({
    ...market,
    observation: positions.phase !== 'ready' || !positions.data?.owner_available || market.coverage !== 'observed'
      ? 'unknown' as const : market.stale ? 'stale' as const : 'fresh' as const,
    positions: (market.items ?? []).map((position) => ({ ...position, observation: positions.phase !== 'ready' || !positions.data?.owner_available || market.stale || market.coverage !== 'observed' || position.stale ? 'stale' as const : 'fresh' as const })),
  }))

  return {
    status,
    liquidity,
    balances,
    positions,
    task: {
      requested: status.data?.session ?? null,
      observed: status.data?.observed_session ?? null,
      operator: status.data?.operator ?? null,
      timeRemainingSeconds: status.data?.time_remaining_seconds ?? null,
      randomCycle: status.data?.session?.request.selection === 'random' ? {
        stepIndex: status.data.session.step_index,
        stepExpiresAt: status.data.session.step_expires_at,
        stepDurationSeconds: status.data.session.request.step_duration_seconds,
        remainingAtObservationSeconds: remainingAtObservation(status.data.session.step_expires_at, status.data.server_time),
        observation: statusCurrent ? 'fresh' as const : 'unknown' as const,
        observedAt: status.data.observation?.observed_at ?? null,
      } : null,
      runtime: { service: status.data?.service ?? null, ownerAvailable: status.data?.owner_available ?? null,
        running: status.data?.running ?? null, phase: status.data?.phase ?? null,
        observation: statusCurrent ? 'fresh' as const : 'unknown' as const },
    },
    observation: {
      status: sessionMismatch ? 'session_mismatch' as const : statusCurrent ? 'fresh' as const : status.data?.observation?.observed_at ? 'stale' as const : 'unknown' as const,
      risk: statusCurrent ? 'fresh' as const : status.data?.observation?.observed_at ? 'stale' as const : 'unknown' as const,
      sessionMatches: status.data?.observation?.session_matches ?? null,
      observedAt: status.data?.observation?.observed_at ?? null,
      liquiditySessionMismatch,
      liquidityObservedAt: liquidity.data?.observed_at ?? null,
      balancesObservedAt: balances.data?.server_time ?? null,
      accountBalanceObservedAt: accounts.map((account) => account.balances.map((balance) => balance.observed_at)),
      positionsObservedAt: positions.data?.server_time ?? null,
      positionObservedAt: positionMarkets.map((market) => market.positions.map((position) => position.observed_at)),
      service: status.data?.service ?? null,
    },
    markets,
    risk: {
      drawdown: status.data?.drawdown ?? null,
      spotOutflow: status.data?.spot_outflow ?? null,
      readiness: status.data?.markets ?? [],
      marketPolicies: (status.data?.markets ?? []).map((market) => ({
        market: market.market, pair: market.pair, connector: market.connector,
        observation: statusCurrent ? 'fresh' as const : 'unknown' as const,
        ready: market.ready, admitted: market.admitted, reason: market.reason,
        inventory: market.inventory_skew, guard: market.quote_guard, spreadPolicy: market.spread_policy,
        sideEvidence: status.data?.quote_activity?.[market.market]?.sides ?? null,
        positions: positionMarkets.filter((position) => position.market === market.market),
      })),
      recovery: status.data?.recovery ?? null,
    },
    accounts,
    positionMarkets,
  }
}

export type OverviewModel = ReturnType<typeof deriveOverview>
