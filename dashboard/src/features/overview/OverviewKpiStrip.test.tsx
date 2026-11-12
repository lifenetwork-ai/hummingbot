import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { completionOverview } from '../../test/mocks/completionFixtures'
import { OverviewKpiStrip } from './OverviewKpiStrip'
import type { OverviewModel } from './model'
function renderKpis(overrides: Partial<OverviewModel> = {}) {
  return render(<OverviewKpiStrip overview={{ ...completionOverview, ...overrides }} />)
}
describe('Overview KPI backend evidence', () => {
  it('does not infer FLAT from an empty unavailable response', () => {
    renderKpis()
    expect(screen.queryByText('FLAT')).not.toBeInTheDocument()
  })
  it('uses each source loading state independently', () => {
    renderKpis({ positions: { phase: 'loading', error: null, refreshing: false } })
    expect(screen.getByText('Loading positions…')).toBeInTheDocument()
  })
  it('names cumulative session coverage without claiming a rolling SLA', () => {
    renderKpis()
    expect(screen.getByText('Two-Sided Coverage (session)')).toBeInTheDocument()
    expect(screen.queryByText('SYNCING')).not.toBeInTheDocument()
  })
  it('shows violations as a fraction of total rules until rules are checked', () => {
    renderKpis()
    expect(screen.getAllByText('—').length).toBeGreaterThanOrEqual(1)
  })
  it('shows an API failure independently of empty data', () => {
    renderKpis({ positions: { phase: 'error', error: new Error('offline'), refreshing: false } })
    expect(screen.getByText('API ERROR')).toBeInTheDocument()
    expect(screen.queryByText('FLAT')).not.toBeInTheDocument()
  })
  it('does not silently pick the first of multiple accounts for USDT', () => {
    const account = { accountId: 'one', identityVerified: true, environment: 'demo', balances: [] }
    renderKpis({ accounts: [account, { ...account, accountId: 'two' }] })
    expect(screen.getByText('PER ACCOUNT')).toBeInTheDocument()
    expect(screen.getByText('Account USDT equity')).toBeInTheDocument()
  })
  it('renders signed SHORT quantities as short exposure and hides stale totals', () => {
    const position = { amount: '-2', side: 'SHORT' as const, pair: 'LIFE-USDT', entry_price: '10', leverage: '1', liquidation_price: null, margin: null, margin_mode: 'cross', observed_at: '2026-11-11T00:00:00Z', stale: false, unrealized_pnl: '-3', observation: 'fresh' as const }
    const market = { connector: 'okx_perpetual', market: 'perpetual' as const, coverage: 'observed' as const, observed_at: '2026-11-11T00:00:00Z', source: 'okx_account_positions' as const, stale: false, observation: 'fresh' as const, items: [position], positions: [position] }
    const overview = { ...completionOverview, positions: { ...completionOverview.positions, data: { owner_available: true, server_time: '2026-11-11T00:00:00Z', markets: [market] } }, positionMarkets: [market] }
    const { rerender } = render(<OverviewKpiStrip overview={overview} />)
    expect(screen.getByText(/SHORT 2/)).toBeInTheDocument()
    expect(screen.getByText(/-3.00/)).toBeInTheDocument()
    rerender(<OverviewKpiStrip overview={{ ...overview, positionMarkets: [{ ...market, observation: 'stale' }] }} />)
    expect(screen.queryByText(/SHORT 2/)).not.toBeInTheDocument()
    expect(screen.queryByText(/-3.00/)).not.toBeInTheDocument()
    expect(screen.getByText('STALE')).toBeInTheDocument()
  })
  it('keeps a known zero equity distinct from missing equity and never uses cash as equity', () => {
    const balance = { currency: 'USDT', equity: '0', cash_balance: '99', available_cash: null, available_equity: null, connector: 'okx', observed_at: '2026-11-11T00:00:00Z', source: 'okx_account_balance' as const, stale: false, present_in_latest_snapshot: true, observation: 'fresh' as const }
    const account = { accountId: 'one', identityVerified: true, environment: 'demo', balances: [balance] }
    const overview = { ...completionOverview, accounts: [account] }
    const { rerender } = render(<OverviewKpiStrip overview={overview} />)
    expect(screen.getByText('0.00 USDT')).toBeInTheDocument()
    rerender(<OverviewKpiStrip overview={{ ...overview, accounts: [{ ...account, balances: [{ ...balance, equity: null }] }] }} />)
    expect(screen.queryByText('0.00 USDT')).not.toBeInTheDocument()
    expect(screen.queryByText(/99.00/)).not.toBeInTheDocument()
  })

})
