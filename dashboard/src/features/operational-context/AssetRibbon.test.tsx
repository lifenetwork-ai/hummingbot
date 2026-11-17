import { render, renderHook, screen, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { http, HttpResponse } from 'msw'
import { setupPageApi, pageApiUrl, pageWrapper } from '../../test/operationalTest'
import { staleBalances } from '../../test/mocks/adminFixtures'
import { server } from '../../test/mocks/server'
import { AssetRibbon } from './AssetRibbon'
import { useAssetRibbonController } from './useAssetRibbonController'
import { useAdminQuery } from '../../core/api/hooks'
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
    // Position observations are presented on Overview, not in the compact balance ribbon.
    expect(screen.queryByText('Flat within observed scope')).not.toBeInTheDocument()
    expect(screen.getByText('Capital guards: Unverified')).toBeInTheDocument()
    expect(screen.queryByText(/Flat \(0/)).not.toBeInTheDocument()
  })
  it('only confirms an empty position scope from a fresh complete owner observation', async () => {
    server.use(http.get(`${pageApiUrl}/positions`, () => HttpResponse.json({ owner_available: true, server_time: '2026-11-11T00:00:00Z', markets: [
      { market: 'perpetual', connector: 'okx_perpetual', coverage: 'observed', source: 'okx_account_positions', stale: false, observed_at: '2026-11-11T00:00:00Z', items: [] },
    ] })))
    const { result } = renderHook(useAssetRibbonController, { wrapper: pageWrapper() })
    await waitFor(() => expect(result.current.positionsObserved).toBe(true))
    expect(result.current.positions).toEqual([])
  })
  it.each([
    { owner: false, coverage: 'observed', stale: false, observedAt: '2026-11-11T00:00:00Z' },
    { owner: true, coverage: 'unavailable', stale: false, observedAt: '2026-11-11T00:00:00Z' },
    { owner: true, coverage: 'observed', stale: true, observedAt: '2026-11-11T00:00:00Z' },
    { owner: true, coverage: 'observed', stale: false, observedAt: null },
  ])('does not confirm empty positions with incomplete evidence: %j', async ({ owner, coverage, stale, observedAt }) => {
    let reads = 0
    server.use(http.get(`${pageApiUrl}/positions`, () => {
      reads++
      return HttpResponse.json({ owner_available: owner, server_time: '2026-11-11T00:00:00Z', markets: [
        { market: 'perpetual', connector: 'okx_perpetual', coverage, source: 'okx_account_positions', stale, observed_at: observedAt, items: [] },
      ] })
    }))
    const { result } = renderHook(() => ({ model: useAssetRibbonController(), source: useAdminQuery('getPositions', {}) }), { wrapper: pageWrapper() })
    await waitFor(() => expect(result.current.source.isSuccess).toBe(true))
    expect(reads).toBeGreaterThan(0)
    expect(result.current.model.positionsObserved).toBe(false)
  })
})
