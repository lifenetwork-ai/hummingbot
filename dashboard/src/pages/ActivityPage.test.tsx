import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { http, HttpResponse } from 'msw'
import { setupPageApi, pageApiUrl, pageWrapper } from '../test/operationalTest'
import { completionStatus } from '../test/mocks/completionFixtures'
import { pageProfiles } from '../test/operationalTest'
import { commandReceipt, reconciliationReceipt } from '../test/mocks/receiptFixtures'
import { server } from '../test/mocks/server'
import { ActivityPage } from './ActivityPage'
setupPageApi()
describe('ActivityPage integrated controller', () => {
  it('follows a specific check and retains mismatch after receipt success', async () => {
    let writes = 0
    server.use(http.post(`${pageApiUrl}/reconciliation/check`, () => { writes++; return HttpResponse.json({ ...reconciliationReceipt, id: 'check-specific', status: 'accepted' }, { status: 202 }) }),
      http.get(`${pageApiUrl}/reconciliation/:id`, ({ params }) => HttpResponse.json({ ...reconciliationReceipt, id: params.id, status: 'succeeded', report: { ...reconciliationReceipt.report, state: 'mismatch' } })))
    render(<ActivityPage />, { wrapper: pageWrapper() })
    const button = await screen.findByRole('button', { name: 'Request Reconciliation' })
    await waitFor(() => expect(button).toBeEnabled())
    fireEvent.click(button)
    await screen.findByText('Discrepancy Detected')
    expect(writes).toBe(1)
    fireEvent.click(screen.getByRole('button', { name: /Session Events/ }))
    expect(await screen.findByRole('heading', { name: /Pinned Configuration/ })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Commands/ }))
    expect(await screen.findByRole('heading', { name: /Command History/ })).toBeInTheDocument()
  })
  it('keeps a Viewer observational and does not expose an enabled check', async () => {
    render(<ActivityPage />, { wrapper: pageWrapper({ read: true }) })
    expect(await screen.findByRole('button', { name: 'Request Reconciliation' })).toBeDisabled()
  })
  it('binds command filters, selected historical configuration, receipt inspection and audit lookup without replay', async () => {
    const reads: URL[] = []
    let writes = 0
    server.use(
      http.get(`${pageApiUrl}/commands`, ({ request }) => { reads.push(new URL(request.url)); return HttpResponse.json({ items: [{ ...commandReceipt, id: 'historical-command', status: 'failed', session_id: 'past-session' }], next_cursor: null }) }),
      http.get(`${pageApiUrl}/commands/:id`, ({ params }) => HttpResponse.json({ ...commandReceipt, id: params.id, status: 'failed', session_id: 'past-session' })),
      http.get(`${pageApiUrl}/audit`, ({ request }) => { reads.push(new URL(request.url)); return HttpResponse.json({ items: [], next_cursor: null }) }),
      http.get(`${pageApiUrl}/sessions`, () => HttpResponse.json({ items: [{ id: 'past-session', profile_id: 'follow_market', status: 'stopped', request: completionStatus.session?.request, config_hash: 'historical-config', configuration_url: '/sessions/past-session/configuration', events_url: '/sessions/past-session/events', created_at: '', last_event_at: '', is_current: false, state_source: 'last_recorded_transition' }], next_cursor: null })),
      http.get(`${pageApiUrl}/sessions/:id/configuration`, ({ params }) => HttpResponse.json({ session_id: params.id, profile_id: 'follow_market', config_hash: 'historical-config', catalogue_hash: 'historical-catalogue', profile_context: 'at_creation', source: 'session_created_event', legacy_overrides: [], request: completionStatus.session?.request, catalogue: pageProfiles.catalogue })),
      http.post(`${pageApiUrl}/*`, () => { writes++; return HttpResponse.json({}, { status: 500 }) }),
    )
    render(<ActivityPage />, { wrapper: pageWrapper() })
    fireEvent.click(screen.getByRole('button', { name: /Commands/ }))
    const status = await screen.findByLabelText('Status')
    fireEvent.change(status, { target: { value: 'failed' } })
    await waitFor(() => expect(reads.some((url) => url.pathname.endsWith('/commands') && url.searchParams.get('status') === 'failed')).toBe(true))
    fireEvent.click(await screen.findByRole('button', { name: 'Inspect' }))
    const dialog = await screen.findByRole('dialog', { name: /Receipt Inspection/ })
    expect(await within(dialog).findByText('failed')).toBeInTheDocument()
    fireEvent.click(within(dialog).getByRole('button', { name: /Audit/ }))
    await waitFor(() => expect(reads.some((url) => url.pathname.endsWith('/audit') && url.searchParams.get('operation_id') === 'historical-command')).toBe(true))
    expect(screen.getByRole('button', { name: 'Audit / Actor Lookup' })).toHaveFocus()
    fireEvent.click(screen.getByRole('button', { name: /Session Events/ }))
    const sessions = await screen.findByLabelText('Select session to track')
    fireEvent.change(sessions, { target: { value: 'past-session' } })
    expect(await screen.findByTitle('historical-config')).toBeInTheDocument()
    expect(screen.getByText(/Profile context: at_creation/)).toBeInTheDocument()
    expect(writes).toBe(0)
  })

})
