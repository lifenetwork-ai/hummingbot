import { useCallback, useEffect, useRef } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useAuth } from '../../core/auth/auth-context'
import { useAdminInfiniteQuery } from '../../core/api/infinite'
import { useAdminQuery } from '../../core/api/hooks'
import { useAudit } from '../history/useHistoryQueries'
import { validateHistoryFilters } from '../history/filters'
import type { AuditState } from './useAuditState'
import { SecurityAuditView } from '../admin/SecurityAuditView'

export function AuditUnit({ active, state }: { active: boolean; state: AuditState }) {
  const { identity } = useAuth()
  const client = useQueryClient()
  const canRead = identity?.permissions.read === true
  const canManageAccounts = identity?.permissions.manage_accounts === true
  const validation = validateHistoryFilters(state.filters)
  const audit = useAudit({ query: state.filters }, active && validation.valid)
  const accounts = useAdminInfiniteQuery('listAdminAccounts', {}, { enabled: active && canRead && canManageAccounts })
  const catalogue = useAdminQuery('listCredentialInputs', {}, { enabled: active && canRead, poll: false })
  const intent = state.lookupIntent
  const handledRequestId = useRef<number | null>(null)
  const changeFilters = useCallback((next: typeof state.filters) => {
    if (JSON.stringify(next) !== JSON.stringify(state.filters)) {
      void client.resetQueries({ queryKey: ['admin', 'getAdminAudit', 'infinite', { query: next }], exact: true })
    }
    state.changeFilters(next)
  }, [client, state])

  useEffect(() => {
    if (!active || !intent || handledRequestId.current === intent.requestId) return
    handledRequestId.current = intent.requestId
    changeFilters({ ...state.filters, operation_id: intent.operationId })
    state.consumeLookup(intent.requestId)
  }, [active, intent, state, changeFilters])

  if (!active) return null

  const accountItems = accounts.data?.pages.flatMap((page) => page.items).map(({ id, email, role }) => ({ id, email, role }))
  const exchanges = catalogue.data?.exchanges.map(({ exchange }) => exchange)
  const connectors = catalogue.data?.exchanges.flatMap(({ connectors: items }) => items)

  return (
    <SecurityAuditView
      entries={audit.data?.pages.flatMap((page) => page.items)}
      filters={state.filters}
      onFilterChange={changeFilters}
      validationErrors={validation.errors}
      isLoading={audit.isLoading}
      error={!canRead ? 'Audit access requires read permission.' : audit.error?.message}
      hasMore={audit.hasNextPage}
      onLoadMore={() => { if (audit.access.allowed && validation.valid && audit.hasNextPage) void audit.fetchNextPage() }}
      onInspectOperation={state.requestLookup}
      accounts={accountItems}
      exchanges={exchanges}
      connectors={connectors}
    />
  )
}
