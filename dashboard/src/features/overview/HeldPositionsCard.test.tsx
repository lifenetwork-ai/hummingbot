import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { OverviewModel } from './model'
import { HeldPositionsCard } from './HeldPositionsCard'

type Markets = OverviewModel['positionMarkets']
const item = (side: 'LONG' | 'SHORT', amount: string, pair = 'LIFE-USDT', stale = false): Markets[number]['positions'][number] => ({
  side, amount, pair, unrealized_pnl: '-3', entry_price: '10', leverage: '1', liquidation_price: null,
  margin: null, margin_mode: null, observed_at: '2026-11-12T00:00:00Z', stale, observation: stale ? 'stale' : 'fresh',
})
const market = (positions: Markets[number]['positions'], marketName: 'spot' | 'perpetual' = 'perpetual', stale = false): Markets[number] => ({
  market: marketName, connector: 'okx_perpetual', coverage: marketName === 'spot' ? 'not_applicable' : 'observed',
  source: 'okx_account_positions', observed_at: '2026-11-12T00:00:00Z', stale,
  observation: stale ? 'stale' : 'fresh', positions,
})
const card = (markets: Markets, props: Partial<Parameters<typeof HeldPositionsCard>[0]> = {}) => render(
  <HeldPositionsCard available={true} markets={markets} {...props} />,
)

describe('HeldPositionsCard', () => {
  it('renders a signed backend SHORT amount as short exposure', () => {
    card([market([item('SHORT', '-2')])])
    expect(screen.getByText(/SHORT 2/)).toBeInTheDocument()
  })

  it('distinguishes loading and API errors', () => {
    card([], { isLoading: true })
    expect(screen.getByText('SYNCING')).toBeInTheDocument()
    card([], { isLoading: false, error: new Error('positions unavailable') })
    expect(screen.getByText('API ERROR')).toBeInTheDocument()
  })

  it('does not treat retained fresh markets as current when the source API has failed', () => {
    card([market([item('SHORT', '-2')])], { error: new Error('positions unavailable') })
    expect(screen.getByText('API ERROR')).toBeInTheDocument()
    expect(screen.queryByText(/SHORT 2/)).not.toBeInTheDocument()
    expect(screen.queryByText('Unrealized PnL (Total):')).not.toBeInTheDocument()
  })

  it('ignores not-applicable spot coverage and confirms flat from observed empty perpetual coverage', () => {
    card([market([], 'spot'), market([])])
    expect(screen.getByText('FLAT')).toBeInTheDocument()
  })

  it('does not show current exposure or PnL from a stale position item', () => {
    card([market([item('LONG', '2', 'LIFE-USDT', true)])])
    expect(screen.getByText('STALE / NO DATA')).toBeInTheDocument()
    expect(screen.queryByText('Unrealized PnL (Total):')).not.toBeInTheDocument()
    expect(screen.queryByText('LONG 2.0000')).not.toBeInTheDocument()
  })

  it('does not combine positions across different instruments', () => {
    card([market([item('LONG', '1', 'BTC-USDT'), item('SHORT', '-1', 'ETH-USDT')])])
    expect(screen.getByText('Different instruments — net exposure not calculated')).toBeInTheDocument()
    expect(screen.getByText('UNSUPPORTED')).toBeInTheDocument()
  })
})
