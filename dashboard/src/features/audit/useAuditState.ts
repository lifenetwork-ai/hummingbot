import { useRef, useState } from 'react'
import type { AuditFilters } from '../history/filters'
import { DEFAULT_HISTORY_PAGE_SIZE } from '../history/filters'

export type AuditLookupIntent = { operationId: string; requestId: number }

/** Mutable audit UI state belongs to a route instance, not the shared query cache. */
export function useAuditState() {
  const [filters, setFilters] = useState<AuditFilters>({ limit: DEFAULT_HISTORY_PAGE_SIZE })
  const [lookupIntent, setLookupIntent] = useState<AuditLookupIntent | null>(null)
  const requestId = useRef(0)

  const changeFilters = (next: AuditFilters) => setFilters({ ...next })
  const requestLookup = (operationId: string) => {
    requestId.current += 1
    setLookupIntent({ operationId, requestId: requestId.current })
    return requestId.current
  }
  const consumeLookup = (id: number) => setLookupIntent((current) => current?.requestId === id ? null : current)

  return { filters, changeFilters, lookupIntent, requestLookup, consumeLookup }
}

export type AuditState = ReturnType<typeof useAuditState>
