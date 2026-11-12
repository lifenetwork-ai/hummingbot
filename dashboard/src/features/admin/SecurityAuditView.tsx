import { ChevronRight, FileSearch, FileText } from 'lucide-react'
import type { components } from '../../core/api/schema'
import type { AuditFilters } from '../history/filters'
import { Badge } from '../../shared/components/Badge'
import { Button } from '../../shared/components/Button'
import { CopyableId } from '../../shared/components/CopyableId'
import { Surface } from '../../shared/components/Surface'
import { HistoryFiltersBar } from '../activity/HistoryFiltersBar'

export interface SecurityAuditViewProps {
  entries?: components['schemas']['AuditEventResponseOutput'][]
  auditData?: components['schemas']['AuditPageOutput'] | null
  isLoading?: boolean
  error?: string
  hasMore?: boolean
  onLoadMore?: () => void
  filters?: AuditFilters
  onFilterChange?: (filters: AuditFilters) => void
  validationErrors?: Record<string, string>
  onInspectOperation?: (operationId: string) => void
  accounts?: Pick<components['schemas']['AccountResponseOutput'], 'id' | 'email' | 'role'>[]
  exchanges?: string[]
  connectors?: string[]
}

export function SecurityAuditView({
  entries,
  auditData,
  isLoading,
  error,
  hasMore,
  onLoadMore,
  filters,
  onFilterChange,
  validationErrors,
  onInspectOperation,
  accounts,
  exchanges,
  connectors,
}: SecurityAuditViewProps) {
  const items = entries ?? auditData?.items ?? []

  return (
    <Surface className="p-5 md:p-6 space-y-4">
      <div className="border-b border-app-border/80 pb-4">
        <div className="flex items-center gap-2">
          <FileText size={20} className="text-app-action" />
          <h3 className="font-heavy text-lg font-bold text-app-text">
            Security & Audit Log
          </h3>
        </div>
        <p className="mt-1 text-xs text-app-muted">
          System intervention history: Track access permission changes, credential updates, and session interventions.
        </p>
      </div>

      {/* Filter Bar if filters & onFilterChange are supplied */}
      {filters && onFilterChange && (
        <HistoryFiltersBar
          mode="audit"
          filters={filters}
          onChange={onFilterChange}
          validationErrors={validationErrors}
          accounts={accounts}
          exchanges={exchanges}
          connectors={connectors}
        />
      )}

      <div className="overflow-hidden rounded-card border border-app-border">
        <table className="w-full text-left text-xs">
          <thead>
            <tr className="border-b border-app-border bg-app-surface-muted/60 text-app-muted font-semibold">
              <th className="p-3">Timestamp</th>
              <th className="p-3">Actor</th>
              <th className="p-3">Action</th>
              <th className="p-3">Outcome</th>
              <th className="p-3">Exchange / Connector</th>
              <th className="p-3">Operation ID</th>
              <th className="p-3">Role</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-app-border/60">
            {isLoading ? (
              <tr>
                <td colSpan={7} className="p-6 text-center text-app-muted">
                  Loading security audit log...
                </td>
              </tr>
            ) : items.length === 0 ? (
              <tr>
                <td colSpan={7} className="p-6 text-center text-app-muted">
                  No audit events recorded.
                </td>
              </tr>
            ) : (
              items.map((entry, idx) => (
                <tr key={idx} className="hover:bg-app-surface-muted/30">
                  <td className="p-3 font-mono text-app-muted whitespace-nowrap">
                    {entry.created_at ? new Date(entry.created_at).toLocaleString('en-US') : '—'}
                  </td>
                  <td className="p-3 font-mono font-semibold text-app-text">
                    {entry.actor_email ? (
                      entry.actor_email
                    ) : entry.actor_id ? (
                      <CopyableId id={entry.actor_id} truncateLength={10} copyLabel="Copy actor ID" />
                    ) : (
                      <span className="text-app-muted italic">Unknown</span>
                    )}
                  </td>
                  <td className="p-3">
                    <Badge tone="neutral" className="text-[10px] font-mono">
                      {entry.action}
                    </Badge>
                  </td>
                  <td className="p-3">
                    <Badge
                      tone={entry.outcome === 'success' ? 'success' : entry.outcome === 'denied' || entry.outcome === 'failure' ? 'danger' : 'neutral'}
                      className="text-[10px] font-mono capitalize"
                    >
                      {entry.outcome ?? '—'}
                    </Badge>
                  </td>
                  <td className="p-3 font-mono text-app-text">
                    {entry.exchange ? `${entry.exchange} (${entry.connector || '—'})` : '—'}
                  </td>
                  <td className="p-3 font-mono text-app-text">
                    {entry.operation_id ? (
                      <div className="flex items-center gap-1">
                        <CopyableId id={entry.operation_id} truncateLength={10} copyLabel="Copy operation ID" />
                        {onInspectOperation && (
                          <button
                            type="button"
                            onClick={() => onInspectOperation(entry.operation_id!)}
                            className="p-1 text-app-muted hover:text-app-action"
                            title="Inspect this operation"
                          >
                            <FileSearch size={12} />
                          </button>
                        )}
                      </div>
                    ) : (
                      <span className="text-app-muted">—</span>
                    )}
                  </td>
                  <td className="p-3 font-mono text-app-muted capitalize">
                    {entry.actor_role || entry.actor_kind || '—'}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {error && (
        <p role="alert" className="mt-3 text-xs text-app-danger">
          {error}
        </p>
      )}

      {hasMore && onLoadMore && (
        <div className="mt-4 flex justify-center">
          <Button variant="secondary" onClick={onLoadMore} disabled={isLoading} className="h-8 px-3 text-xs gap-1">
            <span>{isLoading ? 'Loading...' : 'Load more logs'}</span>
            <ChevronRight size={14} />
          </Button>
        </div>
      )}
    </Surface>
  )
}
