import { AlertTriangle, CheckCircle2, Loader2, Wrench } from 'lucide-react'
import { Button } from '../../shared/components/Button'
import { Dialog } from '../../shared/components/Dialog'

export interface RecoveryResetDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onConfirmReset: () => Promise<void> | void
  isResetting?: boolean
  error?: string | null
}

export function RecoveryResetDialog({
  open,
  onOpenChange,
  onConfirmReset,
  isResetting = false,
  error = null,
}: RecoveryResetDialogProps) {
  return (
    <Dialog
      open={open}
      onClose={() => {
        if (!isResetting) onOpenChange(false)
      }}
      title="Confirm Recovery Reset"
      description="Apply reviewed changes to bring the bot out of HALT state."
      className="max-w-md"
    >
      <div className="space-y-4 text-xs text-app-text">
        <div className="rounded-card border border-app-warning/30 bg-app-warning/10 p-3.5 space-y-2">
          <div className="flex items-start gap-2.5">
            <Wrench size={18} className="text-app-warning mt-0.5 shrink-0" />
            <div>
              <h4 className="font-bold text-app-text">Apply reviewed checkpoint modifications</h4>
              <p className="mt-1 leading-relaxed text-app-muted">
                The system will synchronize position checkpoints and return the bot to a <strong>Safe Stopped (Stopped)</strong> state. The bot will NOT automatically resume trading.
              </p>
            </div>
          </div>
        </div>

        {error && (
          <div className="rounded-card border border-app-danger/30 bg-app-danger/10 p-3 text-app-danger flex items-start gap-2">
            <AlertTriangle size={15} className="mt-0.5 shrink-0" />
            <div>
              <strong>Error applying Reset:</strong> {error}
            </div>
          </div>
        )}

        <div className="mt-6 flex flex-col-reverse justify-end gap-2.5 sm:flex-row pt-2 border-t border-app-border/70">
          <Button
            variant="secondary"
            onClick={() => onOpenChange(false)}
            disabled={isResetting}
            className="w-full sm:w-auto"
          >
            Cancel
          </Button>

          <Button
            variant="primary"
            onClick={onConfirmReset}
            disabled={isResetting}
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
