import { transferableAbortController } from 'node:util'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterAll, describe, expect, it, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import { ApiProvider } from '../core/api/ApiProvider'
import { completionStatus } from '../test/mocks/completionFixtures'
import { commandReceipt } from '../test/mocks/receiptFixtures'
import { rememberAcceptedReceipt } from '../core/api/receipt-tracking'
import { OperationalProvider } from '../features/operational-context/OperationalProvider'
import { contextPrincipal, contextStatus } from '../test/mocks/contextFixtures'
import { pendingCommand, staleBalances } from '../test/mocks/adminFixtures'
import { server } from '../test/mocks/server'
import { OperationalShell } from './OperationalShell'

vi.mock('@tanstack/react-router', () => ({ Outlet: () => <div>Page content</div> }))
vi.mock('../core/auth/auth-context', () => ({ useAuth: () => ({ identity: contextPrincipal }) }))
const controller = transferableAbortController()
vi.stubGlobal('AbortController', controller.constructor)
vi.stubGlobal('AbortSignal', controller.signal.constructor)
afterAll(() => vi.unstubAllGlobals())
const baseUrl = 'https://receipt-shell.test/api/v1/admin'

describe('OperationalShell receipt lifecycle', () => {
  it.each(['refreshing', 'error'] as const)('keeps a completed receipt open when completion makes status %s', async (scenario) => {
    let statusReads = 0
    let releaseStatus: (() => void) | undefined
    let posts = 0
    server.use(
      http.get(`${baseUrl}/status`, async () => {
        statusReads++
        if (statusReads === 2) {
          if (scenario === 'error') return HttpResponse.json({ detail: 'Status unavailable' }, { status: 500 })
          await new Promise<void>((resolve) => { releaseStatus = resolve })
        }
        return HttpResponse.json(contextStatus)
      }),
      http.get(`${baseUrl}/balances`, () => HttpResponse.json(staleBalances)),
      http.get(`${baseUrl}/positions`, () => HttpResponse.json({ markets: [] })),
      http.get(`${baseUrl}/commands/:id`, ({ params }) => HttpResponse.json({ ...pendingCommand, id: params.id, status: 'succeeded' })),
      http.post(`${baseUrl}/*`, () => { posts++; return HttpResponse.json({}) }),
    )
    rememberAcceptedReceipt({ apiBaseUrl: baseUrl, principalId: contextPrincipal.id, botId: contextStatus.bot_id },
      { kind: 'command', id: 'cmd-done' })
    render(<ApiProvider session={{ baseUrl, principalId: contextPrincipal.id, cacheIdentity: 'shell:1' }}>
      <OperationalProvider><OperationalShell /></OperationalProvider>
    </ApiProvider>)
    fireEvent.click(await screen.findByRole('button', { name: /Details/ }))
    fireEvent.click(await screen.findByTitle('command: cmd-done'))
    try {
      await waitFor(() => expect(statusReads).toBe(2))
      if (scenario === 'error') await screen.findByText(/Cannot verify bot information from backend/)
      // Both hook and shell must preserve the read-only reference during invalidation.
      expect(screen.getByLabelText('Tracked operation details')).toBeInTheDocument()
      expect(screen.getByText('Receipt progress: succeeded')).toBeInTheDocument()
      expect(posts).toBe(0)
    } finally { releaseStatus?.() }
  })

  it('clears receipt selection and old data when the authenticated principal changes', async () => {
    let receiptReads = 0
    server.use(
      http.get(`${baseUrl}/status`, () => HttpResponse.json(contextStatus)),
      http.get(`${baseUrl}/balances`, () => HttpResponse.json(staleBalances)),
      http.get(`${baseUrl}/positions`, () => HttpResponse.json({ markets: [] })),
      http.get(`${baseUrl}/commands/:id`, ({ params }) => {
        receiptReads++
        return HttpResponse.json({ ...pendingCommand, id: params.id, status: 'accepted' })
      }),
    )
    rememberAcceptedReceipt({ apiBaseUrl: baseUrl, principalId: contextPrincipal.id, botId: contextStatus.bot_id },
      { kind: 'command', id: 'cmd-private' })
    const { rerender } = render(<ApiProvider session={{ baseUrl, principalId: contextPrincipal.id, cacheIdentity: 'shell:user-one' }}>
      <OperationalProvider><OperationalShell /></OperationalProvider>
    </ApiProvider>)
    fireEvent.click(await screen.findByRole('button', { name: /Details/ }))
    fireEvent.click(await screen.findByTitle('command: cmd-private'))
    await screen.findByText('Receipt progress: accepted')
    expect(receiptReads).toBe(1)
    rerender(<ApiProvider session={{ baseUrl, principalId: 'google:user-two', cacheIdentity: 'shell:user-two' }}>
      <OperationalProvider><OperationalShell /></OperationalProvider>
    </ApiProvider>)
    await screen.findByText('bot-a')
    expect(screen.queryByLabelText('Tracked operation details')).not.toBeInTheDocument()
    expect(screen.queryByTitle('command: cmd-private')).not.toBeInTheDocument()
    expect(receiptReads).toBe(1)
  })
  it('binds global Stop to a refreshed target/version while an independent position read fails', async () => {
    let reads = 0
    const bodies: unknown[] = []
    server.use(http.get(`${baseUrl}/status`, () => {
      reads++
      return HttpResponse.json({ ...contextStatus, control_version: reads === 1 ? 2 : 3,
        session: { ...completionStatus.session, id: reads === 1 ? 'previous-session' : 'current-session' } })
    }), http.get(`${baseUrl}/balances`, () => HttpResponse.json(staleBalances)),
      http.get(`${baseUrl}/positions`, () => HttpResponse.json({ detail: 'Positions unavailable' }, { status: 422 })),
      http.post(`${baseUrl}/commands`, async ({ request }) => { bodies.push(await request.json()); return HttpResponse.json({ ...commandReceipt, id: 'global-stop', status: 'accepted', session_id: 'current-session' }, { status: 202 }) }),
      http.get(`${baseUrl}/commands/:id`, ({ params }) => HttpResponse.json({ ...commandReceipt, id: params.id, status: 'succeeded', session_id: 'current-session' })))
    render(<ApiProvider session={{ baseUrl, principalId: contextPrincipal.id, cacheIdentity: 'shell:stop' }}><OperationalProvider><OperationalShell /></OperationalProvider></ApiProvider>)
    await screen.findByText('bot-a')
    act(() => { window.dispatchEvent(new CustomEvent('open-stop-dialog')) })
    const confirm = await screen.findByRole('button', { name: 'Confirm stop bot now' })
    await waitFor(() => expect(confirm).toBeEnabled())
    fireEvent.click(confirm)
    await waitFor(() => expect(bodies).toEqual([{ action: 'stop', expected_version: 3, session_id: 'current-session' }]))
    expect(await screen.findByText(/Stop global-stop: succeeded/)).toBeInTheDocument()
  })

})
