import { describe, it, expect, vi } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

vi.mock('@tanstack/react-router', () => ({
  Link: ({ children, to, onClick, className, 'aria-label': ariaLabel }: { children?: React.ReactNode; to?: string; onClick?: () => void; className?: string; 'aria-label'?: string }) => (
    <a href={to ?? '#'} onClick={onClick} className={className} aria-label={ariaLabel}>
      {children}
    </a>
  ),
}))
import { AccountBalancesPanel } from './AccountBalancesPanel'
import type { ReadResponse } from '../../core/api/admin'

const data: ReadResponse<'getBalances'> = {
  scope: 'account', owner_available: true, server_time: '2026-11-16T08:15:15Z',
  accounts: [{ account_id: 'demo-account', environment: 'demo', identity_verified: true, balances: [
    { currency: 'BTC', cash_balance: '0.570923023152', available_cash: '0', equity: null, available_equity: '0.570476033152', observed_at: '2026-11-16T08:15:14Z', source: 'okx_account_balance', connector: 'okx_perpetual', stale: false, present_in_latest_snapshot: true },
  ] }],
}
const ready = { phase: 'ready' as const, data, error: null, refreshing: false }

describe('AccountBalancesPanel', () => {
  it('shows visual bars by default and allows switching to table view with all four balance columns', async () => {
    render(<AccountBalancesPanel open onClose={vi.fn()} source={ready} onRefresh={vi.fn()} />)
    expect(screen.getByText('Account demo-account')).toBeInTheDocument()
    expect(screen.getByText('demo')).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Visual bars' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('img', { name: 'Cash allocation for BTC' })).toBeInTheDocument()

    // Chuyển sang Table view để kiểm tra bảng số liệu 4 cột
    await userEvent.click(screen.getByRole('tab', { name: 'Table' }))
    const table = screen.getByRole('table')
    expect(within(table).getAllByRole('columnheader').map((cell) => cell.textContent)).toEqual(['Currency', 'Cash balance', 'Available cash', 'Equity', 'Available equity'])
    const row = within(table).getByRole('rowheader', { name: 'BTC' }).closest('tr')!
    expect(within(row).getAllByRole('cell').map((cell) => cell.textContent)).toEqual(['0.57092', '0', '—', '0.57048'])
    expect(within(row).getByText('0.57092')).toBeVisible()
    expect(within(row).getByText('0.57048')).toBeVisible()
    expect(screen.getByRole('list', { name: 'Balance observation details' })).toHaveTextContent('okx_account_balance')
    expect(screen.queryByText('Stale / unverified')).not.toBeInTheDocument()
  })

  it('keeps accounts separate and marks outdated currencies', async () => {
    render(<AccountBalancesPanel open onClose={vi.fn()} source={{ ...ready, data: { ...data, accounts: [...data.accounts, { ...data.accounts[0], account_id: 'live-account', environment: 'live', balances: data.accounts[0].balances.map((b) => ({ ...b, present_in_latest_snapshot: false })) }] } }} onRefresh={vi.fn()} />)
    expect(screen.getByText('Account live-account')).toBeInTheDocument()
    expect(screen.getAllByText('Stale / unverified')).toHaveLength(1)
    await userEvent.click(screen.getByRole('tab', { name: 'Table' }))
    expect(screen.getAllByRole('rowheader', { name: /BTC/ })).toHaveLength(2)
  })

  it('supports refresh failure, retained data and recovery via inline snapshot trigger', async () => {
    const onRefresh = vi.fn()
    const props = { open: true, onClose: vi.fn(), onRefresh }
    const { rerender } = render(<AccountBalancesPanel {...props} source={{ ...ready, phase: 'error', error: new Error('offline') }} />)
    expect(screen.getByRole('alert')).toHaveTextContent('Showing the last snapshot')
    expect(screen.getByRole('img', { name: 'Cash allocation for BTC' })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Refresh' }))
    expect(onRefresh).toHaveBeenCalledOnce()
    rerender(<AccountBalancesPanel {...props} source={{ ...ready, refreshing: true }} />)
    expect(screen.getByRole('button', { name: 'Refreshing…' })).toBeDisabled()
    rerender(<AccountBalancesPanel {...props} source={ready} />)
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('shows loading, empty, unavailable and error without data', () => {
    const props = { open: true, onClose: vi.fn(), onRefresh: vi.fn() }
    const { rerender } = render(<AccountBalancesPanel {...props} source={{ phase: 'loading', error: null, refreshing: true }} />)
    expect(screen.getByRole('status')).toHaveTextContent('Loading balances')
    rerender(<AccountBalancesPanel {...props} source={{ ...ready, data: { ...data, accounts: [], owner_available: false } }} />)
    expect(screen.getByText('No account balances reported.')).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('source unavailable')
    rerender(<AccountBalancesPanel {...props} source={{ phase: 'error', error: new Error('offline'), refreshing: false }} />)
    expect(screen.getByRole('alert')).toHaveTextContent('Try refreshing again')
  })

  it('closes with Escape and restores trigger focus', async () => {
    const close = vi.fn()
    const { rerender } = render(<><button>View balances</button><AccountBalancesPanel open={false} onClose={close} source={ready} onRefresh={vi.fn()} /></>)
    screen.getByRole('button', { name: 'View balances' }).focus()
    rerender(<><button>View balances</button><AccountBalancesPanel open onClose={close} source={ready} onRefresh={vi.fn()} /></>)
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(close).toHaveBeenCalledOnce()
    rerender(<><button>View balances</button><AccountBalancesPanel open={false} onClose={close} source={ready} onRefresh={vi.fn()} /></>)
    expect(screen.getByRole('button', { name: 'View balances' })).toHaveFocus()
  })
})
