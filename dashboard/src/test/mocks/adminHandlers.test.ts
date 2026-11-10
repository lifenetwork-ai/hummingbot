import { describe, expect, it } from 'vitest'
import { server } from './server'
import { ADMIN_MOCK_URL, adminHandlers } from './adminHandlers'
import { pendingCommand, recordedOrders, staleBalances } from './adminFixtures'

describe('admin contract fixtures', () => {
  it('keeps recorded quantities exact and pagination empty distinct from unavailable', async () => {
    server.use(...adminHandlers)
    expect(await (await fetch(`${ADMIN_MOCK_URL}/orders`)).json()).toEqual(recordedOrders)
    expect(await (await fetch(`${ADMIN_MOCK_URL}/orders?cursor=next`)).json())
      .toEqual({ items: [], next_cursor: null })
  })
  it('keeps stale observations, unknown amounts and pending receipts explicit', async () => {
    server.use(...adminHandlers)
    expect(await (await fetch(`${ADMIN_MOCK_URL}/balances`)).json()).toEqual(staleBalances)
    expect(await (await fetch(`${ADMIN_MOCK_URL}/commands/fixture-command`)).json()).toEqual(pendingCommand)
  })
})
