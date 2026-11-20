import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

vi.mock('@tanstack/react-router', () => ({
  Link: ({ children, to, className, 'aria-label': ariaLabel }: { children?: React.ReactNode; to?: string; className?: string; 'aria-label'?: string }) => (
    <a href={to ?? '#'} className={className} aria-label={ariaLabel}>
      {children}
    </a>
  ),
}))
import { completionOverview, completionStatus } from '../../test/mocks/completionFixtures'
import type { OverviewSupplementSchemas } from './supplement-contract'
import type { OverviewModel } from './model'
import { deriveCapitalRisk, deriveLiquidity, deriveMandate } from './supplement'
import { MandateBar } from './MandateBar'
import { OverviewKpiStripV2 } from './OverviewKpiStripV2'

function positions(pair = 'BTC-USDT', amount = '0.0498', stale = false): OverviewModel['positionMarkets'] {
  return [{ market: 'perpetual', connector: 'okx_perpetual', coverage: 'observed', source: 'okx_account_positions', observed_at: '2026-11-18T00:00:00Z', stale,
    observation: stale ? 'stale' : 'fresh', positions: [{ pair, side: 'LONG', amount, unrealized_pnl: '-1.2', entry_price: '70000', leverage: '1', margin: null, margin_mode: 'cross', liquidation_price: null, observed_at: '2026-11-18T00:00:00Z', stale, observation: stale ? 'stale' : 'fresh' }] }]
}

describe('asset-aware overview', () => {
  it('names the net position report link from its visible label and context', () => {
    const capital = deriveCapitalRisk(completionOverview, null)
    render(<OverviewKpiStripV2 capital={capital} liquidity={[]} />)
    expect(screen.getByRole('link', { name: 'View report for net position and PnL' })).toHaveAttribute('href', '/statistics')
  })

  it('shows fractional BTC exposure instead of rounding LONG to zero', () => {
    const capital = deriveCapitalRisk({ ...completionOverview, positionMarkets: positions() }, null)
    render(<OverviewKpiStripV2 capital={capital} liquidity={[]} />)
    expect(screen.getByText('LONG 0.04980000 BTC')).toBeInTheDocument()
  })
  it('keeps stale positions and mixed-instrument net quantities unknown', () => {
    for (const markets of [positions('BTC-USDT', '0.0498', true), [...positions(), ...positions('ETH-USDT', '1')]]) {
      expect(deriveCapitalRisk({ ...completionOverview, positionMarkets: markets }, null).netPosition.amount).toBe('—')
    }
  })
  it('distinguishes an observed empty position snapshot from missing observations', () => {
    const markets = positions()
    markets[0].positions = []
    expect(deriveCapitalRisk({ ...completionOverview, positionMarkets: markets }, null).netPosition)
      .toMatchObject({ side: 'FLAT', amount: '0', pnlUsdt: '+0.00', observed: true })
    expect(deriveCapitalRisk({ ...completionOverview, positionMarkets: [] }, null).netPosition)
      .toMatchObject({ amount: '—', pnlUsdt: null, observed: false })
  })
  it('does not call a missing own spread an exceeded target', () => {
    const liquidity = deriveLiquidity(completionOverview, null).map(market => ({ ...market, spreadActualBps: null, spreadTargetBps: 8 }))
    render(<OverviewKpiStripV2 capital={deriveCapitalRisk(completionOverview, null)} liquidity={liquidity} />)
    expect(screen.queryByText('Own spreads wider than planned width')).not.toBeInTheDocument()
    expect(screen.getByText('Spread unavailable — two working sides required')).toBeInTheDocument()
  })
  it('distinguishes a missing spread target from a measured violation', () => {
    const capital = deriveCapitalRisk(completionOverview, null)
    const markets = deriveLiquidity(completionOverview, null)
    const { rerender } = render(<OverviewKpiStripV2 capital={capital} liquidity={markets.map(market => ({ ...market, spreadActualBps: 4, spreadTargetBps: null }))} />)
    expect(screen.getByText('Spread target unavailable')).toBeInTheDocument()
    expect(screen.queryByText('Own spreads wider than planned width')).not.toBeInTheDocument()
    rerender(<OverviewKpiStripV2 capital={capital} liquidity={markets.map(market => ({ ...market, spreadActualBps: 12, spreadTargetBps: 8 }))} />)
    expect(screen.getByText('Own spreads wider than planned width')).toBeInTheDocument()
  })
  it('normalizes signed hedge legs and preserves tiny net exposure', () => {
    const markets = positions('BTC-USDT', '0.0498')
    markets[0].positions.push({ ...markets[0].positions[0], side: 'SHORT', amount: '-0.0497' })
    const net = deriveCapitalRisk({ ...completionOverview, positionMarkets: markets }, null).netPosition
    expect(net).toMatchObject({ side: 'LONG', amount: '0.00010000 BTC', observed: true })
    expect(deriveCapitalRisk({ ...completionOverview, positionMarkets: positions('BTC-USDT', '1e-8') }, null).netPosition)
      .toMatchObject({ side: 'LONG', amount: '0.00000001 BTC', observed: true })
  })

})


it('renders account equity as USD with an explicit source, never as USDT', () => {
  const capital = { ...deriveCapitalRisk(completionOverview, null), accountEquity: { available: true as const, observedAt: '2026-11-18T00:00:00Z', stale: false, value: { total_usd: '12345.67', currency: 'USD' as const, source: 'okx_account_balance_total_eq' as const } } }
  render(<OverviewKpiStripV2 capital={capital} liquidity={[]} />)
  expect(screen.getByText('12,345.67 USD')).toBeInTheDocument()
  expect(screen.getByText('OKX account equity · includes position PnL')).toBeInTheDocument()
  expect(screen.queryByText('12,345.67 USDT')).not.toBeInTheDocument()
})

it('resolves venue label dynamically from connector or source for non-OKX venues', () => {
  const capital = { ...deriveCapitalRisk(completionOverview, null), accountEquity: { available: true as const, observedAt: '2026-11-18T00:00:00Z', stale: false, value: { total_usd: '50000.00', currency: 'USD' as const, source: 'binance_account_balance_total_eq' as const } } }
  render(<OverviewKpiStripV2 capital={capital} liquidity={[]} />)
  expect(screen.getByText('BINANCE account equity · includes position PnL')).toBeInTheDocument()
})

it('uses the BE two-sided spread plan and does not round before comparison', () => {
  const unknown = { value: null, observed_at: null, stale: false, unavailable_reason: 'not_observed' }
  const market: OverviewSupplementSchemas['OverviewMarketDataOutput'] = { market: 'spot', pair: 'LIFE-USDT', connector: 'okx', depth: unknown, coverage: unknown, slippage_estimate: unknown, post_fill_recovery: unknown, spread_target: { observed_at: '2026-11-18T00:00:00Z', stale: false, unavailable_reason: null, value: { target_bps: '16.01281', buy_distance_bps: '8', sell_distance_bps: '8.01281', basis: 'two_sided_primary_plan', source: 'controller_spread_policy' } } }
  const data: OverviewSupplementSchemas['OverviewSupplementOutput'] = { contract_version: 1, server_time: '2026-11-18T00:00:00Z', session_id: null, session_started_at: null, account_scope: { account_ids: [], environment: null, site: null, identity_verified: false }, markets: [market], session: { capital_allocated: unknown, delta: unknown, fees: unknown, benchmark_hold: unknown }, portfolio: unknown, risk_rules: unknown, balances: unknown, inventory_limits: unknown, references: unknown }
  const liquidity = deriveLiquidity({ ...completionOverview, markets: completionOverview.markets.map(m => ({ ...m, ownSpreadBps: '16.014' })) }, data)
  expect(liquidity[0]).toMatchObject({ spreadTargetBps: 16.01281, spreadActualBps: 16.014 })
  const { rerender } = render(<OverviewKpiStripV2 capital={deriveCapitalRisk(completionOverview, null)} liquidity={liquidity} />)
  expect(screen.getByText('16.01', { exact: true })).toBeInTheDocument()
  expect(screen.getByText('/ 16.01 bps')).toBeInTheDocument()
  expect(screen.getByText('Own spreads wider than planned width')).toBeInTheDocument()
  market.spread_target!.stale = true
  expect(deriveLiquidity(completionOverview, data)[0].spreadTargetBps).toBeNull()
  rerender(<OverviewKpiStripV2 capital={deriveCapitalRisk(completionOverview, null)} liquidity={liquidity.map(m => ({ ...m, fullWindow: false, rollingCoverageAvailable: true, why: { ...m.why, elapsedSeconds: 845 } }))} />)
  expect(screen.getByText('Spot: collecting 14 / 30 min')).toBeInTheDocument()
})

it('names the current Perp BUY restriction beside the guard and unqualified coverage', () => {
  const unknown = { value: null, observed_at: null, stale: false, unavailable_reason: 'not_observed' }
  const data: OverviewSupplementSchemas['OverviewSupplementOutput'] = {
    contract_version: 1, server_time: '2026-11-11T00:00:00Z', session_id: completionStatus.session!.id,
    session_started_at: null, account_scope: { account_ids: [], environment: 'demo', site: null, identity_verified: true },
    markets: [], session: { capital_allocated: unknown, delta: unknown, fees: unknown, benchmark_hold: unknown },
    portfolio: unknown, balances: unknown, inventory_limits: unknown, references: unknown,
    risk_rules: { observed_at: completionStatus.server_time, stale: false, unavailable_reason: null, value: [{
      controller_id: 'perp', market: 'perpetual', rule_id: 'inventory_long', unit: 'BTC', lower_limit: null,
      upper_limit: '0.078742', current: '0.08050', status: 'violated', unavailable_reason: null,
    }] },
  }
  const activity = completionOverview.markets[0].activity!
  const status: typeof completionStatus = { ...completionStatus, running: true, phase: 'running', quote_activity: { perpetual: {
    ...activity, sides: { ...activity.sides, BUY: {
      ...activity.sides.BUY!, state: 'blocked', order_count: 0, reason_codes: ['inventory_limit'],
    } },
  } } }
  const model = { ...completionOverview, status: { ...completionOverview.status, data: status },
    task: { ...completionOverview.task, runtime: { ...completionOverview.task.runtime, running: true, phase: 'running' } } }
  const liquidity = deriveLiquidity(model, data).map(row => ({ ...row, market: 'perpetual', coverageFraction: null,
    coverageTargetFraction: 0.99, rollingCoverageAvailable: true, measurementReason: 'no_eligible_time' }))
  const components = () => {
    const capital = deriveCapitalRisk(model, data)
    return <><MandateBar view={deriveMandate(model, data)} guards={capital.guardsSummary} /><OverviewKpiStripV2 capital={capital} liquidity={liquidity} /></>
  }
  const { rerender } = render(components())
  expect(screen.getByText('Guards: Perp BUY paused · long inventory above limit (1/1)')).toBeInTheDocument()
  expect(screen.getByText('BUY paused by long inventory limit')).toBeInTheDocument()
  data.risk_rules.value!.push({ ...data.risk_rules.value![0], rule_id: 'drawdown_global', market: null })
  rerender(components())
  expect(screen.getByText('Guards: Intervention needed (2/2)')).toBeInTheDocument()
  data.risk_rules.stale = true
  rerender(components())
  expect(screen.queryByText('BUY paused by long inventory limit')).not.toBeInTheDocument()
  expect(screen.getByText('No eligible time in this window')).toBeInTheDocument()
})
