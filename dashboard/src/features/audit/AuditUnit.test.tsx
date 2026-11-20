import { act, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { http, HttpResponse } from 'msw'
import { setupPageApi, pageApiUrl, pageWrapper } from '../../test/operationalTest'
import { server } from '../../test/mocks/server'
import { useAuditState } from './useAuditState'
import { AuditUnit } from './AuditUnit'

setupPageApi()

describe('AuditUnit', () => {
  it('loads readable exchange metadata without mounting account management for a viewer', async () => {
    const calls: string[] = []
    server.use(
      http.get(`${pageApiUrl}/audit`, () => { calls.push('audit'); return HttpResponse.json({ items: [], next_cursor: null }) }),
      http.get(`${pageApiUrl}/credentials`, () => { calls.push('credentials'); return HttpResponse.json({ source: 'environment', exchanges: [] }) }),
      http.get(`${pageApiUrl}/access/accounts`, () => { calls.push('accounts'); return HttpResponse.json({ items: [], next_cursor: null }) }),
    )
    function Harness() {
      const state = useAuditState()
      return <AuditUnit active state={state} />
    }
    render(<Harness />, { wrapper: pageWrapper({ read: true }) })
    await waitFor(() => expect(calls).toContain('audit'))
    await waitFor(() => expect(calls).toContain('credentials'))
    expect(calls).not.toContain('accounts')
  })

  it('applies a command lookup once while preserving filters and lets reset replace them', async () => {
    const calls: URL[] = []
    server.use(http.get(`${pageApiUrl}/audit`, ({ request }) => {
      calls.push(new URL(request.url))
      return HttpResponse.json({ items: [], next_cursor: null })
    }))
    let current!: ReturnType<typeof useAuditState>
    function Harness() {
      current = useAuditState()
      return <AuditUnit active state={current} />
    }
    render(<Harness />, { wrapper: pageWrapper() })
    await waitFor(() => expect(calls.length).toBeGreaterThan(0))
    act(() => current.changeFilters({ actor_id: 'actor-kept', limit: 25, exchange: 'okx' }))
    await waitFor(() => expect(calls.some((url) => url.searchParams.get('actor_id') === 'actor-kept')).toBe(true))
    let firstRequestId = 0
    act(() => { firstRequestId = current.requestLookup('operation-1') })
    await waitFor(() => expect(calls.some((url) => url.searchParams.get('operation_id') === 'operation-1' && url.searchParams.get('actor_id') === 'actor-kept' && url.searchParams.get('exchange') === 'okx' && url.searchParams.get('limit') === '25')).toBe(true))
    let secondRequestId = 0
    act(() => { secondRequestId = current.requestLookup('operation-1') })
    await waitFor(() => expect(current.lookupIntent).toBeNull())
    expect(firstRequestId).toBeGreaterThan(0)
    expect(secondRequestId).toBeGreaterThan(firstRequestId)
    act(() => current.changeFilters({ limit: 10 }))
    await waitFor(() => expect(current.filters).toEqual({ limit: 10 }))
  })

  it('does not read audit or supporting filters until active and stops new reads when inactive', async () => {
    const calls: string[] = []
    server.use(
      http.get(`${pageApiUrl}/audit`, () => { calls.push('audit'); return HttpResponse.json({ items: [], next_cursor: null }) }),
      http.get(`${pageApiUrl}/credentials`, () => { calls.push('credentials'); return HttpResponse.json({ source: 'postgres_kms', exchanges: [] }) }),
    )
    function Harness({ active }: { active: boolean }) {
      const state = useAuditState()
      return <AuditUnit active={active} state={state} />
    }
    const view = render(<Harness active={false} />, { wrapper: pageWrapper() })
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(calls).toEqual([])
    view.rerender(<Harness active />)
    await waitFor(() => expect(calls).toContain('audit'))
    view.rerender(<Harness active={false} />)
    const afterDeactivate = calls.length
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(calls.length).toBe(afterDeactivate)
  })

  it('resets a cached filter cursor chain when returning to that filter', async () => {
    const calls: URL[] = []
    server.use(http.get(`${pageApiUrl}/audit`, ({ request }) => {
      const url = new URL(request.url)
      calls.push(url)
      const actor = url.searchParams.get('actor_id')
      return HttpResponse.json({ items: [], next_cursor: actor === 'actor-a' && !url.searchParams.has('cursor') ? 'a2' : null })
    }))
    let state!: ReturnType<typeof useAuditState>
    function Harness() {
      state = useAuditState()
      return <AuditUnit active state={state} />
    }
    render(<Harness />, { wrapper: pageWrapper() })
    act(() => state.changeFilters({ actor_id: 'actor-a', limit: 10 }))
    await waitFor(() => expect(calls.some((url) => url.searchParams.get('actor_id') === 'actor-a')).toBe(true))
    act(() => state.requestLookup('op-a'))
    await waitFor(() => expect(state.filters.operation_id).toBe('op-a'))
    // The returned lookup intent preserves the active filters; paginate this cursor chain.
    const loadMore = await screen.findByRole('button', { name: 'Load more logs' })
    act(() => loadMore.click())
    await waitFor(() => expect(calls.some((url) => url.searchParams.get('operation_id') === 'op-a' && url.searchParams.get('cursor') === 'a2')).toBe(true))
    act(() => state.requestLookup('op-b'))
    await waitFor(() => expect(state.filters.operation_id).toBe('op-b'))
    await waitFor(() => expect(calls.some((url) => url.searchParams.get('operation_id') === 'op-b')).toBe(true))
    act(() => state.requestLookup('op-a'))
    await waitFor(() => expect(state.filters.operation_id).toBe('op-a'))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Load more logs' })).toBeInTheDocument())
    expect(calls.at(-1)?.searchParams.get('cursor')).toBeNull()
  })
})
