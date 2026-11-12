import { act, renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { http, HttpResponse } from 'msw'
import { rememberAcceptedReceipt } from '../../core/api/receipt-tracking'
import { setupPageApi, pageApiUrl, pageWrapper } from '../../test/operationalTest'
import { contextPrincipal, contextStatus } from '../../test/mocks/contextFixtures'
import { recoveryResetReceipt } from '../../test/mocks/receiptFixtures'
import { server } from '../../test/mocks/server'
import { useOperationalContext } from './useOperationalContext'
import { useSessionControl } from '../session-control/useSessionControl'
setupPageApi()
describe('recovery fence in the workspace', () => {
  it('keeps new Start/Switch blocked across consumer remounts while preserving Stop availability', async () => {
    let terminal = false
    server.use(http.get(`${pageApiUrl}/status`, () => HttpResponse.json({ ...contextStatus, actions: { start: { allowed: true, reason_code: null, detail: 'Ready' }, switch: { allowed: true, reason_code: null, detail: 'Ready' }, stop: { allowed: true, reason_code: null, detail: 'Stop remains available' } } })),
      http.get(`${pageApiUrl}/recovery/operations/:id`, ({ params }) => HttpResponse.json({ ...recoveryResetReceipt, id: params.id, status: terminal ? 'succeeded' : 'applying' })))
    rememberAcceptedReceipt({ apiBaseUrl: pageApiUrl, principalId: contextPrincipal.id, botId: contextStatus.bot_id }, { kind: 'recovery', id: 'reset-pending' })
    const wrapper = pageWrapper({ read: true, operate: true, manage_recovery: true })
    const first = renderHook(() => ({ context: useOperationalContext(), control: useSessionControl() }), { wrapper })
    await waitFor(() => expect(first.result.current.context.recoveryLock?.active).toBe(true))
    expect(first.result.current.context.actions.start.reasonCode).toBe('recovery_in_progress')
    expect(first.result.current.context.actions.stop.allowed).toBe(true)
    expect(first.result.current.control.review('start', { marketMode: 'spot', selection: 'fixed', profileId: 'follow_market', durationMode: 'timed', durationSeconds: 600 }).errors.action).toBe('recovery_in_progress')
    first.unmount()
    const next = renderHook(useOperationalContext, { wrapper })
    await waitFor(() => expect(next.result.current.recoveryLock?.active).toBe(true))
    terminal = true
    await act(async () => { await next.result.current.refreshRecoveryOperation?.() })
    await waitFor(() => expect(next.result.current.recoveryLock?.active).toBe(false))
    expect(next.result.current.actions.start.allowed).toBe(true)
    expect(JSON.stringify(next.result.current.recoveryLock)).not.toContain('confirmation_token')
  })
})
