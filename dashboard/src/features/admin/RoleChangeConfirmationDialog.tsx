import { AlertTriangle, CheckCircle2, Loader2, ShieldAlert } from 'lucide-react'
import type { components } from '../../core/api/schema'
import { Badge } from '../../shared/components/Badge'
import { Button } from '../../shared/components/Button'
import { Dialog } from '../../shared/components/Dialog'

export interface RoleChangeConfirmationDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  account: components['schemas']['AccountResponseOutput'] | null
  targetRole: 'admin' | 'write' | 'read' | null
  onConfirm: () => Promise<void> | void
  isSubmitting?: boolean
  error?: string | null
}

const ROLE_LABELS: Record<'admin' | 'write' | 'read', string> = {
  admin: 'Administrator (Admin)',
  write: 'Operator',
  read: 'Viewer',
}

export function RoleChangeConfirmationDialog({
  open,
  onOpenChange,
  account,
  targetRole,
  onConfirm,
  isSubmitting = false,
  error = null,
}: RoleChangeConfirmationDialogProps) {
  if (!account || !targetRole) return null

  const isCurrentAdmin = account.role === 'admin'
  const isTargetAdmin = targetRole === 'admin'

  const title = isCurrentAdmin
    ? 'Confirm Changing Administrator Role (Admin)'
    : 'Confirm Role Change'

  return (
    <Dialog
      open={open}
      onClose={() => {
        if (!isSubmitting) onOpenChange(false)
      }}
      title={title}
      className="max-w-md"
    >
      <div className="space-y-4 text-xs text-app-text">
        {/* Account and Role Transition Info */}
        <div className="rounded-card border border-app-border bg-app-surface-muted/50 p-3.5 space-y-2">
          <div className="flex items-center justify-between text-app-muted text-[11px]">
            <span>Account:</span>
            <strong className="text-app-text font-mono">{account.email || account.id}</strong>
          </div>
          <div className="flex items-center justify-between border-t border-app-border/60 pt-2">
            <span className="text-app-muted text-[11px]">Role:</span>
            <div className="flex items-center gap-2">
              <Badge tone={account.role === 'admin' ? 'brand' : 'neutral'} className="text-[10px]">
                {ROLE_LABELS[account.role]}
              </Badge>
              <span className="text-app-muted">→</span>
              <Badge tone={targetRole === 'admin' ? 'brand' : 'neutral'} className="text-[10px]">
                {ROLE_LABELS[targetRole]}
              </Badge>
            </div>
          </div>
        </div>

        {/* Warning if demoting an Admin */}
        {isCurrentAdmin && !isTargetAdmin && (
          <div className="rounded-card border border-app-warning/40 bg-app-warning/10 p-3.5 space-y-2">
            <div className="flex items-start gap-2.5">
              <ShieldAlert size={18} className="text-app-warning mt-0.5 shrink-0" />
              <div className="space-y-1">
                <h4 className="font-bold text-app-text">Warning: Administrator Demotion</h4>
                <p className="leading-relaxed text-app-muted">
                  This account will lose administrative privileges, and will no longer be able to manage other accounts or modify sensitive configurations.
                </p>
                <p className="text-[11px] text-app-muted">
                  Note: If this is the last active Google Administrator, the system will block this action via the Last Bound Admin guard.
                </p>
              </div>
            </div>
          </div>
        )}

        {/* Notice if promoting to Admin */}
        {!isCurrentAdmin && isTargetAdmin && (
          <div className="rounded-card border border-app-action/30 bg-app-action/10 p-3.5 space-y-1 text-app-text">
            <h4 className="font-bold">Grant Full Administrator Privileges (Admin)</h4>
            <p className="leading-relaxed text-app-muted">
              This account will be granted full administrative access, including managing exchange connections, account authorizations, and incident recovery interventions.
            </p>
          </div>
        )}

        {/* Error message */}
        {error && (
          <div className="rounded-card border border-app-danger/30 bg-app-danger/10 p-3 text-app-danger flex items-start gap-2">
            <AlertTriangle size={15} className="mt-0.5 shrink-0" />
            <div>
              <strong>Error changing role:</strong> {error}
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
            variant={isCurrentAdmin && !isTargetAdmin ? 'danger' : 'primary'}
            onClick={onConfirm}
            disabled={isSubmitting}
            className="w-full sm:w-auto font-bold gap-1.5"
          >
            {isSubmitting ? (
              <>
                <Loader2 size={14} className="animate-spin" />
                <span>Updating role...</span>
              </>
            ) : (
              <>
                <CheckCircle2 size={14} />
                <span>Confirm change</span>
              </>
            )}
          </Button>
        </div>
      </div>
    </Dialog>
  )
}
