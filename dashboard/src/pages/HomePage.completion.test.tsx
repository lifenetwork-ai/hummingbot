import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import { setupPageApi, pageApiUrl, pageWrapper } from '../test/operationalTest'
import { completionStatus, completionOverview } from '../test/mocks/completionFixtures'
import { server } from '../test/mocks/server'
import { HomePage } from './HomePage'
const { navigate } = vi.hoisted(() => ({ navigate: vi.fn() }))
vi.mock('@tanstack/react-router', () => ({ Link: ({ children, to }: { children: React.ReactNode; to: string }) => <a href={to}>{children}</a>, useNavigate: () => navigate }))
setupPageApi()
describe('Overview completed model integration', () => {
  it('shows the actual random cycle, operator guidance and inventory policy without declaring running to mean healthy', async () => {
    server.use(http.get(`${pageApiUrl}/status`, () => HttpResponse.json({ ...completionStatus, observed_session: completionStatus.session })),
      http.get(`${pageApiUrl}/balances`, () => HttpResponse.json(completionOverview.balances.data)),
      http.get(`${pageApiUrl}/positions`, () => HttpResponse.json(completionOverview.positions.data)),
      http.get(`${pageApiUrl}/statistics/liquidity`, () => HttpResponse.json(completionOverview.liquidity.data)))
    render(<HomePage />, { wrapper: pageWrapper() })
    expect(await screen.findByText('#2')).toBeInTheDocument()
    expect(screen.getAllByText('Inventory restriction').length).toBeGreaterThan(0)
    expect(screen.getByTitle(/Rolling SLA is not provided by this API/)).toBeInTheDocument()
    expect(screen.queryByText('Safe')).not.toBeInTheDocument()
    // 30-minute barcode: NO DATA placeholder — per-minute data is not simulated
    expect(screen.getByText('Rolling 30-minute SLA and per-minute history are not provided by this API.')).toBeInTheDocument()
    expect(screen.getAllByText(/Unverified:.*Observed but not eligible:/).length).toBeGreaterThan(0)
    expect(screen.queryByTitle(/Minute -/)).not.toBeInTheDocument()
    expect(screen.queryByText(/account_cash/)).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /view rule details/i }))
    expect(screen.getByRole('dialog', { name: 'Capital Protection' })).toBeInTheDocument()
    expect(screen.getByText(/Rule 1 · Quote Guard — spot \(LIFE-USDT\)/)).toBeInTheDocument()
    expect(screen.getByText('Rule 2 · Drawdown')).toBeInTheDocument()
    expect(screen.getByText('Rule 3 · Spot Outflow')).toBeInTheDocument()
    expect(screen.getByText(/— violations · 0 of 3 rules checked · 3 unverified/)).toBeInTheDocument()
    expect(screen.getByText(/account_cash/)).toBeInTheDocument()
    expect(screen.getAllByText(/0\.000000001/).length).toBeGreaterThan(0)
  })
  it('opens the recovery tab for the backend recovery action rather than leaving the operator on connections', async () => {
    server.use(http.get(`${pageApiUrl}/status`, () => HttpResponse.json({ ...completionStatus, observed_session: completionStatus.session,
      recovery: { halted: true, check_available: true, reset_available: false, pending_mutations: false, next_action: 'check_recovery', detail: 'Review technical recovery', status_url: '/recovery' } })),
      http.get(`${pageApiUrl}/positions`, () => HttpResponse.json(completionOverview.positions.data)),
      http.get(`${pageApiUrl}/statistics/liquidity`, () => HttpResponse.json(completionOverview.liquidity.data)))
    render(<HomePage />, { wrapper: pageWrapper({ read: true, manage_recovery: true }) })
    fireEvent.click(await screen.findByRole('button', { name: 'Check incident recovery (Admin)' }))
    expect(navigate).toHaveBeenCalledWith({ to: '/admin', search: { tab: 'recovery' } })
  })

  it('shows a source API error and retries it without hiding independent coverage', async () => {
    let fail = true
    let positionReads = 0
    let writes = 0
    server.use(
      http.get(`${pageApiUrl}/status`, () => HttpResponse.json(completionStatus)),
      http.get(`${pageApiUrl}/positions`, () => {
        positionReads++
        return fail ? HttpResponse.json({ detail: 'Position lookup failed', retry: 'fix_request' }, { status: 422 })
          : HttpResponse.json({ ...completionOverview.positions.data, owner_available: true, markets: [
            { market: 'spot', connector: 'okx', coverage: 'not_applicable', items: [], observed_at: null, source: null, stale: false },
            { market: 'perpetual', connector: 'okx_perpetual', coverage: 'observed', items: [], observed_at: completionStatus.server_time, source: 'okx_account_positions', stale: false },
          ] })
      }),
      http.get(`${pageApiUrl}/statistics/liquidity`, () => HttpResponse.json(completionOverview.liquidity.data)),
      http.post(`${pageApiUrl}/*`, () => { writes++; return HttpResponse.json({}) }),
    )
    render(<HomePage />, { wrapper: pageWrapper() })
    expect(await screen.findByText('Positions: API error')).toBeInTheDocument()
    expect(screen.getAllByText('SESSION TOTAL').length).toBeGreaterThan(0)
    expect(screen.queryByText('FLAT')).not.toBeInTheDocument()
    fail = false
    fireEvent.click(screen.getByRole('button', { name: 'Retry positions' }))
    await waitFor(() => expect(screen.queryByText('Positions: API error')).not.toBeInTheDocument())
    expect(screen.getAllByText('FLAT').length).toBeGreaterThan(0)
    expect(positionReads).toBe(2)
    expect(writes).toBe(0)
  })

})
