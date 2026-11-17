import { act, render, renderHook, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import { http, HttpResponse } from 'msw'
import { setupPageApi, pageApiUrl, pageWrapper } from '../../test/operationalTest'
import { contextStatus } from '../../test/mocks/contextFixtures'
import { staleBalances } from '../../test/mocks/adminFixtures'
import { server } from '../../test/mocks/server'
import { AssetRibbon } from './AssetRibbon'
import { useOperationalContext } from './useOperationalContext'
import { useAssetRibbonController } from './useAssetRibbonController'
import { useAdminQuery } from '../../core/api/hooks'
setupPageApi()
const assetMarket = (market: 'spot' | 'perpetual', pair: string, base: string, quote: string) => ({ market, connector: market === 'spot' ? 'okx' : 'okx_perpetual', pair, base_asset: base, quote_asset: quote, ready: true, admitted: true, reason: null, reference_price: '60000' })
beforeEach(() => server.use(
  http.get(`${pageApiUrl}/status`, () => HttpResponse.json({ ...contextStatus, markets: [assetMarket('spot', 'LIFE-USDT', 'LIFE', 'USDT')] })),
  http.get(`${pageApiUrl}/positions`, () => HttpResponse.json({ owner_available: false, markets: [], server_time: '' })),
))
describe('AssetRibbon integration', () => {
  it('uses BTC context from status and deduplicates shared Spot/Perpetual account assets', async () => {
    server.use(http.get(`${pageApiUrl}/status`, () => HttpResponse.json({ ...contextStatus, markets: [assetMarket('spot', 'BTC-USDT', 'BTC', 'USDT'), assetMarket('perpetual', 'BTC-USDT', 'BTC', 'USDT')] })),
      http.get(`${pageApiUrl}/balances`, () => HttpResponse.json({ ...staleBalances, owner_available: true, accounts: [{ ...staleBalances.accounts[0], balances: [
        { ...staleBalances.accounts[0].balances[0], currency: 'BTC', stale: false, available_cash: '0.0498' },
        { ...staleBalances.accounts[0].balances[0], currency: 'USDT', stale: false, available_cash: '100' },
      ] }] })))
    render(<AssetRibbon />, { wrapper: pageWrapper() })
    await screen.findByText('0.04980000 BTC')
    expect(screen.getAllByText('Available BTC:')).toHaveLength(1)
    expect(screen.getAllByText('Available USDT:')).toHaveLength(1)
    expect(screen.queryByText('Available LIFE:')).not.toBeInTheDocument()
  })

  it.each(['missing', 'mismatched'])('does not guess a LIFE asset from %s session context', async (kind) => {
    server.use(http.get(`${pageApiUrl}/status`, () => HttpResponse.json({ ...contextStatus,
      observation: { ...contextStatus.observation!, session_matches: kind !== 'mismatched' },
      markets: kind === 'missing' ? [] : [assetMarket('spot', 'BTC-USDT', 'BTC', 'USDT')],
    })))
    const { result } = renderHook(() => ({ model: useAssetRibbonController(), context: useOperationalContext() }), { wrapper: pageWrapper() })
    await waitFor(() => expect(result.current.context.status.phase).toBe('ready'))
    expect(result.current.model.assetContextAvailable).toBe(false)
    expect(result.current.model.accounts.every(account => account.balances.length === 0)).toBe(true)
  })

  it('switches cached pair context without reusing the previous asset or making duplicate status reads', async () => {
    let base = 'BTC'
    let statusReads = 0
    server.use(http.get(`${pageApiUrl}/status`, () => {
      statusReads++
      return HttpResponse.json({ ...contextStatus, markets: [assetMarket('spot', `${base}-USDT`, base, 'USDT')] })
    }))
    const { result } = renderHook(() => ({ model: useAssetRibbonController(), context: useOperationalContext() }), { wrapper: pageWrapper() })
    await waitFor(() => expect(result.current.model.accounts[0]?.balances.some(balance => balance.currency === 'BTC')).toBe(true))
    expect(statusReads).toBe(1)
    base = 'ETH'
    await act(async () => { await result.current.context.refreshStatus() })
    await waitFor(() => expect(result.current.model.accounts[0]?.balances.some(balance => balance.currency === 'ETH')).toBe(true))
    expect(result.current.model.accounts[0]?.balances.some(balance => balance.currency === 'BTC')).toBe(false)
    expect(statusReads).toBe(2)
  })

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
