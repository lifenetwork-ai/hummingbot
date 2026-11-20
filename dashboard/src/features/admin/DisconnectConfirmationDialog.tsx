import { AlertTriangle, Loader2, OctagonAlert, Unlink } from 'lucide-react'
import { useRef } from 'react'
import { Button } from '../../shared/components/Button'
import { Dialog } from '../../shared/components/Dialog'

export interface DisconnectConfirmationDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  connector?: string | null
  onConfirmDisconnect: () => Promise<void> | void
  isSubmitting?: boolean
  error?: string | null
  /** Backend lock reason (decisions.disconnect.detail/reasonCode); when set, confirm stays disabled but the reason stays readable. */
  lockReason?: string | null
}

export function DisconnectConfirmationDialog({
  open,
  onOpenChange,
  connector,
  onConfirmDisconnect,
  isSubmitting = false,
  error = null,
  lockReason = null,
}: DisconnectConfirmationDialogProps) {
  const cancelButtonRef = useRef<HTMLButtonElement>(null)

  return (
    <Dialog
      open={open}
      onClose={() => {
        if (!isSubmitting) onOpenChange(false)
      }}
      title={`Disconnect exchange ${connector ? connector.toUpperCase() : 'connection'}`}
      description="Stop trading, cancel this bot's pending orders and close its managed perpetual positions at market price, then remove this saved connection."
      className="max-w-lg"
    >
      <div className="space-y-4 text-xs text-app-text">
        {/* Critical Warnings Box */}
        <div className="rounded-card border border-app-danger/30 bg-app-danger/10 p-4">
          <div className="flex items-start gap-3">
            <OctagonAlert className="mt-0.5 shrink-0 text-app-danger" size={20} />
            <div className="space-y-2">
              <h4 className="font-heavy text-sm font-bold text-app-danger">
                Important Distinction: Disconnect differs from Stop
              </h4>
              <p className="leading-relaxed">
                When confirming exchange disconnect:
              </p>
              <ul className="list-disc pl-4 space-y-1.5 leading-relaxed">
                <li>
                  <strong>Stops the whole bot:</strong> Disconnect stops bot-wide trading on every market — not only the market of this connection. Use Stop instead if you only want to stop quoting while keeping the saved key.
                </li>
                <li>
                  <strong>Cancel this bot's pending orders:</strong> Cleanup cancels the bot's open buy and sell orders and verifies their terminal outcomes before removing the saved key.
                </li>
                <li>
                  <strong className="text-app-danger">Close managed perpetual positions at market price:</strong> Unlike Stop (which preserves positions), Disconnect will <strong>close its managed perpetual positions using Market orders</strong>. Market closure may incur trading fees and slippage.
                </li>
                <li>
                  <strong>Spot assets preserved:</strong> Spot holdings will not be sold and remain untouched in the exchange account.
                </li>
                <li>
                  <strong>Only the selected key is removed from this bot:</strong> This removes the chosen saved connection from the bot. A still-connected companion connection stays connected, and cleanup completion must be verified before it can be called Disconnected.
                </li>
                <li>
                  <strong>Exchange API keys are not revoked:</strong> The API key on the exchange remains valid. You must still log in to the exchange console to delete it separately if you wish to revoke it permanently.
                </li>
              </ul>
            </div>
          </div>
        </div>

        {lockReason && (
          <div role="status" className="rounded-card border border-app-warning/30 bg-app-warning/10 p-3 flex items-start gap-2">
            <AlertTriangle size={15} className="mt-0.5 shrink-0 text-app-warning" />
            <div>
              <strong>Disconnect is currently locked:</strong> {lockReason}. Resolve this condition, then refresh status.
            </div>
          </div>
        )}

        {error && (
          <div className="rounded-card border border-app-danger/30 bg-app-danger/10 p-3 text-app-danger flex items-start gap-2">
            <AlertTriangle size={15} className="mt-0.5 shrink-0" />
            <div>
              <strong>Error disconnecting exchange:</strong> {error}
            </div>
          </div>
        )}

        {/* Action buttons */}
        <div className="mt-6 flex flex-col-reverse justify-end gap-2.5 sm:flex-row pt-2 border-t border-app-border/70">
          <Button
            ref={cancelButtonRef}
            variant="secondary"
            onClick={() => onOpenChange(false)}
            disabled={isSubmitting}
            className="w-full sm:w-auto"
          >
            Cancel (Keep connection)
          </Button>

          <Button
            variant="danger"
            onClick={onConfirmDisconnect}
            disabled={isSubmitting || Boolean(lockReason)}
            className="w-full sm:w-auto font-bold gap-1.5"
          >
            {isSubmitting ? (
              <>
                <Loader2 size={14} className="animate-spin" />
                <span>Closing positions & disconnecting...</span>
              </>
            ) : (
              <>
                <Unlink size={14} />
                <span>Confirm close positions & disconnect</span>
              </>
            )}
          </Button>
        </div>
      </div>
    </Dialog>
  )
}
