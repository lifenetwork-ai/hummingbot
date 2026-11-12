import type { ReadResponse } from '../../core/api/admin'
import { contextStatus } from './contextFixtures'
import { pageProfiles } from '../operationalTest'
import { deriveOverview, type Source } from '../../features/overview/model'
import { buildConfigurationReview } from '../../features/session-control/configuration-review'
import { buildRecoveryComparison } from '../../features/recovery/comparison'
import { derivePerformancePanel } from '../../features/reporting/performance'

// Synthetic, typed inputs for component tests only. Do not import into runtime pages.
const observedAt = '2026-11-11T00:00:00Z'
export const completionStatus: ReadResponse<'getRuntimeStatus'> = { ...contextStatus, server_time: observedAt,
  observation: { stale: false, session_matches: true, observed_at: observedAt, reason_code: null },
  operator: { state: 'waiting', label: 'Inventory restriction', detail: 'Spot BUY is restricted; SELL observation remains independent.', next_action: 'wait' },
  session: { id: 'completion-session', generation: 3, profile_id: 'follow_market', status: 'running', started_at: observedAt, expires_at: null,
    step_index: 2, step_expires_at: '2026-11-11T00:02:00Z', config_hash: 'config-pinned', anchors: {},
    request: { duration_mode: 'until_stopped', duration_seconds: null, selection: 'random', market_mode: 'both', profile_id: null, profile_ids: ['follow_market'], references: null, step_duration_seconds: 120 } },
  markets: [{ market: 'spot', connector: 'okx', pair: 'LIFE-USDT', admitted: false, ready: false, reason: 'inventory_restricted', reference_price: '0.1', quote_guard: null, spread_policy: null,
    inventory_skew: { state: 'active', scope: 'account_cash', balance_observed_at: observedAt, evaluated_at: observedAt, base_cash: '0.000000001', cash_quote: '2.500001', base_pct: null, buy_multiplier: '0.1', sell_multiplier: '1', budget_scale: '0.5', valuation_price: '0.1', reason_code: 'inventory_restricted' } }],
}
completionStatus.observed_session = completionStatus.session
const balances: ReadResponse<'getBalances'> = { accounts: [], owner_available: false, server_time: observedAt, scope: 'account' }
const positions: ReadResponse<'getPositions'> = { markets: [], owner_available: false, server_time: observedAt }
const liquidity: ReadResponse<'getLiquidity'> = { session_id: 'completion-session', generation: 3, state: 'observed', measurement: 'sampled_owner_observations', observed_at: observedAt, started_at: observedAt, ended_at: null, profile_id: 'follow_market', coverage_started_at: observedAt, markets: { spot: {
  connector: 'okx', pair: 'LIFE-USDT', valid: true, eligible: false, two_sided: false, elapsed_seconds: 600, observed_seconds: 500, eligible_seconds: 400, unknown_seconds: 100, two_sided_seconds: 300, two_sided_fraction_of_elapsed: 0.5,
  best_bid: null, best_ask: '0.100001', bid_notional: null, ask_notional: '20.00000001', buy_order_count: 0, sell_order_count: 1, spread_bps: null, working_orders_with_fills: null, reason_code: 'inventory_restricted',
  quote_activity: { state: 'one_sided', label: 'Restricted BUY', detail: 'Inventory policy observation', no_quote_seconds: 25, warning_active: false, warning_after_seconds: 120, fill_observation: 'no_fills_on_working_orders', fill_observation_basis: 'current_working_orders', proposal_observed_at: observedAt,
    sides: { BUY: { state: 'blocked', detail: 'BUY restricted', order_count: 0, proposed_count: 0, pending_create_count: 1, pending_cancel_count: 0, reason_codes: ['inventory_restricted'], diagnosis: { stage: 'trading_conditions', source: 'controller_observation', error_type: null, observed_seconds: 25, next_action: 'Review inventory; do not relax guards.' } }, SELL: { state: 'working', detail: 'SELL observed', order_count: 1, proposed_count: null, pending_create_count: 0, pending_cancel_count: 1, reason_codes: [], diagnosis: null } } },
  working_orders: [{ side: 'SELL', price: '0.1', remaining_quantity: '200', remaining_notional: '20', order_type: 'LIMIT_MAKER', state: 'OPEN' }],
} } }
const source = <T>(data: T, phase: Source<T>['phase'] = 'ready'): Source<T> => ({ data, phase, error: phase === 'error' ? new Error('source unavailable') : null, refreshing: false })
export const completionOverview = deriveOverview({ status: source(completionStatus), balances: source(balances), positions: source(positions), liquidity: source(liquidity) })
export const completionStaleOverview = deriveOverview({ status: source(completionStatus, 'error'), balances: source(balances), positions: source(positions), liquidity: source(liquidity) })
export const completionConfiguration = buildConfigurationReview(pageProfiles, { catalogue: { ...pageProfiles.catalogue, max_total_amount_quote: '9007199254740993.000001' }, catalogue_hash: 'catalogue-pinned', config_hash: 'config-pinned', session_id: 'completion-session', profile_id: 'follow_market', profile_context: 'at_creation', source: 'session_created_event', legacy_overrides: [], request: completionStatus.session!.request })
export const completionRecoveryComparison = buildRecoveryComparison([{ id: 'repair-1', controller_id: 'controller-1', connector: 'okx_perpetual', pair: 'LIFE-USDT', valuation_basis: 'reconstructed_average_entry_not_live_pnl',
  before: { side: 'BUY', amount: '1.00000001', breakeven_price: '0.1', cum_fees_quote: '0.000001', realized_pnl_quote: '-25', unrealized_pnl_quote: '-1', volume_traded_quote: '50.000000001' },
  after: { side: 'BUY', amount: '1.00000001', breakeven_price: '0.2', cum_fees_quote: '0.000001', realized_pnl_quote: '0', unrealized_pnl_quote: '0', volume_traded_quote: '50.000000001' },
}])
export const completionUnavailablePerformance = derivePerformancePanel({ phase: 'error', error: new Error('report unavailable'), refreshing: false })
