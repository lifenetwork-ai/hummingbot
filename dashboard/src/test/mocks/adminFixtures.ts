import type { components } from '../../core/api/schema'

// Synthetic, test-only observations. Never imported by application pages.
export const emptyOrders = {
  items: [], next_cursor: null,
} satisfies components['schemas']['OrdersPageOutput']

export const recordedOrders = {
  items: [{
    id: 'fixture-order', connector: 'okx', market: 'spot', pair: 'LIFE-USDT',
    amount: '9007199254740993.00000001', price: '0.01234567890123456789',
    created_at: '2026-11-09T00:00:00Z', updated_at: '2026-11-09T00:01:00Z',
    creation_pending: false, exchange_order_id: 'fixture-exchange-order',
    leverage: 1, order_type: 'LIMIT', position: null, side: 'BUY', status: 'CANCELED',
    progress_basis: 'recorded_fills_only', recorded_filled_amount: '0',
    recorded_remaining_amount: '9007199254740993.00000001',
  }], next_cursor: null,
} satisfies components['schemas']['OrdersPageOutput']

export const pendingCommand = {
  id: 'fixture-command', action: 'stop', status: 'accepted', session_id: 'fixture-session',
  expected_version: 1, effective_version: 1, error: null,
  created_at: '2026-11-09T00:00:00Z', updated_at: '2026-11-09T00:00:00Z',
  payload: { action: 'stop', expected_version: 1, session_id: 'fixture-session' },
} satisfies components['schemas']['CommandResponseOutput']

export function apiFailure(
  code: string,
  retry: components['schemas']['ErrorResponse']['retry'],
): components['schemas']['ErrorResponse'] {
  return { error: 'fixture_error', code, retry, fields: [], detail: null,
    request_id: 'fixture-request', retry_after_seconds: null, service: null }
}

export const staleBalances = {
  owner_available: false, scope: 'account', server_time: '2026-11-09T00:10:00Z',
  accounts: [{ account_id: 'fixture-account', environment: 'demo', identity_verified: true,
    balances: [{ connector: 'okx', currency: 'USDT', cash_balance: '0',
      available_cash: null, equity: '123.000000000000000001', available_equity: null,
      observed_at: '2026-11-09T00:00:00Z', present_in_latest_snapshot: true,
      source: 'okx_account_balance', stale: true }],
  }],
} satisfies components['schemas']['BalancesResponseOutput']
