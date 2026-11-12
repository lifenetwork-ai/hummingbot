import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { components } from '../../core/api/schema'
import { SharedBalancesCard } from './SharedBalancesCard'

type Balances = components['schemas']['BalancesResponseOutput']
type Balance = Balances['accounts'][number]['balances'][number]
const makeBalance = (currency: string, values: Partial<Balance> = {}): Balance => ({
  available_cash: '0', available_equity: null, cash_balance: '0', connector: 'okx', currency, equity: '0',
  observed_at: '2026-11-12T00:00:00Z', present_in_latest_snapshot: true, source: 'okx_account_balance', stale: false, ...values,
})
const makeBalances = (overrides: Partial<Balance> = {}): Balances => ({
  accounts: [{
    account_id: 'acc-1', environment: 'demo', identity_verified: true, balances: [
      makeBalance('USDT', { cash_balance: '15000.50', available_cash: '12000.00', equity: '15500.00', ...overrides }),
      makeBalance('BTC', { cash_balance: '0.4500', available_cash: '0.4000', equity: '0.4500' }),
    ],
  }], scope: 'account', owner_available: true, server_time: '2026-11-12T00:00:00Z',
})

function renderCard(balances: Balances | null | undefined = makeBalances(), props: Partial<Parameters<typeof SharedBalancesCard>[0]> = {}) {
  return render(<SharedBalancesCard balances={balances} available={true} {...props} />)
}

describe('SharedBalancesCard', () => {
  it('shows the known USDT cash and available amounts without mixing native asset units', () => {
    renderCard()
    expect(screen.getByText(/Available 12,000\.00 USDT/)).toBeInTheDocument()
    expect(screen.getByText(/Cash unavailable 3,000\.50 USDT/)).toBeInTheDocument()
    expect(screen.queryByText(/Multi-asset equity allocation/)).not.toBeInTheDocument()
    expect(screen.queryByText(/USDT 15,500\.00 USDT/)).not.toBeInTheDocument()
    expect(screen.getByText(/Detailed breakdown \(2 assets\)/)).toBeInTheDocument()
  })

  it('keeps explicit zero distinct from unknown null values', () => {
    const balances = makeBalances({ cash_balance: '0', available_cash: '0', equity: null })
    renderCard(balances)
    expect(screen.getAllByText('0.00 USDT').length).toBeGreaterThanOrEqual(2)
    expect(screen.getByText('—')).toBeInTheDocument()
    expect(screen.queryByText(/Available 0\.00 USDT/)).not.toBeInTheDocument()
  })

  it.each([
    ['stale', { stale: true }],
    ['missing from latest snapshot', { present_in_latest_snapshot: false }],
  ])('marks %s retained observations stale and hides their allocation bar', (_label, overrides) => {
    renderCard(makeBalances(overrides))
    expect(screen.getByText('stale')).toBeInTheDocument()
    expect(screen.queryByText(/Available 12,000\.00 USDT/)).not.toBeInTheDocument()
  })

  it('marks retained values stale and hides allocation when owner is unavailable', () => {
    renderCard(makeBalances(), { available: false })
    expect(screen.getAllByText('stale')).toHaveLength(2)
    expect(screen.queryByText(/Available 12,000\.00 USDT/)).not.toBeInTheDocument()
    expect(screen.getByText('NO DATA')).toBeInTheDocument()
  })

  it('reports a refresh error while retaining the last values as stale', () => {
    renderCard(makeBalances(), { error: new Error('backend unavailable') })
    expect(screen.getByRole('alert')).toHaveTextContent(/Could not refresh balances/)
    expect(screen.getByRole('alert')).toHaveTextContent(/Showing the last received values as stale/)
    expect(screen.getAllByText('stale')).toHaveLength(2)
    expect(screen.queryByText(/Available 12,000\.00 USDT/)).not.toBeInTheDocument()
  })

  it('reports an unavailable API error when there is no retained data', () => {
    renderCard(null, { error: new Error('backend unavailable'), available: false })
    expect(screen.getByRole('alert')).toHaveTextContent(/Could not refresh balances/)
    expect(screen.getByRole('alert')).toHaveTextContent(/No balance observation is available/)
  })

  it('shows loading distinctly and recovers to the current snapshot on rerender', () => {
    const balances = makeBalances()
    const { rerender } = render(<SharedBalancesCard balances={balances} available={false} isLoading />)
    expect(screen.getByText('SYNCING')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    rerender(<SharedBalancesCard balances={balances} available isLoading={false} />)
    expect(screen.getByText('SHARED WALLET')).toBeInTheDocument()
    expect(screen.getByText(/Available 12,000\.00 USDT/)).toBeInTheDocument()
    expect(screen.queryByText('stale')).not.toBeInTheDocument()
  })

  it('renders collapsible multi-asset table with zero-balance filter', () => {
    const balances = makeBalances()
    balances.accounts[0].balances.push(makeBalance('ETH', { cash_balance: '10.5000', available_cash: '5.2500', equity: '10.5000' }))
    balances.accounts[0].balances.push(makeBalance('SOL', { cash_balance: '0.00', available_cash: '0.00', equity: '0.00' }))
    renderCard(balances)
    expect(screen.getByText(/Detailed breakdown \(4 assets\)/)).toBeInTheDocument()
    expect(screen.getAllByText('SOL').length).toBeGreaterThanOrEqual(1)
    fireEvent.click(screen.getByRole('button', { name: 'Hide zero balances' }))
    expect(screen.getByText(/Detailed breakdown \(3 assets\)/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Show all' })).toBeInTheDocument()
  })

  it('renders an empty state when no accounts are returned', () => {
    renderCard({ accounts: [], scope: 'account', owner_available: true, server_time: '' })
    expect(screen.getByText(/No balance data returned from exchange/)).toBeInTheDocument()
  })
})
