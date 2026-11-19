import { describe, it, expect, vi } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { AccountBalancesPanel } from './AccountBalancesPanel'
import type { ReadResponse } from '../../core/api/admin'
import { server } from '../../test/mocks/server'
import { pageApiUrl, pageWrapper, setupPageApi } from '../../test/operationalTest'

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

describe('Balance changes', () => {
  setupPageApi()

  it('loads only on the tab and displays native units, explicit zero and missing cash', async () => {
    const now = '2026-11-16T08:15:14Z'
    const body: ReadResponse<'getBalanceChanges'> = {
      period: 'days', start_day: '2026-11-16', end_day: '2026-11-17', reporting_timezone: 'UTC',
      session_id: null, session_started_at: null, account_id: 'demo-account', environment: 'demo',
      server_time: now, flow_history: 'unverified', reason_code: null,
      rows: [
        { currency: 'BTC', opening_cash: '0.1', closing_cash: '0.07999999', delta_cash: '-0.02000001', opening_observed_at: now, closing_observed_at: now, reason_code: null },
        { currency: 'USDT', opening_cash: '10000', closing_cash: '11300', delta_cash: '1300', opening_observed_at: now, closing_observed_at: now, reason_code: null },
        { currency: 'ETH', opening_cash: null, closing_cash: '0', delta_cash: null, opening_observed_at: null, closing_observed_at: now, reason_code: 'opening_missing' },
      ],
    }
    const requests: string[] = []
    server.use(http.get(`${pageApiUrl}/balances/changes`, ({ request }) => {
      const search = new URL(request.url).searchParams
      requests.push(search.toString())
      return search.get('period') === 'session'
        ? HttpResponse.json({ ...body, period: 'session', rows: [], reason_code: 'opening_missing' })
        : HttpResponse.json(body)
    }))
    render(<AccountBalancesPanel open onClose={vi.fn()} source={ready} onRefresh={vi.fn()} />, { wrapper: pageWrapper() })
    expect(requests).toEqual([])
    expect(screen.queryByText('Net Pos. PnL (Equity - Cash)')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('tab', { name: 'Balance changes' }))
    expect(await screen.findByText('-0.02000001 BTC')).toBeVisible()
    expect(screen.getByText('+1,300 USDT')).toBeVisible()
    expect(screen.getByText('0 ETH')).toBeVisible()
    expect(screen.getByText('Opening balance unavailable')).toBeVisible()
    expect(requests).toEqual(['period=days'])
    fireEvent.change(screen.getByLabelText('From date'), { target: { value: '2026-11-14' } })
    fireEvent.change(screen.getByLabelText('To date (exclusive)'), { target: { value: '2026-11-16' } })
    await userEvent.click(screen.getByRole('button', { name: 'Apply dates' }))
    expect(await screen.findByText('-0.02000001 BTC')).toBeVisible()
    expect(requests[1]).toBe('period=days&from=2026-11-14&to=2026-11-16')
    await userEvent.click(screen.getByRole('button', { name: 'Current session' }))
    expect(await screen.findByText(/Session opening balance unavailable/)).toBeVisible()
    expect(requests[2]).toBe('period=session')
  })

  it('keeps snapshot views usable when the server predates balance changes', async () => {
    server.use(http.get(`${pageApiUrl}/balances/changes`, () => new HttpResponse(null, { status: 404 })))
    render(<AccountBalancesPanel open onClose={vi.fn()} source={ready} onRefresh={vi.fn()} />, { wrapper: pageWrapper() })
    await userEvent.click(screen.getByRole('tab', { name: 'Balance changes' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Balance changes are unavailable on this server')
    await userEvent.click(screen.getByRole('tab', { name: 'Visual bars' }))
    expect(screen.getByRole('img', { name: 'Cash allocation for BTC' })).toBeVisible()
  })

  it('hides a previous account comparison when the account changes', async () => {
    const now = '2026-11-16T08:15:14Z'
    server.use(http.get(`${pageApiUrl}/balances/changes`, () => HttpResponse.json({
      period: 'days', start_day: '2026-11-16', end_day: '2026-11-17', reporting_timezone: 'UTC',
      session_id: null, session_started_at: null, account_id: 'demo-account', environment: 'demo',
      server_time: now, flow_history: 'unverified', reason_code: null,
      rows: [{ currency: 'BTC', opening_cash: '1', closing_cash: '2', delta_cash: '1',
        opening_observed_at: now, closing_observed_at: now, reason_code: null }],
    })))
    const props = { open: true, onClose: vi.fn(), source: ready, onRefresh: vi.fn() }
    const { rerender } = render(<AccountBalancesPanel {...props} changesScope={{ accountId: 'demo-account' }} />, { wrapper: pageWrapper() })
    await userEvent.click(screen.getByRole('tab', { name: 'Balance changes' }))
    expect(await screen.findByText('+1 BTC')).toBeVisible()
    rerender(<AccountBalancesPanel {...props} changesScope={{ accountId: 'new-account' }} />)
    expect(screen.queryByText('+1 BTC')).not.toBeInTheDocument()
    expect(await screen.findByText(/Waiting for a comparison for the current account/)).toBeVisible()
  })
})
