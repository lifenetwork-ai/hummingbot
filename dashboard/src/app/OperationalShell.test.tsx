import { transferableAbortController } from 'node:util'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterAll, describe, expect, it, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import { ApiProvider } from '../core/api/ApiProvider'
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
      http.get(`${baseUrl}/commands/:id`, ({ params }) => HttpResponse.json({ ...pendingCommand, id: params.id, status: 'succeeded' })),
      http.post(`${baseUrl}/*`, () => { posts++; return HttpResponse.json({}) }),
    )
    rememberAcceptedReceipt({ apiBaseUrl: baseUrl, principalId: contextPrincipal.id, botId: contextStatus.bot_id },
      { kind: 'command', id: 'cmd-done' })
    render(<ApiProvider session={{ baseUrl, principalId: contextPrincipal.id, cacheIdentity: 'shell:1' }}>
      <OperationalProvider><OperationalShell /></OperationalProvider>
    </ApiProvider>)
    fireEvent.click(await screen.findByTitle('command: cmd-done'))
    try {
      await waitFor(() => expect(statusReads).toBe(2))
      if (scenario === 'error') await screen.findByText(/Không thể xác minh thông tin bot từ backend/)
      // Both hook and shell must preserve the read-only reference during invalidation.
      expect(screen.getByLabelText('Chi tiết thao tác đang theo dõi')).toBeInTheDocument()
      expect(screen.getByText('Tiến trình receipt: succeeded')).toBeInTheDocument()
      expect(posts).toBe(0)
    } finally { releaseStatus?.() }
  })

  it('clears receipt selection and old data when the authenticated principal changes', async () => {
    let receiptReads = 0
    server.use(
      http.get(`${baseUrl}/status`, () => HttpResponse.json(contextStatus)),
      http.get(`${baseUrl}/balances`, () => HttpResponse.json(staleBalances)),
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
    fireEvent.click(await screen.findByTitle('command: cmd-private'))
    await screen.findByText('Tiến trình receipt: accepted')
    expect(receiptReads).toBe(1)
    rerender(<ApiProvider session={{ baseUrl, principalId: 'google:user-two', cacheIdentity: 'shell:user-two' }}>
      <OperationalProvider><OperationalShell /></OperationalProvider>
    </ApiProvider>)
    await screen.findByText('bot-a')
    expect(screen.queryByLabelText('Chi tiết thao tác đang theo dõi')).not.toBeInTheDocument()
    expect(screen.queryByTitle('command: cmd-private')).not.toBeInTheDocument()
    expect(receiptReads).toBe(1)
  })
})
