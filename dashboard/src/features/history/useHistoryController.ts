import { useState } from 'react'
import { useAuth } from '../../core/auth/auth-context'
import { useQueryClient } from '@tanstack/react-query'
import { useReceiptInspection } from '../../core/api/hooks'
import type { ReceiptReference } from '../../core/api/receipt-tracking'
import { useAudit, useCommands, useSessionEvents, useSessions, useSessionConfiguration } from './useHistoryQueries'
import { validateHistoryFilters, type AuditFilters, type CommandFilters, type EventFilters, type HistoryFilters } from './filters'

export function useHistoryController(options: { active: 'events' | 'commands' | 'audit' | 'none'; currentSessionId?: string }) {
  const client = useQueryClient()
  const { identity } = useAuth()
  const canRead = identity?.permissions.read === true
  const [filters, setFilters] = useState<HistoryFilters>({ commands: {}, events: {}, audit: {} })
  const [selectedSession, setSelectedSession] = useState<string>()
  const sessionId = selectedSession ?? options.currentSessionId
  const validation = { commands: validateHistoryFilters(filters.commands), events: validateHistoryFilters(filters.events), audit: validateHistoryFilters(filters.audit) }
  const sessions = useSessions({}, canRead && options.active === 'events')
  const events = useSessionEvents(sessionId, filters.events, { enabled: canRead && options.active === 'events' && validation.events.valid })
  const pinned = useSessionConfiguration(canRead && options.active === 'events' ? sessionId : undefined)
  const commands = useCommands({ query: filters.commands }, canRead && options.active === 'commands' && validation.commands.valid)
  const audit = useAudit({ query: filters.audit }, options.active === 'audit' && validation.audit.valid)
  const [selectedReceipt, setSelectedReceipt] = useState<ReceiptReference | null>(null)
  const inspection = useReceiptInspection(selectedReceipt)

  function changeCommands(next: CommandFilters) {
    if (JSON.stringify(next) !== JSON.stringify(filters.commands)) void client.resetQueries({ queryKey: ['admin', 'listCommands', 'infinite', { query: next }], exact: true })
    setFilters((current) => ({ ...current, commands: { ...next } }))
  }
  function changeEvents(next: EventFilters) {
    if (sessionId && JSON.stringify(next) !== JSON.stringify(filters.events)) void client.resetQueries({ queryKey: ['admin', 'getSessionEvents', 'infinite', { path: { session_id: sessionId }, query: next }], exact: true })
    setFilters((current) => ({ ...current, events: { ...next } }))
  }
  function changeAudit(next: AuditFilters) {
    if (JSON.stringify(next) !== JSON.stringify(filters.audit)) void client.resetQueries({ queryKey: ['admin', 'getAdminAudit', 'infinite', { query: next }], exact: true })
    setFilters((current) => ({ ...current, audit: { ...next } }))
  }
  function selectSession(id: string | undefined) {
    if (id !== undefined && (!id.trim() || id.length > 128)) throw new Error('invalid_session_id')
    if (id) void client.resetQueries({ queryKey: ['admin', 'getSessionEvents', 'infinite', { path: { session_id: id }, query: filters.events }], exact: true })
    setSelectedSession(id)
  }
  return { filters, validation, sessionId, sessions, events, pinned, commands, audit, selectedReceipt, inspection,
    changeCommands, changeEvents, changeAudit, selectSession,
    inspectCommand: (id: string) => { if (!canRead) throw new Error('permission_required'); if (id.trim()) setSelectedReceipt({ kind: 'command', id }) },
    access: { allowed: canRead, reasonCode: canRead ? null : 'permission_required' },
    loadMoreCommands: () => canRead && validation.commands.valid && commands.hasNextPage ? commands.fetchNextPage() : Promise.resolve(null),
    loadMoreEvents: () => canRead && validation.events.valid && events.hasNextPage ? events.fetchNextPage() : Promise.resolve(null),
    loadMoreAudit: () => audit.access.allowed && validation.audit.valid && audit.hasNextPage ? audit.fetchNextPage() : Promise.resolve(null),
    closeInspection: () => setSelectedReceipt(null),
    auditForOperation: (operationId: string) => changeAudit({ ...filters.audit, operation_id: operationId }),
    commandLinks: (commands.data?.pages.flatMap((page) => page.items) ?? []).map((command) => ({
      reference: { kind: 'command' as const, id: command.id }, sessionId: command.session_id,
      action: command.action, status: command.status, actorId: null,
      auditFilter: { operation_id: command.id } satisfies AuditFilters,
    })),
  }
}
export type HistoryControllerModel = ReturnType<typeof useHistoryController>
