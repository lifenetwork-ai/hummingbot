import { describe, expect, it } from 'vitest'
import type { ReadResponse } from '../../core/api/admin'
import { deriveOverview, type OverviewInput } from './model'

type Status = ReadResponse<'getRuntimeStatus'>
type Liquidity = ReadResponse<'getLiquidity'>
type Balances = ReadResponse<'getBalances'>
type Positions = ReadResponse<'getPositions'>

const balances: Balances = {
  accounts: [{ account_id: 'acct-1', identity_verified: true, environment: 'demo', balances: [{
    available_cash: '1.00000001', available_equity: null, cash_balance: '2.00000001', connector: 'okx', currency: 'USDT',
    equity: null, observed_at: '2026-11-11T00:00:00Z', present_in_latest_snapshot: true, source: 'okx_account_balance', stale: false,
  }] }], owner_available: true, scope: 'account', server_time: '2026-11-11T00:00:00Z',
}
const positions: Positions = {
  markets: [{ connector: 'okx_perpetual', coverage: 'observed', items: [{
    amount: '0.001', entry_price: '20', leverage: '1', liquidation_price: null, margin: '0.020', margin_mode: 'cross',
    observed_at: '2026-11-11T00:00:00Z', pair: 'LIFE-USDT', side: 'SHORT', stale: false, unrealized_pnl: '0.0001',
  }], market: 'perpetual', observed_at: '2026-11-11T00:00:00Z', source: 'okx_account_positions', stale: true }],
  owner_available: true, server_time: '2026-11-11T00:00:00Z',
}

const status: Status = {
  acknowledged_version: 4, automatic_recovery: null, bot_id: 'bot-1', control_version: 5, desired_state: 'running',
  drawdown: null, error: null, funding_sync: null, heartbeat_age_seconds: 1, owner_available: true,
  owner_identity: null, process_identity: null, phase: 'running', running: true, server_time: '2026-11-11T00:00:00Z',
  observation: { observed_at: '2026-11-11T00:00:00Z', reason_code: null, session_matches: true, stale: false },
  observed_session: null, operator: { detail: 'Waiting for quotes', label: 'Waiting', next_action: 'monitor', state: 'waiting' },
  recovery: null, service: null, session: null, session_configuration_url: null, spot_outflow: null,
  time_remaining_seconds: null,
}
status.observed_session = {
  id: 'session-1', generation: 1, profile_id: 'follow_market', status: 'running', started_at: '2026-11-10T23:30:00Z',
  expires_at: null, step_index: 0, step_expires_at: null, config_hash: 'config-1', anchors: {},
  request: { duration_mode: 'until_stopped', duration_seconds: null, market_mode: 'spot', profile_id: 'follow_market', profile_ids: null, references: null, selection: 'fixed', step_duration_seconds: null },
}
const liquidity: Liquidity = {
  coverage_started_at: '2026-11-10T23:30:00Z', ended_at: '2026-11-11T00:00:00Z', generation: 1,
  markets: { spot: {
    ask_notional: null, best_ask: null, bid_notional: '0.001', best_bid: '10', buy_order_count: 1, connector: 'okx',
    elapsed_seconds: 1800, eligible: true, eligible_seconds: 1600, observed_seconds: 1750, pair: 'LIFE-USDT',
    quote_activity: { detail: 'Buy blocked by inventory', fill_observation: 'not_applicable', fill_observation_basis: 'current_working_orders',
      label: 'One sided', no_quote_seconds: 23, proposal_observed_at: '2026-11-11T00:00:00Z', state: 'one_sided', warning_active: true,
      warning_after_seconds: 20, sides: {
        BUY: { detail: 'Pending refresh', diagnosis: null, order_count: 1, proposed_count: 1, pending_create_count: 1, pending_cancel_count: 0, reason_codes: [], state: 'pending' },
        SELL: { detail: 'Inventory restricted', diagnosis: null, order_count: 0, proposed_count: 0, pending_create_count: 0, pending_cancel_count: 1, reason_codes: ['inventory_limit'], state: 'blocked' },
      } },
    working_orders: [{ side: 'BUY', price: '10', remaining_quantity: '0.0001', remaining_notional: '0.001', order_type: 'LIMIT_MAKER', state: 'OPEN' }],
    reason_code: 'inventory_limit', sell_order_count: 0, spread_bps: null, two_sided: false,
    two_sided_fraction_of_elapsed: 0.5, two_sided_seconds: 900, unknown_seconds: 50, valid: true, working_orders_with_fills: null,
  } }, measurement: 'sampled_owner_observations', observed_at: '2026-11-11T00:00:00Z',
  profile_id: null, session_id: 'session-1', started_at: null, state: 'observed',
}

function source<T>(data: T, phase: 'ready' | 'error' = 'ready') {
  return { phase, data, error: phase === 'error' ? new Error('offline') : null, refreshing: false }
}
function input(overrides: Partial<OverviewInput> = {}): OverviewInput {
  return {
    status: source(status), liquidity: source(liquidity),
    balances: source(balances),
    positions: source(positions),
    ...overrides,
  }
}

describe('deriveOverview', () => {
  it('keeps requested and observed sessions separate and diagnoses sides from sampled owner evidence', () => {
    const model = deriveOverview(input())
    expect(model.task.operator?.state).toBe('waiting')
    expect(model.markets[0].sides.map(({ side, pending, reasonCodes }) => ({ side, pending, reasonCodes }))).toEqual([
      { side: 'buy', pending: true, reasonCodes: [] }, { side: 'sell', pending: false, reasonCodes: ['inventory_limit'] },
    ])
    expect(model.markets[0].coverageAssessment).toBe('eligible_fraction_unavailable')
    expect(model.markets[0].twoSidedElapsedFraction).toBe(0.5)
    expect(model.markets[0].coverageWindow).toMatchObject({ measurement: 'cumulative_session', targetFraction: 0.99,
      reviewDurationSeconds: 1800, elapsedSeconds: 1800, observedSeconds: 1750, eligibleSeconds: 1600, unknownSeconds: 50,
      readinessIneligibleObservedSeconds: 150, guardConstrainedSecondsUnavailable: true, rollingWindowAvailable: false })
    expect(model.markets[0].warningThresholdStatus).toBe('aligned')
    expect(model.markets[0].sides[0]).toMatchObject({ price: '10', notional: '0.001' })
    expect(model.markets[0].sides[1]).toMatchObject({ reasonCodes: ['inventory_limit'], activity: { state: 'blocked' } })
    expect(model.markets[0].sides[0]).toMatchObject({ pendingCreateCount: 1, pendingCancelCount: 0 })
    expect(model.markets[0].workingOrders).toEqual(liquidity.markets.spot!.working_orders)
  })

  it('marks liquidity unknown when status session evidence is stale or mismatched', () => {
    const mismatched = { ...status, observation: { ...status.observation!, session_matches: false } }
    const model = deriveOverview(input({ status: source(mismatched) }))
    expect(model.observation.status).toBe('session_mismatch')
    expect(model.markets[0].observation).toBe('unknown')
  })

  it('keeps source errors independent and preserves exact balances and stale parent position coverage', () => {
    const model = deriveOverview(input({ balances: source(balances, 'error') }))
    expect(model.liquidity.phase).toBe('ready')
    expect(model.balances.phase).toBe('error')
    expect(model.accounts[0].balances[0]).toMatchObject({ cash_balance: '2.00000001', observation: 'stale' })
    expect(model.positionMarkets[0].observation).toBe('stale')
    expect(model.positionMarkets[0].positions[0].observation).toBe('stale')
  })

  it('keeps per-account balances and LONG/SHORT exposure values in source units', () => {
    const model = deriveOverview(input())
    expect(model.accounts[0].balances[0].cash_balance).toBe('2.00000001')
    expect(model.positionMarkets[0].positions[0]).toMatchObject({ side: 'SHORT', amount: '0.001', unrealized_pnl: '0.0001' })
  })

  it('does not call zero eligible time a passing coverage target', () => {
    const noEligible = { ...liquidity, markets: { spot: { ...liquidity.markets.spot!, eligible_seconds: 0 } } }
    expect(deriveOverview(input({ liquidity: source(noEligible) })).markets[0].coverageAssessment).toBe('no_eligible_time')
    const short = { ...liquidity, markets: { spot: { ...liquidity.markets.spot!, elapsed_seconds: 1799 } } }
    expect(deriveOverview(input({ liquidity: source(short) })).markets[0].coverageAssessment).toBe('insufficient_window')
  })
})

describe('liquidity identity and validity', () => {
  it('rejects an old generation within the same session', () => {
    const model = deriveOverview(input({ liquidity: source({ ...liquidity, generation: 2 }) }))
    expect(model.markets[0].observation).toBe('session_mismatch')
    expect(model.markets[0].workingOrders).toBeNull()
  })
  it('does not expose live orders for an invalid market sample', () => {
    const model = deriveOverview(input({ liquidity: source({ ...liquidity, markets: { spot: { ...liquidity.markets.spot!, valid: false } } }) }))
    expect(model.markets[0].workingOrders).toBeNull()
    expect(model.markets[0].sides[0].price).toBeNull()
  })
})
