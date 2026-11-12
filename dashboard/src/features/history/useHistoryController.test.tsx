import { act, renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { http, HttpResponse } from 'msw'
import { setupPageApi, pageApiUrl, pageWrapper, pageProfiles } from '../../test/operationalTest'
import { completionStatus } from '../../test/mocks/completionFixtures'
import { commandReceipt } from '../../test/mocks/receiptFixtures'
import { server } from '../../test/mocks/server'
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
  it('blocks invalid audit instants before reads and preserves actor/operation filters on a valid request', async () => {
    const calls: URL[] = []
    server.use(http.get(`${pageApiUrl}/audit`, ({ request }) => { calls.push(new URL(request.url)); return HttpResponse.json({ items: [], next_cursor: null }) }))
    const { result } = renderHook(() => useHistoryController({ active: 'audit' }), { wrapper: pageWrapper() })
    await waitFor(() => expect(result.current.audit.isSuccess).toBe(true))
    await act(async () => { result.current.changeAudit({ from_time: '2026-11-11' }) })
    expect(result.current.validation.audit.errors.from_time).toBe('utc_instant_required')
    await act(async () => { await result.current.loadMoreAudit() })
    expect(calls).toHaveLength(1)
    await act(async () => { result.current.changeAudit({ actor_id: 'actor-1', operation_id: 'op-1', from_time: '2026-11-11T00:00:00Z', to_time: '2026-11-12T00:00:00Z' }) })
    await waitFor(() => expect(result.current.audit.isSuccess).toBe(true))
    expect(calls.at(-1)?.searchParams.get('actor_id')).toBe('actor-1')
    expect(calls.at(-1)?.searchParams.get('operation_id')).toBe('op-1')
  })
  it('reads selected historical pinned configuration without inventing an operation type from event payloads', async () => {
    const paths: string[] = []
    server.use(http.get(`${pageApiUrl}/sessions/:id/events`, ({ params }) => { paths.push(`events:${params.id}`); return HttpResponse.json({ items: [], next_cursor: null }) }),
      http.get(`${pageApiUrl}/sessions/:id/configuration`, ({ params }) => {
        paths.push(`config:${params.id}`)
        return HttpResponse.json({ catalogue: pageProfiles.catalogue, catalogue_hash: 'historical-catalogue', config_hash: 'historical-config', session_id: params.id, profile_id: 'follow_market', request: completionStatus.session!.request, source: 'session_created_event', profile_context: 'at_creation', legacy_overrides: [] })
      }))
    const { result } = renderHook(() => useHistoryController({ active: 'events', currentSessionId: 'current-session' }), { wrapper: pageWrapper() })
    await waitFor(() => expect(result.current.pinned.data?.session_id).toBe('current-session'))
    await act(async () => { result.current.selectSession('past-session') })
    await waitFor(() => expect(result.current.pinned.data?.session_id).toBe('past-session'))
    expect(result.current.pinned.data?.catalogue_hash).toBe('historical-catalogue')
    expect(paths).toContain('events:past-session')
    expect(() => result.current.selectSession(' ')).toThrow('invalid_session_id')
  })
  it('keeps reads and inspection disabled without the read permission', async () => {
    const { result } = renderHook(() => useHistoryController({ active: 'commands' }), { wrapper: pageWrapper({}) })
    expect(result.current.commands.fetchStatus).toBe('idle')
    expect(result.current.access.allowed).toBe(false)
    expect(() => result.current.inspectCommand('cmd-1')).toThrow('permission_required')
  })
})
