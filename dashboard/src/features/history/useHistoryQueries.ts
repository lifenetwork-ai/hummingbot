import { useAdminInfiniteQuery, type CursorInput } from '../../core/api/infinite'
import { useAdminQuery } from '../../core/api/hooks'
import { useAuth } from '../../core/auth/auth-context'
import type { ReadInput } from '../../core/api/admin'

/** Cursor pages are scoped by the full filter set; changing filters selects a new cursor chain. */
export function useOrders(input: CursorInput<'listOrders'>, enabled = true) {
  return useAdminInfiniteQuery('listOrders', input, { enabled })
}
export function useFills(input: CursorInput<'listFills'>, enabled = true) {
  return useAdminInfiniteQuery('listFills', input, { enabled })
}
export function useSessions(input: CursorInput<'listSessions'> = {}, enabled = true) {
  return useAdminInfiniteQuery('listSessions', input, { enabled })
}
export function useSessionEvents(sessionId: string | undefined, filters: Omit<NonNullable<ReadInput<'getSessionEvents'>['query']>, 'cursor'> = {}, options: { enabled?: boolean } = {}) {
  return useAdminInfiniteQuery('getSessionEvents', sessionId ? { path: { session_id: sessionId }, query: filters } : { path: { session_id: '' }, query: filters }, { enabled: Boolean(sessionId) && options.enabled !== false })
}
export function useCommands(input: CursorInput<'listCommands'> = {}, enabled = true) {
  return useAdminInfiniteQuery('listCommands', input, { enabled })
}
export function useAudit(input: CursorInput<'getAdminAudit'> = {}, enabled = true) {
  const { identity } = useAuth()
  const allowed = identity?.permissions.read === true
  return { ...useAdminInfiniteQuery('getAdminAudit', input, { enabled: enabled && allowed }),
    access: { allowed, reasonCode: allowed ? null : 'permission_required' as const,
      detail: allowed ? null : 'This action requires the read permission.' } }
}

/** Expensive details are fetched only after the operator selects a row. */
export function useOrderDetail(orderId: string | undefined, timelineLimit?: number) {
  return useAdminQuery('getOrder', orderId ? { path: { order_id: orderId }, ...(timelineLimit === undefined ? {} : { query: { timeline_limit: timelineLimit } }) } : undefined, { enabled: Boolean(orderId), poll: false })
}
export function useSessionConfiguration(sessionId: string | undefined) {
  return useAdminQuery('getSessionConfiguration', sessionId ? { path: { session_id: sessionId } } : undefined, { enabled: Boolean(sessionId), poll: false })
}
