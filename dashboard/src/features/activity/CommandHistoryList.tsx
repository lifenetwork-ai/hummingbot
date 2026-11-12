import { ChevronRight, FileSearch, Search, Terminal } from 'lucide-react'
import type { components } from '../../core/api/schema'
import type { ReceiptReference } from '../../core/api/receipt-tracking'
import type { AuditFilters, CommandFilters } from '../history/filters'
import { Badge } from '../../shared/components/Badge'
import { Button } from '../../shared/components/Button'
import { CopyableId } from '../../shared/components/CopyableId'
import { Surface } from '../../shared/components/Surface'
import { CommandInspectionModal } from './CommandInspectionModal'
import { HistoryFiltersBar } from './HistoryFiltersBar'

export interface CommandHistoryListProps {
  commands?: components['schemas']['CommandResponseOutput'][]
  isLoading?: boolean
  commandLinks?: {
    reference: { kind: 'command'; id: string }
    sessionId: string | null
    action: string
    status: string
    actorId: string | null
    auditFilter: AuditFilters
  }[]
  onInspectCommand?: (id: string) => void
  onAuditForOperation?: (operationId: string) => void
  filters?: CommandFilters
  validationErrors?: Record<string, string>
  onFilterChange?: (filters: CommandFilters) => void
  hasMore?: boolean
  onLoadMore?: () => void
  selectedReceipt?: ReceiptReference | null
  inspection?: {
    phase: 'idle' | 'loading' | 'ready' | 'error'
    status?: string | null
    error?: Error | null
    outcome?: unknown
  }
  onCloseInspection?: () => void
}

export function CommandHistoryList({
  commands = [],
  isLoading,
  commandLinks = [],
  onInspectCommand,
  onAuditForOperation,
  filters,
  onFilterChange,
  validationErrors,
  hasMore,
  onLoadMore,
  selectedReceipt,
  inspection,
  onCloseInspection,
}: CommandHistoryListProps) {
  // Map of links by command ID
  const linkMap = new Map(commandLinks.map((link) => [link.reference.id, link]))

  return (
    <Surface className="p-5 md:p-6 space-y-4">
      <div className="border-b border-app-border/80 pb-3">
        <div className="flex items-center gap-2">
          <Terminal size={18} className="text-app-action" />
          <h3 className="font-heavy text-lg font-bold text-app-text">
            Command History
          </h3>
        </div>
        <p className="mt-1 text-xs text-app-muted">
          History of Start, Switch, and Stop commands with receipt status, effective version, and audit log cross-references.
        </p>
      </div>

      {/* Filter Bar */}
      {filters && onFilterChange && (
        <HistoryFiltersBar
          mode="commands"
          filters={filters}
          onChange={onFilterChange} validationErrors={validationErrors}
        />
      )}

      <div className="overflow-hidden rounded-card border border-app-border">
        <table className="w-full text-left text-xs">
          <thead>
            <tr className="border-b border-app-border bg-app-surface-muted/60 text-app-muted font-semibold">
              <th className="p-3">Timestamp</th>
              <th className="p-3">Action</th>
              <th className="p-3">Session ID</th>
              <th className="p-3">Status</th>
              <th className="p-3">Version</th>
              <th className="p-3">Command ID</th>
              <th className="p-3">Actor</th>
              <th className="p-3 text-right">Inspect / Audit</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-app-border/60">
            {isLoading ? (
              <tr>
                <td colSpan={8} className="p-8 text-center text-app-muted">
                  Loading command history…
                </td>
              </tr>
            ) : commands.length === 0 ? (
              <tr>
                <td colSpan={8} className="p-8 text-center text-app-muted">
                  No commands recorded yet.
                </td>
              </tr>
            ) : (
              commands.map((cmd) => {
                const link = linkMap.get(cmd.id)
                const actorId = link?.actorId ?? null

                return (
                  <tr key={cmd.id} className="hover:bg-app-surface-muted/30 transition-colors">
                    <td className="p-3 font-mono text-app-muted whitespace-nowrap">
                      {cmd.created_at ? new Date(cmd.created_at).toLocaleString('en-US') : '—'}
                    </td>
                    <td className="p-3 font-bold uppercase text-app-text">{cmd.action}</td>
                    <td className="p-3">
                      {cmd.session_id ? (
                        <CopyableId
                          id={cmd.session_id}
                          truncateLength={10}
                          copyLabel="Copy session ID"
                          codeClassName="text-[11px] text-app-muted"
                        />
                      ) : (
                        <span className="font-mono text-app-muted text-[11px]">—</span>
                      )}
                    </td>
                    <td className="p-3">
                      <Badge
                        tone={cmd.status === 'succeeded' ? 'success' : cmd.status === 'failed' ? 'danger' : 'neutral'}
                        className="text-[10px] capitalize font-mono"
                      >
                        {cmd.status}
                      </Badge>
                    </td>
                    <td className="p-3 font-mono font-semibold text-app-text">
                      {cmd.effective_version ? `v${cmd.effective_version}` : '—'}
                    </td>
                    <td className="p-3">
                      <CopyableId
                        id={cmd.id}
                        truncateLength={10}
                        copyLabel="Copy command ID"
                        codeClassName="text-[11px] text-app-muted"
                      />
                    </td>
                    <td className="p-3 font-mono text-[11px] text-app-muted">
                      {actorId ? (
                        <CopyableId id={actorId} truncateLength={8} copyLabel="Copy actor ID" />
                      ) : (
                        <span>No audit link</span>
                      )}
                    </td>
                    <td className="p-3 text-right">
                      <div className="flex items-center justify-end gap-1.5">
                        {onInspectCommand && (
                          <Button
                            variant="secondary"
                            onClick={() => onInspectCommand(cmd.id)}
                            className="h-6 px-2 text-[10px] gap-1 font-semibold"
                            title="Inspect receipt status details"
                          >
                            <Search size={11} />
                            <span>Inspect</span>
                          </Button>
                        )}
                        {onAuditForOperation && (
                          <Button
                            variant="secondary"
                            onClick={() => onAuditForOperation(cmd.id)}
                            className="h-6 px-2 text-[10px] gap-1"
                            title="Filter audit log for this operation_id"
                          >
                            <FileSearch size={11} />
                            <span>Audit</span>
                          </Button>
                        )}
                      </div>
                    </td>
                  </tr>
                )
              })
            )}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {hasMore && onLoadMore && (
        <div className="flex justify-center pt-2">
          <Button
            variant="secondary"
            onClick={onLoadMore}
            disabled={isLoading}
            className="h-8 px-4 text-xs gap-1"
          >
            <span>{isLoading ? 'Loading more…' : 'Load more commands'}</span>
            <ChevronRight size={14} />
          </Button>
        </div>
      )}

      {/* Inspection Modal */}
      {selectedReceipt && onCloseInspection && (
        <CommandInspectionModal
          reference={selectedReceipt}
          inspection={inspection}
          onClose={onCloseInspection}
          onAuditForOperation={onAuditForOperation}
        />
      )}
    </Surface>
  )
}
