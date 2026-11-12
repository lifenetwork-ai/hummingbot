import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { http, HttpResponse } from 'msw'
import { setupPageApi, pageApiUrl, pageWrapper } from '../../test/operationalTest'
import { staleBalances } from '../../test/mocks/adminFixtures'
import { server } from '../../test/mocks/server'
import { AssetRibbon } from './AssetRibbon'
setupPageApi()
describe('AssetRibbon integration', () => {
  it('does not substitute equity/cash for availability or treat absent positions and policies as safe', async () => {
    server.use(http.get(`${pageApiUrl}/balances`, () => HttpResponse.json({ ...staleBalances, owner_available: true, accounts: [
      { ...staleBalances.accounts[0], account_id: 'account-A', balances: [{ ...staleBalances.accounts[0].balances[0], stale: false, available_cash: null, cash_balance: '500', equity: '999' }] },
      { ...staleBalances.accounts[0], account_id: 'account-B', balances: [{ ...staleBalances.accounts[0].balances[0], stale: false, available_cash: '2.5001' }] },
    ] })), http.get(`${pageApiUrl}/positions`, () => HttpResponse.json({ owner_available: false, markets: [], server_time: '' })))
    render(<AssetRibbon />, { wrapper: pageWrapper() })
    await screen.findByText('account-A')
    await screen.findByText('account-B')
    expect(screen.getByText('2.50 USDT')).toBeInTheDocument()
    expect(screen.queryByText('500.00 USDT')).not.toBeInTheDocument()
    expect(screen.queryByText('999.00 USDT')).not.toBeInTheDocument()
    expect(screen.getByText('Unverified positions')).toBeInTheDocument()
    expect(screen.getByText('Capital guards: Unverified')).toBeInTheDocument()
    expect(screen.queryByText(/Flat \(0/)).not.toBeInTheDocument()
  })
  it('only reports Flat for a fresh observed empty position scope', async () => {
    server.use(http.get(`${pageApiUrl}/positions`, () => HttpResponse.json({ owner_available: true, server_time: '2026-11-11T00:00:00Z', markets: [
      { market: 'perpetual', connector: 'okx_perpetual', coverage: 'observed', source: 'okx_account_positions', stale: false, observed_at: '2026-11-11T00:00:00Z', items: [] },
    ] })))
    render(<AssetRibbon />, { wrapper: pageWrapper() })
    expect(await screen.findByText('Flat within observed scope')).toBeInTheDocument()
  })
})
