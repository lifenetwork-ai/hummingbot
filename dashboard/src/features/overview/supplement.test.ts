import { describe, expect, it } from 'vitest'
import type { OverviewSupplementSchemas } from './supplement-contract'
import { deriveOverview } from './model'
import { deriveCapitalRisk, deriveMandate } from './supplement'

type Supplement = OverviewSupplementSchemas['OverviewSupplementOutput']
const unknown = { value: null, stale: true, observed_at: null, unavailable_reason: 'unavailable' }
const loading = { phase: 'loading' as const, data: undefined, error: null, refreshing: false }

function supplement(): Supplement {
  return {
    contract_version: 1, server_time: '2026-11-13T00:00:00Z',
    account_scope: { account_ids: ['17'], environment: 'demo', identity_verified: true, site: null },
    session_id: 'session', session_started_at: null,
    session: { capital_allocated: unknown, delta: unknown, benchmark_hold: unknown, fees: unknown },
    portfolio: unknown, balances: unknown, inventory_limits: unknown, references: unknown, markets: [],
    risk_rules: { observed_at: '2026-11-13T00:00:00Z', stale: false, unavailable_reason: null,
      value: [{ controller_id: null, market: null, rule_id: 'drawdown_global', unit: 'USDT',
        lower_limit: null, upper_limit: '50', current: '12', status: 'ok', unavailable_reason: null }] },
  }
}

const model = deriveOverview({ status: loading, liquidity: loading, positions: loading, balances: loading })

describe('supplement risk completeness', () => {
  it.each(['partial', 'stale'])('does not claim all guards are stable for %s risk data', (reason) => {
    const data = supplement()
    data.risk_rules.unavailable_reason = reason === 'partial' ? 'risk_policy_observation_incomplete' : null
    data.risk_rules.stale = reason === 'stale'
    const summary = deriveCapitalRisk(model, data).guardsSummary
    expect(summary.available).toBe(false)
    expect(summary.status).toBe('attention')
  })

  it('keeps a known violation visible even if other risk rules are missing', () => {
    const data = supplement()
    data.risk_rules.value![0].status = 'violated'
    data.risk_rules.unavailable_reason = 'risk_policy_observation_incomplete'
    expect(deriveCapitalRisk(model, data).guardsSummary.status).toBe('stop')
  })

  it('keeps stale or timestamp-free observations unavailable without affecting independent fresh metrics', () => {
    const data = supplement()
    data.portfolio = { observed_at: '2026-11-13T00:00:00Z', stale: true, unavailable_reason: null, value: {
      total_usdt: '10', life_usdt: '4', usdt: '6', other_assets_usdt: '0',
      valuation: { currency: 'USDT', prices: [], valuation_mark: 'mid', valued_at: '2026-11-13T00:00:00Z' },
    } }
    data.session.fees = { observed_at: '2026-11-13T00:00:00Z', stale: false, unavailable_reason: null, value: {
      total_usdt: '0.01', by_market: null, conversion_basis: 'at_fill', history_complete: true,
      through_at: '2026-11-13T00:00:00Z', backfill_processed_fill_count: 1, caught_up_at: '2026-11-13T00:00:00Z',
      processed_fill_count: 1, processing_projection_version: 1, processing_state: 'ready', reported_projection_version: 1,
    } }
    const capital = deriveCapitalRisk(model, data)
    expect(capital.portfolio).toMatchObject({ available: false, reason: 'stale_observation' })
    expect(capital.fees).toMatchObject({ available: true, stale: false })

    data.portfolio = { observed_at: null, stale: false, unavailable_reason: null, value: {
      total_usdt: '10', life_usdt: '4', usdt: '6', other_assets_usdt: '0',
      valuation: { currency: 'USDT', prices: [], valuation_mark: 'mid', valued_at: '2026-11-13T00:00:00Z' },
    } }
    expect(deriveCapitalRisk(model, data).portfolio).toMatchObject({ available: false, reason: 'observation_time_unavailable' })

    data.session.fees = { ...data.session.fees, stale: true, unavailable_reason: 'refreshing' }
    expect(deriveCapitalRisk(model, data).fees).toMatchObject({ available: true, stale: true })
  })
})

it('counts unknown guards separately from breaches', () => {
  const data = supplement()
  data.risk_rules.value!.push({ ...data.risk_rules.value![0], rule_id: 'inventory_long', controller_id: 'perp', market: 'perpetual', status: 'unknown', current: null })
  expect(deriveCapitalRisk(model, data).guardsSummary).toMatchObject({ violationCount: 0, unknownCount: 1, checkedCount: 1, status: 'attention' })
})

it('does not claim guards stable before any observation', () => {
  expect(deriveCapitalRisk(model, null).guardsSummary.status).toBe('attention')
})

it('preserves controller scope, pinned quote budget and separate USD equity', () => {
  const data = supplement()
  data.risk_rules.value![0].controller_id = 'spot-controller'
  data.risk_rules.value![0].market = 'spot'
  data.session.quote_budget = { value: { total_usdt: '200', by_market: [{ market: 'spot', budget_usdt: '100' }, { market: 'perpetual', budget_usdt: '100' }], source: 'pinned_session_catalogue', basis: 'configured_working_orders_notional' }, observed_at: data.server_time, stale: false, unavailable_reason: null }
  data.account_equity = { value: { total_usd: '12345.67', currency: 'USD', source: 'okx_account_balance_total_eq' }, observed_at: data.server_time, stale: false, unavailable_reason: null }
  expect(deriveMandate(model, data)).toMatchObject({ quoteBudget: { available: true, value: { total_usdt: '200' } }, capitalAllocated: { available: false }, riskLimits: [{ controllerId: 'spot-controller', market: 'spot' }] })
  expect(deriveCapitalRisk(model, data).accountEquity).toMatchObject({ available: true, value: { total_usd: '12345.67', currency: 'USD' } })
  data.account_equity.stale = true
  expect(deriveCapitalRisk(model, data).accountEquity).toMatchObject({ available: false })
})

describe('supplement display formatting', () => {
  it('formats the guards headline instead of echoing raw backend decimals', () => {
    const data = supplement()
    data.risk_rules.value![0].status = 'violated'
    data.risk_rules.value![0].current = '1225.61449273833200000000007'
    data.risk_rules.value![0].upper_limit = '5000'
    expect(deriveCapitalRisk(model, data).guardsSummary.headline)
      .toBe('drawdown_global: current 1,225.61 / limit 5,000.00 (USDT)')
  })

  it('keeps fractional net position amounts instead of rounding them to 0', () => {
    const withPositions = deriveOverview({
      status: loading,
      liquidity: loading,
      balances: loading,
      positions: {
        phase: 'ready',
        data: {
          owner_available: true,
          server_time: '2026-11-13T00:00:00Z',
          markets: [{
            market: 'perpetual',
            connector: 'okx_perpetual',
            coverage: 'observed',
            source: 'okx_account_positions',
            observed_at: '2026-11-13T00:00:00Z',
            stale: false,
            items: [{
              side: 'LONG',
              amount: '0.0498',
              pair: 'BTC-USDT',
              unrealized_pnl: '-398.14',
              entry_price: '72900',
              leverage: '1',
              liquidation_price: null,
              margin: null,
              margin_mode: null,
              observed_at: '2026-11-13T00:00:00Z',
              stale: false,
            }],
          }],
        },
        error: null,
        refreshing: false,
      },
    })
    expect(deriveCapitalRisk(withPositions, null).netPosition).toMatchObject({ side: 'LONG', amount: '0.04980000 BTC', inventoryLimit: null, pnlUsdt: '-398.14' })
  })

  it('picks the perpetual side limit (long/short) for positions and net position, not the spot-only inventory_limit', () => {
    const data = supplement()
    data.inventory_limits = { observed_at: '2026-11-13T00:00:00Z', stale: false, unavailable_reason: null, value: [
      { controller_id: 'life-mm', market: 'perpetual', pair: 'BTC-USDT', asset: 'BTC',
        inventory_limit: null, long_limit: '0.08', short_limit: '0.06', gross_limit: '0.12',
        exposure_basis: null, exposure_observed_at: null, long_exposure: null, short_exposure: null },
    ] }
    const withPositions = deriveOverview({
      status: loading,
      liquidity: loading,
      balances: loading,
      positions: {
        phase: 'ready',
        data: {
          owner_available: true,
          server_time: '2026-11-13T00:00:00Z',
          markets: [{
            market: 'perpetual',
            connector: 'okx_perpetual',
            coverage: 'observed',
            source: 'okx_account_positions',
            observed_at: '2026-11-13T00:00:00Z',
            stale: false,
            items: [
              {
                side: 'LONG', amount: '0.0498', pair: 'BTC-USDT', unrealized_pnl: '-398.14',
                entry_price: '72900', leverage: '1', liquidation_price: null,
                margin: null, margin_mode: null, observed_at: '2026-11-13T00:00:00Z', stale: false,
              },
              {
                side: 'SHORT', amount: '0.02', pair: 'BTC-USDT', unrealized_pnl: '50',
                entry_price: '73000', leverage: '1', liquidation_price: null,
                margin: null, margin_mode: null, observed_at: '2026-11-13T00:00:00Z', stale: false,
              },
            ],
          }],
        },
        error: null,
        refreshing: false,
      },
    })
    const capital = deriveCapitalRisk(withPositions, data)
    expect(capital.positions[0]).toMatchObject({ side: 'LONG', inventoryLimit: '0.08' })
    expect(capital.positions[1]).toMatchObject({ side: 'SHORT', inventoryLimit: '0.06' })
    // net = 0.0498 - 0.02 = 0.0298 LONG → dùng long_limit
    expect(capital.netPosition).toMatchObject({ side: 'LONG', amount: '0.02980000 BTC', inventoryLimit: '0.08' })
  })
})
