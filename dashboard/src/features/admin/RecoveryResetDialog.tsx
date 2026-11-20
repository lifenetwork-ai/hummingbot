import { AlertTriangle, CheckCircle2, Loader2, Wrench } from 'lucide-react'
import { useEffect, useState } from 'react'
import type { components } from '../../core/api/schema'
import { Badge } from '../../shared/components/Badge'
import { Button } from '../../shared/components/Button'
import { Dialog } from '../../shared/components/Dialog'

export interface RecoveryResetDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onConfirmReset: (options?: { confirmOrderChanges?: boolean }) => Promise<void> | void
  isResetting?: boolean
  error?: string | null
  /** True for verification.scope=verified_checkpoint with an empty proposed-change list. */
  checkpointPreserved?: boolean
  scopedOrders?: components['schemas']['RecoveryUncertainOrderRepairOutput'][]
  previewValid?: boolean
}

export function RecoveryResetDialog({
  open,
  onOpenChange,
  onConfirmReset,
  isResetting = false,
  error = null,
  checkpointPreserved = false,
  scopedOrders = [],
  previewValid = true,
}: RecoveryResetDialogProps) {
  const [acknowledged, setAcknowledged] = useState(false)
  const isScoped = scopedOrders.length > 0
  const orderIdsKey = scopedOrders.map((o) => o.id).sort().join(',')

  useEffect(() => {
    setAcknowledged(false)
  }, [open, orderIdsKey])

  const handleClose = () => {
    if (!isResetting) {
      setAcknowledged(false)
      onOpenChange(false)
    }
  }

  const handleSubmit = async () => {
    if (isScoped && !acknowledged) return
    if (!previewValid) return
    await onConfirmReset(isScoped ? { confirmOrderChanges: true } : undefined)
    setAcknowledged(false)
  }

  const isConfirmDisabled = isResetting || (isScoped && !acknowledged) || !previewValid
  const lockReason = isResetting
    ? 'Reset is currently in progress.'
    : !previewValid
      ? 'Preview has expired. Run a new Check recovery before resetting.'
      : isScoped && !acknowledged
        ? 'Explicit confirmation required: review and confirm all uncertain order changes.'
        : undefined

  return (
    <Dialog
      open={open}
      onClose={handleClose}
      title={isScoped ? 'Confirm Uncertain Order Recovery Reset' : 'Confirm Recovery Reset'}
      description={
        isScoped
          ? `Review and confirm transition of ${scopedOrders.length} uncertain order(s) to OrderFailure.`
          : checkpointPreserved
            ? 'Confirm the verified checkpoint review to close the HALT.'
            : 'Apply reviewed changes to bring the bot out of HALT state.'
      }
      className="max-w-lg"
    >
      <div className="space-y-4 text-xs text-app-text">
        <div className="rounded-card border border-app-warning/30 bg-app-warning/10 p-3.5 space-y-2">
          <div className="flex items-start gap-2.5">
            <Wrench size={18} className="text-app-warning mt-0.5 shrink-0" />
            <div>
              <h4 className="font-bold text-app-text">
                {isScoped
                  ? `Apply uncertain order repairs (${scopedOrders.length})`
                  : checkpointPreserved
                    ? 'Confirm verified checkpoint review'
                    : 'Apply reviewed checkpoint modifications'}
              </h4>
              <p className="mt-1 leading-relaxed text-app-muted">
                {isScoped ? (
                  <>
                    The system will record the reviewed uncertain orders as <strong>OrderFailure</strong> in the local ledger after reconciliation. This is a local state repair and does not trade on the exchange.{' '}
                  </>
                ) : checkpointPreserved ? (
                  <>No ledger changes will be applied — financial rows, remaining cost basis and risk checkpoints are preserved as reviewed. </>
                ) : (
                  <>The system will synchronize position checkpoints and applies the reviewed changes. </>
                )}
                The system will return the bot to a <strong>Safe Stopped (Stopped)</strong> state. The bot will NOT automatically resume trading.
              </p>
            </div>
          </div>
        </div>

        {/* Scoped orders preview inside dialog */}
        {isScoped && (
          <div className="space-y-2">
            <span className="font-semibold text-app-text text-xs">
              Orders to be repaired ({scopedOrders.length}):
            </span>
            <div className="max-h-48 overflow-y-auto rounded-card border border-app-border divide-y divide-app-border/60">
              {scopedOrders.map((order) => (
                <div key={order.id} className="p-2.5 flex items-center justify-between text-xs">
                  <div>
                    <span className="font-mono font-bold text-app-text">{order.id}</span>
                    <span className="ml-2 font-mono text-app-muted">{order.pair}</span>
                    <Badge tone={order.side === 'BUY' ? 'success' : 'danger'} className="ml-2 text-[10px]">
                      {order.side}
                    </Badge>
                  </div>
                  <div className="text-right">
                    <span className="font-mono text-app-muted">{order.amount}</span>
                    <span className="ml-2 font-mono text-app-action font-semibold">
                      {order.before} → {order.after}
                    </span>
                  </div>
                </div>
              ))}
            </div>

            {/* Explicit confirmation checkbox */}
            <label className="flex items-start gap-2.5 cursor-pointer rounded-button border border-app-border/80 bg-app-surface p-3 hover:bg-app-surface-muted/30">
              <input
                type="checkbox"
                id="confirm-scoped-order-changes"
                checked={acknowledged}
                onChange={(e) => setAcknowledged(e.target.checked)}
                disabled={isResetting}
                className="mt-0.5 h-4 w-4 rounded border-app-border text-app-action focus:ring-app-action"
              />
              <span className="text-xs text-app-text font-medium leading-relaxed">
                I confirm that I have reviewed the entire list of {scopedOrders.length} uncertain order(s) and acknowledge transitioning their status to OrderFailure.
              </span>
            </label>
          </div>
        )}

        {!previewValid && (
          <div className="rounded-card border border-app-danger/30 bg-app-danger/10 p-3 text-app-danger flex items-start gap-2">
            <AlertTriangle size={15} className="mt-0.5 shrink-0" />
            <div>
              <strong>Preview expired:</strong> Run a new Check recovery before confirming Reset.
            </div>
          </div>
        )}

        {error && (
          <div className="rounded-card border border-app-danger/30 bg-app-danger/10 p-3 text-app-danger flex items-start gap-2">
            <AlertTriangle size={15} className="mt-0.5 shrink-0" />
            <div>
              <strong>Error applying Reset:</strong> {error}
            </div>
          </div>
        )}

        {lockReason && isConfirmDisabled && !isResetting && (
          <p className="text-[11px] text-app-warning" role="note">
            {lockReason}
          </p>
        )}

        <div className="mt-6 flex flex-col-reverse justify-end gap-2.5 sm:flex-row pt-2 border-t border-app-border/70">
          <Button
            variant="secondary"
            onClick={handleClose}
            disabled={isResetting}
            className="w-full sm:w-auto"
          >
            Cancel
          </Button>

          <Button
            variant="primary"
            onClick={() => { void handleSubmit() }}
            disabled={isConfirmDisabled}
            title={lockReason}
            aria-disabled={isConfirmDisabled}
            className="w-full sm:w-auto font-bold gap-1.5"
          >
            {isResetting ? (
              <>
                <Loader2 size={14} className="animate-spin" />
                <span>Applying Reset...</span>
              </>
            ) : (
              <>
                <CheckCircle2 size={14} />
                <span>Confirm Reset</span>
              </>
            )}
          </Button>
        </div>
      </div>
    </Dialog>
  )
}
