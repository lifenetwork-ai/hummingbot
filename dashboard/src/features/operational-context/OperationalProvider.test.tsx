import { transferableAbortController } from 'node:util'
import { act, render, screen, waitFor } from '@testing-library/react'
import { afterAll, describe, expect, it, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider } from '@tanstack/react-router'
import { ApiProvider } from '../../core/api/ApiProvider'
import { server } from '../../test/mocks/server'
import { contextPrincipal, contextStatus } from '../../test/mocks/contextFixtures'
import { staleBalances } from '../../test/mocks/adminFixtures'
import { OperationalProvider } from './OperationalProvider'
import { useOperationalContext } from './useOperationalContext'

vi.mock('../../core/auth/auth-context', () => ({ useAuth: () => ({ identity: contextPrincipal }) }))
const controller = transferableAbortController()
vi.stubGlobal('AbortController', controller.constructor)
vi.stubGlobal('AbortSignal', controller.signal.constructor)
afterAll(() => vi.unstubAllGlobals())
const baseUrl = 'https://workspace.test/api/v1/admin'

function Consumer({ name }: { name: string }) {
  const context = useOperationalContext()
  return <div aria-label={name}>{context.botId ?? context.status.phase}
    <button onClick={() => { void context.refreshStatus() }}>Refresh {name}</button>
  </div>
}

describe('OperationalProvider', () => {
  it('shares a single controller between shell and page consumers and propagates refresh', async () => {
    let statusCalls = 0
    let balanceCalls = 0
    server.use(
      http.get(`${baseUrl}/status`, () => HttpResponse.json({ ...contextStatus, bot_id: `bot-${++statusCalls}` })),
      http.get(`${baseUrl}/balances`, () => { balanceCalls++; return HttpResponse.json(staleBalances) }),
    )
    render(<ApiProvider session={{ baseUrl, cacheIdentity: 'workspace:1' }}>
      <OperationalProvider><Consumer name="shell" /><Consumer name="page" /></OperationalProvider>
    </ApiProvider>)
    await waitFor(() => expect(screen.getByLabelText('page')).toHaveTextContent('bot-1'))
    expect(screen.getByLabelText('shell')).toHaveTextContent('bot-1')
    expect(statusCalls).toBe(1)
    expect(balanceCalls).toBe(1)
    await act(async () => { screen.getByRole('button', { name: 'Refresh shell' }).click() })
    await waitFor(() => expect(screen.getByLabelText('page')).toHaveTextContent('bot-2'))
    expect(screen.getByLabelText('shell')).toHaveTextContent('bot-2')
    expect(balanceCalls).toBe(1)
  })

  it('fails explicitly when a consumer is outside its workspace provider', () => {
    const error = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    try {
      expect(() => render(<Consumer name="outside" />)).toThrow('useOperationalContext must be used within OperationalProvider')
    } finally { error.mockRestore() }
  })

  it('keeps the controller mounted across child route navigation', async () => {
    let statusCalls = 0
    server.use(
      http.get(`${baseUrl}/status`, () => { statusCalls++; return HttpResponse.json(contextStatus) }),
      http.get(`${baseUrl}/balances`, () => HttpResponse.json(staleBalances)),
    )
    const root = createRootRoute({ component: () => <OperationalProvider><Outlet /></OperationalProvider> })
    const first = createRoute({ getParentRoute: () => root, path: '/', component: () => <Consumer name="first-page" /> })
    const second = createRoute({ getParentRoute: () => root, path: '/next', component: () => <Consumer name="next-page" /> })
    const router = createRouter({ routeTree: root.addChildren([first, second]), history: createMemoryHistory({ initialEntries: ['/'] }) })
    render(<ApiProvider session={{ baseUrl, cacheIdentity: 'workspace:navigation' }}><RouterProvider router={router} /></ApiProvider>)
    await waitFor(() => expect(screen.getByLabelText('first-page')).toHaveTextContent('bot-a'))
    act(() => { router.history.push('/next') })
    await waitFor(() => expect(screen.getByLabelText('next-page')).toHaveTextContent('bot-a'))
    expect(screen.queryByLabelText('first-page')).not.toBeInTheDocument()
    expect(statusCalls).toBe(1)
  })
})
