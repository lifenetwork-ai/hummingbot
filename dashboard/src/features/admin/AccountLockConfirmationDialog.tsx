import { AlertTriangle, CheckCircle2, Loader2, Lock, ShieldAlert, Unlock } from 'lucide-react'
import type { components } from '../../core/api/schema'
import { Badge } from '../../shared/components/Badge'
import { Button } from '../../shared/components/Button'
import { Dialog } from '../../shared/components/Dialog'

export interface AccountLockConfirmationDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  account: components['schemas']['AccountResponseOutput'] | null
  targetActive: boolean | null
  onConfirm: () => Promise<void> | void
  isSubmitting?: boolean
  error?: string | null
}

const ROLE_LABELS: Record<'admin' | 'write' | 'read', string> = {
  admin: 'Administrator (Admin)',
  write: 'Operator',
  read: 'Viewer',
}

export function AccountLockConfirmationDialog({
  open,
  onOpenChange,
  account,
  targetActive,
  onConfirm,
  isSubmitting = false,
  error = null,
}: AccountLockConfirmationDialogProps) {
  if (!account || targetActive === null) return null

  const isLocking = targetActive === false
  const isAdmin = account.role === 'admin'

  const title = isLocking
    ? isAdmin
      ? 'Confirm Suspending Administrator (Admin)'
      : 'Confirm Suspending Account'
    : 'Confirm Reactivating Account'

  const description = isLocking
    ? `Google account ${account.email || account.id} will be suspended from system access.`
    : `Google account ${account.email || account.id} will be unlocked to allow login.`

  return (
    <Dialog
      open={open}
      onClose={() => {
        if (!isSubmitting) onOpenChange(false)
      }}
      title={title}
      description={description}
      className="max-w-md"
    >
      <div className="space-y-4 text-xs text-app-text">
        {/* Account and Status Transition Info */}
        <div className="rounded-card border border-app-border bg-app-surface-muted/50 p-3.5 space-y-2">
          <div className="flex items-center justify-between text-app-muted text-[11px]">
            <span>Account:</span>
            <strong className="text-app-text font-mono">{account.email || account.id}</strong>
          </div>
          <div className="flex items-center justify-between border-t border-app-border/60 pt-2 text-app-muted text-[11px]">
            <span>Current role:</span>
            <Badge tone={isAdmin ? 'brand' : 'neutral'} className="text-[10px]">
              {ROLE_LABELS[account.role]}
            </Badge>
          </div>
          <div className="flex items-center justify-between border-t border-app-border/60 pt-2">
            <span className="text-app-muted text-[11px]">Active status:</span>
            <div className="flex items-center gap-2">
              <Badge tone={account.active ? 'success' : 'danger'} className="text-[10px]">
                {account.active ? 'Active' : 'Suspended'}
              </Badge>
              <span className="text-app-muted">→</span>
              <Badge tone={targetActive ? 'success' : 'danger'} className="text-[10px]">
                {targetActive ? 'Active' : 'Suspended'}
              </Badge>
            </div>
          </div>
        </div>

        {/* Warning if locking an account */}
        {isLocking && (
          <div className="rounded-card border border-app-danger/30 bg-app-danger/10 p-3.5 space-y-2">
            <div className="flex items-start gap-2.5">
              <ShieldAlert size={18} className="text-app-danger mt-0.5 shrink-0" />
              <div className="space-y-1">
                <h4 className="font-bold text-app-danger">
                  {isAdmin ? 'Warning: Suspending Administrator' : 'Notice on account suspension'}
                </h4>
                <p className="leading-relaxed text-app-muted">
                  Suspended accounts will not be able to log in or perform any actions in the system.
                </p>
                {isAdmin && (
                  <p className="text-[11px] text-app-muted">
                    Note: If this is the last active Google Administrator, the system will block this action via the Last Bound Admin guard.
                  </p>
                )}
              </div>
            </div>
          </div>
        )}

        {/* Notice if unlocking an account */}
        {!isLocking && (
          <div className="rounded-card border border-app-success/30 bg-app-success/10 p-3.5 space-y-1 text-app-text">
            <div className="flex items-start gap-2.5">
              <Unlock size={16} className="text-app-success mt-0.5 shrink-0" />
              <div>
                <h4 className="font-bold">Reactivate account</h4>
                <p className="leading-relaxed text-app-muted">
                  The account will be reactivated and will be able to log in with Google OAuth under its assigned permissions.
                </p>
              </div>
            </div>
          </div>
        )}

        {/* Error message */}
        {error && (
          <div className="rounded-card border border-app-danger/30 bg-app-danger/10 p-3 text-app-danger flex items-start gap-2">
            <AlertTriangle size={15} className="mt-0.5 shrink-0" />
            <div>
              <strong>Error updating status:</strong> {error}
            </div>
          </div>
        )}

        {/* Action Buttons */}
        <div className="mt-6 flex flex-col-reverse justify-end gap-2.5 sm:flex-row pt-2 border-t border-app-border/70">
          <Button
            variant="secondary"
            onClick={() => onOpenChange(false)}
            disabled={isSubmitting}
            className="w-full sm:w-auto"
          >
            Cancel
          </Button>

          <Button
            variant={isLocking ? 'danger' : 'primary'}
            onClick={onConfirm}
            disabled={isSubmitting}
            className="w-full sm:w-auto font-bold gap-1.5"
          >
            {isSubmitting ? (
              <>
                <Loader2 size={14} className="animate-spin" />
                <span>{isLocking ? 'Suspending...' : 'Reactivating...'}</span>
              </>
            ) : isLocking ? (
              <>
                <Lock size={14} />
                <span>Confirm suspend</span>
              </>
            ) : (
              <>
                <CheckCircle2 size={14} />
                <span>Confirm reactivate</span>
              </>
            )}
          </Button>
        </div>
      </div>
    </Dialog>
  )
}
