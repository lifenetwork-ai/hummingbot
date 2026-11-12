import { AlertCircle, FileSearch, Loader2, Terminal } from 'lucide-react'
import type { ReceiptReference } from '../../core/api/receipt-tracking'
import { Badge } from '../../shared/components/Badge'
import { Button } from '../../shared/components/Button'
import { CopyableId } from '../../shared/components/CopyableId'
import { Dialog } from '../../shared/components/Dialog'

export interface CommandInspectionModalProps {
  reference: ReceiptReference | null
  inspection?: {
    phase: 'idle' | 'loading' | 'ready' | 'error'
    status?: string | null
    error?: Error | null
    outcome?: unknown
  }
  onClose: () => void
  onAuditForOperation?: (operationId: string) => void
}

export function CommandInspectionModal({
  reference,
  inspection,
  onClose,
  onAuditForOperation,
}: CommandInspectionModalProps) {
  if (!reference) return null

  const isOpen = Boolean(reference)
  const isSucceeded = inspection?.status === 'succeeded'
  const isFailed = inspection?.status === 'failed'
  const isLoading = inspection?.phase === 'loading'

  return (
    <Dialog
      open={isOpen}
      onClose={onClose}
      title="Operation Receipt Inspection"
      description="Inspect processing progress, status, and observed outcome for backend receipts."
    >
      <div className="space-y-4 text-xs">
        {/* Receipt Header details */}
        <div className="rounded-card border border-app-border/80 bg-app-surface-muted/30 p-3 space-y-2">
          <div className="flex items-center justify-between">
            <span className="font-semibold text-app-text flex items-center gap-1.5">
              <Terminal size={14} className="text-app-action" />
              <span className="uppercase">{reference.kind} Receipt</span>
            </span>
            <div className="flex items-center gap-1.5">
              {isLoading && (
                <span className="inline-flex items-center gap-1 text-[10px] text-app-muted">
                  <Loader2 size={11} className="animate-spin" />
                  <span>Loading…</span>
                </span>
              )}
              <Badge
                tone={isSucceeded ? 'success' : isFailed ? 'danger' : 'brand'}
                className="text-[10px] uppercase font-bold"
              >
                {inspection?.status ?? inspection?.phase ?? 'unknown'}
              </Badge>
            </div>
          </div>

          <div className="flex items-center justify-between text-[11px] pt-1 border-t border-app-border/40">
            <span className="text-app-muted">Receipt ID:</span>
            <CopyableId id={reference.id} copyLabel="Copy Receipt ID" />
          </div>
        </div>

        {/* Error if present */}
        {inspection?.error && (
          <div role="alert" className="rounded-card border border-app-danger/30 bg-app-danger/10 p-3 text-xs text-app-danger flex items-start gap-2">
            <AlertCircle size={16} className="shrink-0 mt-0.5" />
            <div>
              <strong className="block font-bold">Error reading receipt:</strong>
              <p>{inspection.error.message}</p>
            </div>
          </div>
        )}

        {/* Outcome details */}
        {inspection?.outcome !== undefined && inspection.outcome !== null && (
          <div className="space-y-1.5">
            <span className="font-semibold text-app-text text-[11px] block">
              Typed Outcome:
            </span>
            <pre className="max-h-56 overflow-auto rounded-card border border-app-border bg-app-surface-muted/40 p-3 text-[11px] font-mono text-app-text leading-relaxed">
              {typeof inspection.outcome === 'object'
                ? JSON.stringify(inspection.outcome, null, 2)
                : String(inspection.outcome)}
            </pre>
            <p className="text-[10px] text-app-muted">
              Notice: A succeeded receipt status only confirms backend acknowledgment; actual exchange effects depend on order book and position observations.
            </p>
          </div>
        )}

        {/* Action buttons */}
        <div className="flex flex-wrap items-center justify-between gap-2 pt-3 border-t border-app-border/80">
          <div>
            {onAuditForOperation && (
              <Button
                variant="secondary"
                onClick={() => {
                  onAuditForOperation(reference.id)
                  onClose()
                }}
                className="h-8 px-3 text-xs gap-1.5 font-bold"
              >
                <FileSearch size={13} />
                <span>Find in Audit Log</span>
              </Button>
            )}
          </div>
          <Button
            variant="secondary"
            onClick={onClose}
            className="h-8 px-3 text-xs"
          >
            Close
          </Button>
        </div>
      </div>
    </Dialog>
  )
}
