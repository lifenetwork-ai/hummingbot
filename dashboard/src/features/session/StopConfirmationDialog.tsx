import { AlertCircle, AlertTriangle, CheckCircle2, Loader2, OctagonAlert } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import type { OperationalContextModel } from '../operational-context/context'
import { notify } from '../../core/feedback/notify'
import { Button } from '../../shared/components/Button'
import { CopyableId } from '../../shared/components/CopyableId'
import { Dialog } from '../../shared/components/Dialog'

export interface StopConfirmationDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  context?: OperationalContextModel | null
  onConfirm?: () => Promise<void> | void
  isSubmitting?: boolean
  error?: string | null
}

export function StopConfirmationDialog({
  open,
  onOpenChange,
  context,
  onConfirm,
  isSubmitting = false,
  error = null,
}: StopConfirmationDialogProps) {
  const cancelButtonRef = useRef<HTMLButtonElement>(null)
  const [successNoticed, setSuccessNoticed] = useState(false)
  const [localError, setLocalError] = useState<string | null>(null)

  const stopAvailability = context?.actions.stop
  const isAllowed = stopAvailability?.allowed === true && Boolean(onConfirm)
  const blockedReason = stopAvailability?.detail || stopAvailability?.reasonCode || 'Stop action not permitted by backend'

  const sessionId = context?.status.data?.observed_session?.id || context?.status.data?.session?.id || '—'
  const controlVersion = context?.controlVersion ?? '—'

  useEffect(() => {
    if (open) {
      setSuccessNoticed(false)
      setLocalError(null)
      setTimeout(() => {
        cancelButtonRef.current?.focus()
      }, 50)
    }
  }, [open])

  const handleConfirm = async () => {
    if (!isAllowed || isSubmitting) return
    try {
      if (onConfirm) {
        await onConfirm()
      }
      setSuccessNoticed(true)
      notify.info('Stop receipt received — completion not yet verified', {
        id: 'stop-confirmation',
        description: 'Positions remain intact; observe exchange and receipt to verify outcome.',
      })
      setTimeout(() => {
        onOpenChange(false)
      }, 1500)
    } catch (failure) {
      const msg = failure instanceof Error ? failure.message : 'Stop submission outcome unknown; track receipt before retrying.'
      setLocalError(msg)

    }
  }

  return (
    <Dialog
      open={open}
      onClose={() => {
        if (!isSubmitting) {
          onOpenChange(false)
        }
      }}
      title="Stop market making; positions remain open"
      description="Confirm emergency stop for all market-making quoting operations."
      className="max-w-lg"
    >
      <div className="space-y-4 text-sm text-app-text">
        {/* Core Guarantee Alert */}
        <div className="rounded-card border border-app-danger/30 bg-app-danger/10 p-4">
          <div className="flex items-start gap-3">
            <OctagonAlert className="mt-0.5 shrink-0 text-app-danger" size={20} />
            <div className="space-y-1.5">
              <h4 className="font-heavy text-base font-bold text-app-danger">
                Stop MM; positions remain open
              </h4>
              <p className="text-xs leading-5 text-app-text">
                When executing this Stop command:
              </p>
              <ul className="list-disc pl-4 text-xs space-y-1 text-app-text">
                <li>Bot receives request to <strong>stop quoting and cancel active MM orders</strong>. Track receipt and exchange observations to verify outcome.</li>
                <li><strong>Perpetual contract positions REMAIN OPEN</strong> and will not be force-closed to the market to protect against slippage and high fees.</li>
                <li>Stop does not actively liquidate Spot holdings; balances must still be reconciled against exchange observations.</li>
              </ul>
            </div>
          </div>
        </div>

        {/* Session context details */}
        <div className="rounded-card border border-app-border/80 bg-app-surface-muted/50 p-3.5 text-xs space-y-2">
          <div className="flex justify-between items-center gap-2">
            <span className="text-app-muted shrink-0">Target session ID:</span>
            {sessionId !== '—' ? (
              <CopyableId id={sessionId} copyLabel="Copy session ID" />
            ) : (
              <span className="font-mono font-semibold text-app-text">—</span>
            )}
          </div>
          <div className="flex justify-between items-center">
            <span className="text-app-muted">Control version:</span>
            <span className="font-mono text-app-text">v{controlVersion}</span>
          </div>
          <div className="flex justify-between items-center">
            <span className="text-app-muted">Applicable markets:</span>
            <span className="font-medium text-app-text">All (Spot & Perpetual)</span>
          </div>
        </div>

        {/* Blocked reason banner if not allowed */}
        {!isAllowed && (
          <div className="rounded-card border border-app-warning/30 bg-app-warning/10 p-3 text-xs text-app-warning flex items-start gap-2">
            <AlertTriangle size={15} className="mt-0.5 shrink-0" />
            <div>
              <strong>Action currently blocked:</strong> {blockedReason}
            </div>
          </div>
        )}

        {/* Error message */}
        {(error || localError) && (
          <div className="rounded-card border border-app-danger/30 bg-app-danger/10 p-3 text-xs text-app-danger flex items-start gap-2">
            <AlertCircle size={15} className="mt-0.5 shrink-0" />
            <div>
              <strong>Command submission error:</strong> {error || localError}
            </div>
          </div>
        )}

        {/* Success confirmation note */}
        {successNoticed && (
          <div className="rounded-card border border-emerald-500/30 bg-emerald-500/10 p-3 text-xs text-emerald-500 flex items-center gap-2">
            <CheckCircle2 size={16} />
            <span>Stop receipt received; completion not yet verified. Track progress in the operational context bar.</span>
          </div>
        )}

        {/* Modal Actions */}
        <div className="mt-6 flex flex-col-reverse justify-end gap-2.5 sm:flex-row pt-2 border-t border-app-border/70">
          <Button
            ref={cancelButtonRef}
            variant="secondary"
            onClick={() => onOpenChange(false)}
            disabled={isSubmitting}
            className="w-full sm:w-auto"
          >
            Cancel (Keep running)
          </Button>

          <Button
            variant="danger"
            onClick={handleConfirm}
            disabled={!isAllowed || isSubmitting || successNoticed}
            className="w-full sm:w-auto font-bold"
          >
            {isSubmitting ? (
              <span className="flex items-center gap-1.5">
                <Loader2 size={14} className="animate-spin" />
                <span>Processing stop...</span>
              </span>
            ) : (
              <span>Confirm stop bot now</span>
            )}
          </Button>
        </div>
      </div>
    </Dialog>
  )
}
