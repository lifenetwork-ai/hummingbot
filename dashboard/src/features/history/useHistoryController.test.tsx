import { act, renderHook, waitFor } from '@testing-library/react'
import type { PropsWithChildren } from 'react'
import { describe, expect, it } from 'vitest'
import { http, HttpResponse } from 'msw'
import { setupPageApi, pageApiUrl, pageWrapper, pageProfiles } from '../../test/operationalTest'
import { completionStatus } from '../../test/mocks/completionFixtures'
import { commandReceipt } from '../../test/mocks/receiptFixtures'
import { server } from '../../test/mocks/server'
import { contextPrincipal } from '../../test/mocks/contextFixtures'
import { useHistoryController } from './useHistoryController'
setupPageApi()
describe('history completion controller', () => {
  it('binds command filters to cursor pages and resets a previously visited filter to its first page', async () => {
    const calls: URL[] = []
    server.use(http.get(`${pageApiUrl}/commands`, ({ request }) => {
      const url = new URL(request.url); calls.push(url)
      return HttpResponse.json({ items: [{ ...commandReceipt, id: url.searchParams.get('status') ?? 'all' }], next_cursor: url.searchParams.has('cursor') ? null : 'opaque-2' })
    }))
    const { result } = renderHook(() => useHistoryController({ active: 'commands' }), { wrapper: pageWrapper() })
    await waitFor(() => expect(result.current.commands.isSuccess).toBe(true))
    await act(async () => { result.current.changeCommands({ status: 'failed', action: 'switch', session_id: 's2', limit: 50 }) })
    await waitFor(() => expect(result.current.commandLinks[0]?.reference.id).toBe('failed'))
    await act(async () => { await result.current.loadMoreCommands() })
    await waitFor(() => expect(result.current.commands.data?.pages).toHaveLength(2))
    expect(calls.at(-1)?.searchParams.get('cursor')).toBe('opaque-2')
    expect(calls.at(-1)?.searchParams.get('session_id')).toBe('s2')
    await act(async () => { result.current.changeCommands({ status: 'superseded' }) })
    await waitFor(() => expect(result.current.commandLinks[0]?.reference.id).toBe('superseded'))
    await act(async () => { result.current.changeCommands({ status: 'failed', action: 'switch', session_id: 's2', limit: 50 }) })
    await waitFor(() => expect(result.current.commands.data?.pages).toHaveLength(1))
    expect(calls.at(-1)?.searchParams.get('cursor')).toBeNull()
    expect(result.current.commandLinks[0]).toMatchObject({ actorId: null, auditFilter: { operation_id: 'failed' } })
  })
  it('tracks the current session for events and pinned configuration', async () => {
    const paths: string[] = []
    server.use(http.get(`${pageApiUrl}/sessions/:id/events`, ({ params }) => { paths.push(`events:${params.id}`); return HttpResponse.json({ items: [], next_cursor: null }) }),
      http.get(`${pageApiUrl}/sessions/:id/configuration`, ({ params }) => {
        paths.push(`config:${params.id}`)
        return HttpResponse.json({ catalogue: pageProfiles.catalogue, catalogue_hash: 'historical-catalogue', config_hash: 'historical-config', session_id: params.id, profile_id: 'follow_market', request: completionStatus.session!.request, source: 'session_created_event', profile_context: 'at_creation', legacy_overrides: [] })
      }))
    const { result } = renderHook(() => useHistoryController({ active: 'events', currentSessionId: 'current-session' }), { wrapper: pageWrapper() })
    await waitFor(() => expect(result.current.pinned.data?.session_id).toBe('current-session'))
    expect(result.current.pinned.data?.catalogue_hash).toBe('historical-catalogue')
    expect(paths).toContain('events:current-session')
  })
  it('keeps reads and inspection disabled without the read permission', async () => {
    const { result } = renderHook(() => useHistoryController({ active: 'commands' }), { wrapper: pageWrapper({}) })
    expect(result.current.commands.fetchStatus).toBe('idle')
    expect(result.current.access.allowed).toBe(false)
    expect(() => result.current.inspectCommand('cmd-1')).toThrow('permission_required')
  })
  it('stops pending command receipt reads and clears inspection when inactive or read permission is revoked', async () => {
    let readPermission = true
    let receiptReads = 0
    server.use(http.get(`${pageApiUrl}/commands/:id`, ({ params }) => {
      receiptReads += 1
      return HttpResponse.json({ ...commandReceipt, id: params.id, status: 'accepted' })
    }))
    function Wrapper({ children }: PropsWithChildren) {
      const Inner = pageWrapper(readPermission ? contextPrincipal.permissions : {})
      return <Inner>{children}</Inner>
    }
    const { result, rerender } = renderHook(({ active }: { active: 'commands' | 'none' }) => useHistoryController({ active }), {
      initialProps: { active: 'commands' }, wrapper: Wrapper,
    })
    await waitFor(() => expect(result.current.commands.isSuccess).toBe(true))
    act(() => result.current.inspectCommand('pending-command'))
    await waitFor(() => expect(receiptReads).toBeGreaterThan(0))
    expect(result.current.selectedReceipt?.id).toBe('pending-command')
    rerender({ active: 'none' })
    await waitFor(() => expect(result.current.selectedReceipt).toBeNull())
    expect(result.current.inspection).toBeNull()
    const afterInactive = receiptReads
    await new Promise((resolve) => setTimeout(resolve, 30))
    expect(receiptReads).toBe(afterInactive)

    rerender({ active: 'commands' })
    expect(result.current.selectedReceipt).toBeNull()
    act(() => result.current.inspectCommand('revoked-command'))
    await waitFor(() => expect(receiptReads).toBeGreaterThan(afterInactive))
    readPermission = false
    rerender({ active: 'commands' })
    await waitFor(() => expect(result.current.selectedReceipt).toBeNull())
    expect(result.current.inspection).toBeNull()
    const afterPermissionLoss = receiptReads
    await new Promise((resolve) => setTimeout(resolve, 30))
    expect(receiptReads).toBe(afterPermissionLoss)
  })
})
