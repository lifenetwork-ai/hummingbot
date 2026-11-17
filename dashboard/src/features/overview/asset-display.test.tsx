import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { completionOverview } from '../../test/mocks/completionFixtures'
import type { OverviewModel } from './model'
import { deriveCapitalRisk, deriveLiquidity } from './supplement'
import { OverviewKpiStripV2 } from './OverviewKpiStripV2'

function positions(pair = 'BTC-USDT', amount = '0.0498', stale = false): OverviewModel['positionMarkets'] {
  return [{ market: 'perpetual', connector: 'okx_perpetual', coverage: 'observed', source: 'okx_account_positions', observed_at: '2026-11-18T00:00:00Z', stale,
    observation: stale ? 'stale' : 'fresh', positions: [{ pair, side: 'LONG', amount, unrealized_pnl: '-1.2', entry_price: '70000', leverage: '1', margin: null, margin_mode: 'cross', liquidation_price: null, observed_at: '2026-11-18T00:00:00Z', stale, observation: stale ? 'stale' : 'fresh' }] }]
}

describe('asset-aware overview', () => {
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
    expect(screen.queryByText('Some markets exceed spread target')).not.toBeInTheDocument()
    expect(screen.getByText('Spread unavailable — two working sides required')).toBeInTheDocument()
  })
  it('distinguishes a missing spread target from a measured violation', () => {
    const capital = deriveCapitalRisk(completionOverview, null)
    const markets = deriveLiquidity(completionOverview, null)
    const { rerender } = render(<OverviewKpiStripV2 capital={capital} liquidity={markets.map(market => ({ ...market, spreadActualBps: 4, spreadTargetBps: null }))} />)
    expect(screen.getByText('Spread target unavailable')).toBeInTheDocument()
    expect(screen.queryByText('Some markets exceed spread target')).not.toBeInTheDocument()
    rerender(<OverviewKpiStripV2 capital={capital} liquidity={markets.map(market => ({ ...market, spreadActualBps: 12, spreadTargetBps: 8 }))} />)
    expect(screen.getByText('Some markets exceed spread target')).toBeInTheDocument()
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
