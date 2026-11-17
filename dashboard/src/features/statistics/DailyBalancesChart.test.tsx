import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { DailyBalancesChart } from './DailyBalancesChart'
import type { components } from '../../core/api/schema'

type BalanceRow = {
  day: string
  account_id: string
  currency: string
  equity: string | null
  cash_balance?: string | null
  available_equity?: string | null
}

function toBalances(rows: BalanceRow[]): components['schemas']['DailyBalanceOutput'][] {
  return rows.map((row) => ({
    available_cash: null,
    available_equity: row.available_equity ?? null,
    cash_balance: row.cash_balance ?? null,
    connector: 'binance',
    ...row,
  })) as unknown as components['schemas']['DailyBalanceOutput'][]
}

function renderChart(props: Partial<Parameters<typeof DailyBalancesChart>[0]> = {}) {
  return render(<DailyBalancesChart balances={props.balances ?? []} isLoading={props.isLoading} />)
}

const mockBalances = toBalances([
  { day: '2026-11-08', account_id: 'mm-primary', currency: 'USDT', equity: '10000' },
  { day: '2026-11-09', account_id: 'mm-primary', currency: 'USDT', equity: '11000' },
  { day: '2026-11-10', account_id: 'mm-primary', currency: 'USDT', equity: '9500' },
  { day: '2026-11-08', account_id: 'mm-primary', currency: 'BTC', equity: '0.5' },
  { day: '2026-11-09', account_id: 'mm-primary', currency: 'BTC', equity: '0.4' },
  { day: '2026-11-10', account_id: 'mm-primary', currency: 'BTC', equity: '0.55' },
])

describe('DailyBalancesChart', () => {
  it('renders loading state when isLoading is true', () => {
    renderChart({ isLoading: true })
    expect(screen.getByText('Loading balance history...')).toBeInTheDocument()
  })

  it('renders empty state when there is no data', () => {
    renderChart()
    expect(screen.getByText('No historical balance data for the selected date range.')).toBeInTheDocument()
  })

  it('draws one indexed line per asset currency', () => {
    const { container } = renderChart({ balances: mockBalances })
    expect(container.querySelectorAll('polyline')).toHaveLength(2)
  })

  it('shows concrete latest values and base-relative deltas per asset in the legend', () => {
    renderChart({ balances: mockBalances })
    expect(screen.getAllByText('9,500.00 USDT').length).toBeGreaterThan(0)
    // Vs the first recorded day (10000): 9500.
    expect(screen.getAllByText('-5.0%').length).toBeGreaterThan(0)
    expect(screen.getAllByText('0.55000000 BTC').length).toBeGreaterThan(0)
    // Vs the first recorded day (0.5): 0.55.
    expect(screen.getAllByText('+10.0%').length).toBeGreaterThan(0)
  })

  it('inspects a pinned day with absolute numbers and base-relative deltas via the day columns', () => {
    renderChart({ balances: mockBalances })
    const dayColumn = screen.getByRole('button', { name: /Balance detail for 2026-11-09/ })
    fireEvent.click(dayColumn)
    // Banner shows the pinned day plus that day's concrete equity per asset vs base: 10000 -> 11000 and 0.5 -> 0.4.
    expect(screen.getAllByText('2026-11-09').length).toBeGreaterThan(0)
    expect(screen.getAllByText('11,000.00 USDT').length).toBeGreaterThan(0)
    expect(screen.getAllByText('+10.0%').length).toBeGreaterThan(0)
    expect(screen.getAllByText('0.40000000 BTC').length).toBeGreaterThan(0)
    expect(screen.getAllByText('-20.0%').length).toBeGreaterThan(0)
  })

  it('toggles a series off from the legend', () => {
    const { container } = renderChart({ balances: mockBalances })
    expect(container.querySelectorAll('polyline')).toHaveLength(2)
    fireEvent.click(screen.getByRole('button', { name: /^USDT/ }))
    expect(container.querySelectorAll('polyline')).toHaveLength(1)
  })

  it('falls back to an all-hidden notice when every series is toggled off', () => {
    const { container } = renderChart({ balances: mockBalances })
    fireEvent.click(screen.getByRole('button', { name: /^USDT/ }))
    fireEvent.click(screen.getByRole('button', { name: /^BTC/ }))
    expect(container.querySelectorAll('polyline')).toHaveLength(0)
    expect(screen.getByText('All asset series are hidden.')).toBeInTheDocument()
  })

  it('keeps exact server decimals reachable in the expandable detail table', () => {
    renderChart({ balances: mockBalances })
    fireEvent.click(screen.getByText(/Detailed balance table \(6 rows\)/))
    expect(screen.getAllByText('mm-primary').length).toBeGreaterThan(0)
    expect(screen.getAllByText(/10,000\.00 USDT/).length).toBeGreaterThan(0)
  })
})
