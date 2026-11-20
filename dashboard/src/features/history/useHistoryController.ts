import { useEffect, useState } from 'react'
import { useAuth } from '../../core/auth/auth-context'
import { useQueryClient } from '@tanstack/react-query'
import { useReceiptInspection } from '../../core/api/hooks'
import type { ReceiptReference } from '../../core/api/receipt-tracking'
import { useCommands, useSessionEvents, useSessionConfiguration } from './useHistoryQueries'
import { validateHistoryFilters, DEFAULT_HISTORY_PAGE_SIZE, type AuditFilters, type CommandFilters, type EventFilters } from './filters'

export function useHistoryController(options: { active: 'events' | 'commands' | 'audit' | 'none'; currentSessionId?: string }) {
  const client = useQueryClient()
  const { identity } = useAuth()
  const canRead = identity?.permissions.read === true
  const [filters, setFilters] = useState({ commands: { limit: DEFAULT_HISTORY_PAGE_SIZE } as CommandFilters, events: { limit: DEFAULT_HISTORY_PAGE_SIZE } as EventFilters })
  // Session selection is not exposed in the events filter until the backend offers a
  // server-side session search; the timeline always tracks the current session.
  const sessionId = options.currentSessionId
  const validation = { commands: validateHistoryFilters(filters.commands), events: validateHistoryFilters(filters.events) }
  const events = useSessionEvents(sessionId, filters.events, { enabled: canRead && options.active === 'events' && validation.events.valid })
  const pinned = useSessionConfiguration(canRead && options.active === 'events' ? sessionId : undefined)
  const commands = useCommands({ query: filters.commands }, canRead && options.active === 'commands' && validation.commands.valid)
  const [selectedReceipt, setSelectedReceipt] = useState<ReceiptReference | null>(null)
  const canInspect = canRead && options.active === 'commands'
  const inspection = useReceiptInspection(canInspect ? selectedReceipt : null)
  useEffect(() => {
    if (!canInspect) setSelectedReceipt(null)
  }, [canInspect])

  function changeCommands(next: CommandFilters) {
    if (JSON.stringify(next) !== JSON.stringify(filters.commands)) void client.resetQueries({ queryKey: ['admin', 'listCommands', 'infinite', { query: next }], exact: true })
    setFilters((current) => ({ ...current, commands: { ...next } }))
  }
  function changeEvents(next: EventFilters) {
    if (sessionId && JSON.stringify(next) !== JSON.stringify(filters.events)) void client.resetQueries({ queryKey: ['admin', 'getSessionEvents', 'infinite', { path: { session_id: sessionId }, query: next }], exact: true })
    setFilters((current) => ({ ...current, events: { ...next } }))
  }
  return { filters, validation, sessionId, events, pinned, commands, selectedReceipt, inspection,
    changeCommands, changeEvents,
    inspectCommand: (id: string) => { if (!canRead) throw new Error('permission_required'); if (id.trim()) setSelectedReceipt({ kind: 'command', id }) },
    loadMoreCommands: () => canRead && validation.commands.valid && commands.hasNextPage ? commands.fetchNextPage() : Promise.resolve(null),
    loadMoreEvents: () => canRead && validation.events.valid && events.hasNextPage ? events.fetchNextPage() : Promise.resolve(null),
    closeInspection: () => setSelectedReceipt(null),
    commandLinks: (commands.data?.pages.flatMap((page) => page.items) ?? []).map((command) => ({
      reference: { kind: 'command' as const, id: command.id }, sessionId: command.session_id,
      action: command.action, status: command.status, actorId: null,
      auditFilter: { operation_id: command.id } satisfies AuditFilters,
    })),
    access: { allowed: canRead, reasonCode: canRead ? null : 'permission_required' },
  }
}
export type HistoryControllerModel = ReturnType<typeof useHistoryController>
