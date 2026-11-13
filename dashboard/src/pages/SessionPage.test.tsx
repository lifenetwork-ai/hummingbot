import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import { setupPageApi, pageApiUrl, pageWrapper } from '../test/operationalTest'
import { contextStatus } from '../test/mocks/contextFixtures'
import { commandReceipt } from '../test/mocks/receiptFixtures'
import { notify } from '../core/feedback/notify'
import { server } from '../test/mocks/server'
import { SessionPage } from './SessionPage'
vi.mock('@tanstack/react-router', () => ({ Link: ({ children, to }: { children: React.ReactNode; to: string }) => <a href={to}>{children}</a> }))
setupPageApi()
function stopped() {
  server.use(http.get(`${pageApiUrl}/status`, () => HttpResponse.json({ ...contextStatus, control_version: 5, desired_state: 'stopped', session: null,
    actions: { start: { allowed: true, detail: 'Ready', reason_code: null }, switch: { allowed: false, detail: 'No session', reason_code: 'no_session' }, stop: { allowed: false, detail: 'No session', reason_code: 'no_session' } } })),
    http.get(`${pageApiUrl}/commands/:id`, ({ params }) => HttpResponse.json({ ...commandReceipt, id: params.id, action: 'start', status: 'succeeded' })))
}
async function review() {
  const button = await screen.findByRole('button', { name: /Continue: Review Configuration Diff/ })
  await waitFor(() => expect(button).toBeEnabled())
  fireEvent.click(screen.getByRole('button', { name: /Until manually stopped/ }))
  fireEvent.click(button)
  await screen.findByRole('heading', { name: /Configuration Diff Review/ })
  fireEvent.click(screen.getByRole('button', { name: /Confirm & Apply New Configuration/ }))
}
describe('SessionPage integrated controller', () => {
  it('reviews Start against the catalogue and polls the exact receipt without replay', async () => {
    stopped()
    const success = vi.spyOn(notify, 'success')
    const info = vi.spyOn(notify, 'info')
    const calls: unknown[] = []
    server.use(http.post(`${pageApiUrl}/commands`, async ({ request }) => { calls.push(await request.json()); return HttpResponse.json({ ...commandReceipt, id: 'started-1', action: 'start', status: 'accepted' }, { status: 202 }) }))
    render(<SessionPage />, { wrapper: pageWrapper() })
    await review()
    await screen.findByText('Command successfully applied!')
    expect(success).not.toHaveBeenCalled()
    expect(info).toHaveBeenCalledWith('Configuration receipt received — verification not yet complete', expect.objectContaining({ id: 'session-submit' }))
    success.mockRestore(); info.mockRestore()
    expect(calls).toEqual([{ action: 'start', expected_version: 5, expected_catalogue_hash: 'catalogue-reviewed', market_mode: 'spot', selection: 'fixed', profile_id: 'follow_market', duration_mode: 'until_stopped', duration_seconds: null }])
    expect(screen.getAllByText('started-1').length).toBeGreaterThan(0)
  })
  it('restores timeout state after remount and explicitly retries the identical request', async () => {
    stopped()
    const calls: { body: unknown; key: string | null }[] = []
    server.use(http.post(`${pageApiUrl}/commands`, async ({ request }) => {
      calls.push({ body: await request.json(), key: request.headers.get('Idempotency-Key') })
      return calls.length === 1 ? HttpResponse.error() : HttpResponse.json({ ...commandReceipt, id: 'retried-1', action: 'start', status: 'accepted' }, { status: 202 })
    }))
    const first = render(<SessionPage />, { wrapper: pageWrapper() })
    await review()
    await screen.findByRole('button', { name: 'Retry with same body/key' })
    first.unmount()
    render(<SessionPage />, { wrapper: pageWrapper() })
    const retry = await screen.findByRole('button', { name: 'Retry with same body/key' })
    expect(calls).toHaveLength(1)
    fireEvent.click(retry)
    await waitFor(() => expect(calls).toHaveLength(2))
    expect(calls[1]).toEqual(calls[0])
  })
  it('does not infer write permission from the route or role', async () => {
    stopped()
    render(<SessionPage />, { wrapper: pageWrapper({ read: true }) })
    const reviewButton = await screen.findByRole('button', { name: /Continue: Review Configuration Diff/ })
    expect(reviewButton).toBeDisabled()
  })
  it('binds backend field validation and request ID to the editable form while preserving the draft for a new review', async () => {
    stopped()
    const calls: { body: unknown; key: string | null }[] = []
    server.use(http.post(`${pageApiUrl}/commands`, async ({ request }) => {
      calls.push({ body: await request.json(), key: request.headers.get('Idempotency-Key') })
      return calls.length === 1 ? HttpResponse.json({ detail: 'Duration rejected', request_id: 'request-inline-1', retry: 'fix_request', fields: [{ path: 'duration_seconds', code: 'duration_out_of_bounds' }] }, { status: 422 })
        : HttpResponse.json({ ...commandReceipt, id: 'command-corrected', action: 'start', status: 'accepted' }, { status: 202 })
    }))
    render(<SessionPage />, { wrapper: pageWrapper() })
    const reviewButton = await screen.findByRole('button', { name: /Continue: Review Configuration Diff/ })
    await waitFor(() => expect(reviewButton).toBeEnabled())
    fireEvent.click(screen.getByRole('checkbox', { name: /Use default duration from YAML/ }))
    fireEvent.change(screen.getByLabelText('Duration (seconds)'), { target: { value: '999' } })
    fireEvent.click(reviewButton)
    fireEvent.click(await screen.findByRole('button', { name: /Confirm & Apply New Configuration/ }))
    await screen.findByText('Duration rejected')
    fireEvent.click(screen.getByRole('button', { name: /Back to form/ }))
    expect(await screen.findByText('duration_out_of_bounds')).toBeInTheDocument()
    expect(screen.getByLabelText('Duration (seconds)')).toHaveValue(999)
    fireEvent.click(screen.getByRole('button', { name: 'Show diagnostics details' }))
    expect(await screen.findByTitle('request-inline-1')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Duration (seconds)'), { target: { value: '600' } })
    fireEvent.click(screen.getByRole('button', { name: /Continue: Review Configuration Diff/ }))
    expect(calls).toHaveLength(1)
    fireEvent.click(await screen.findByRole('button', { name: /Confirm & Apply New Configuration/ }))
    await waitFor(() => expect(calls).toHaveLength(2))
    expect(calls[1].key).not.toBe(calls[0].key)
    expect(calls[1].body).toMatchObject({ duration_mode: 'timed', duration_seconds: 600 })
  })

  it('prevents a command retry POST during the backend wait window and keeps the same key afterwards', async () => {
    stopped()
    const calls: string[] = []
    server.use(http.post(`${pageApiUrl}/commands`, ({ request }) => {
      calls.push(request.headers.get('Idempotency-Key') ?? '')
      return calls.length === 1 ? HttpResponse.json({ detail: 'Wait before retrying', retry: 'wait', retry_after_seconds: 60 }, { status: 503 }) : HttpResponse.json({ ...commandReceipt, id: 'command-after-wait', action: 'start', status: 'accepted' }, { status: 202 })
    }))
    render(<SessionPage />, { wrapper: pageWrapper() })
    await review()
    const retry = await screen.findByRole('button', { name: 'Retry with same body/key' })
    fireEvent.click(retry)
    await screen.findByText('operation_cooldown')
    expect(calls).toHaveLength(1)
    const clock = vi.spyOn(Date, 'now').mockReturnValue(Date.now() + 65000)
    try {
      fireEvent.click(retry)
      await waitFor(() => expect(calls).toHaveLength(2))
      expect(calls[1]).toBe(calls[0])
    } finally { clock.mockRestore() }
  })

})
