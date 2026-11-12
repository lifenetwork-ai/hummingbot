import { AlertOctagon, CheckCircle2, Clock, Info, Loader2, ShieldAlert, Wrench } from 'lucide-react'
import { useEffect, useState } from 'react'
import type { components } from '../../core/api/schema'
import type { RecoveryDecision } from '../recovery/recovery'
import { buildRecoveryComparison, type RecoveryComparisonModel } from '../recovery/comparison'
import { Badge } from '../../shared/components/Badge'
import { Button } from '../../shared/components/Button'
import { Surface } from '../../shared/components/Surface'
import { RecoveryResetDialog } from './RecoveryResetDialog'

export interface AdminRecoveryViewProps {
  recoverySummary?: components['schemas']['RecoveryOverviewOutput'] | null
  status?: components['schemas']['StatusResponseOutput'] | null
  activeOperation?: components['schemas']['RecoveryOperationOutput']
  checkDecision: RecoveryDecision
  reviewDecision: RecoveryDecision
  canRetryCheck?: boolean
  canRetryReset?: boolean
  isChecking?: boolean
  isResetting?: boolean
  checkError?: string
  resetError?: string
  onCheckRecovery: () => Promise<unknown>
  onRetryCheck: () => Promise<unknown>
  onResetRecovery: () => Promise<unknown>
  onRetryReset: () => Promise<unknown>
  onRefresh: () => Promise<unknown>
  comparison?: RecoveryComparisonModel | null
  hasAccess?: boolean
}

export function AdminRecoveryView({
  recoverySummary,
  status,
  activeOperation,
  checkDecision,
  reviewDecision,
  canRetryCheck = false,
  canRetryReset = false,
  isChecking = false,
  isResetting = false,
  checkError,
  resetError,
  onCheckRecovery,
  onRetryCheck,
  onResetRecovery,
  onRetryReset,
  onRefresh,
  comparison,
  hasAccess = true,
}: AdminRecoveryViewProps) {
  const [resetDialogOpen, setResetDialogOpen] = useState(false)
  const [now, setNow] = useState(Date.now)

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [])

  // 1. Permission Denied Entry (T-22)
  if (hasAccess === false || checkDecision.reason === 'permission_required') {
    return (
      <Surface className="p-6 text-center">
        <div className="mx-auto grid h-12 w-12 place-items-center rounded-full bg-amber-500/10 text-amber-500">
          <ShieldAlert size={24} />
        </div>
        <h3 className="mt-4 font-heavy text-lg font-bold text-app-text">
          Administrator Access Required (manage_recovery required)
        </h3>
        <p className="mx-auto mt-2 max-w-md text-xs text-app-muted leading-relaxed">
          You do not have permission to manage incident recovery. Please contact a system Administrator (Admin) to inspect or resolve the HALT state.
        </p>
      </Surface>
    )
  }

  const halted = recoverySummary?.halted === true
  const stage = activeOperation?.stage ?? 'initial'
  const recovered = activeOperation?.action === 'recovery_reset' && activeOperation.status === 'succeeded' && stage === 'recovered'
  const blockers = activeOperation?.blockers ?? recoverySummary?.blockers ?? []
  const proposedChanges = activeOperation?.proposed_changes ?? []

  // Check succeeded but can_recover=false (T-23)
  const isCheckSucceededButCannotRecover = activeOperation?.action === 'recovery_check' &&
    activeOperation.status === 'succeeded' &&
    stage !== 'review_required'

  // Comparison model with all 7 fields (T-23, T-25)
  const comparisonItems = comparison !== undefined && comparison !== null
    ? comparison
    : buildRecoveryComparison(proposedChanges)

  const isCheckUnknown = canRetryCheck
  const isResetUnknown = canRetryReset

  const expiresAt = Date.parse(activeOperation?.expires_at ?? '')
  const previewValid = Number.isFinite(expiresAt) && expiresAt > now

  const handleConfirmReset = async () => {
    try {
      await onResetRecovery()
      setResetDialogOpen(false)
    } catch {
      // Keep confirmation open; error is supplied by the controller owner.
    }
  }

  // No recovery needed state
  if (recoverySummary && !halted && !activeOperation) {
    return (
      <Surface className="p-6 text-center">
        <div className="mx-auto grid h-12 w-12 place-items-center rounded-full bg-emerald-500/10 text-emerald-500">
          <CheckCircle2 size={24} />
        </div>
        <h3 className="mt-4 font-heavy text-lg font-bold text-app-text">
          No Incident Recovery Required
        </h3>
        <p className="mx-auto mt-2 max-w-md text-xs text-app-muted">
          Backend is not in a HALT state requiring recovery. Recovery operations only restore ledger state; they do not automatically start trading.
        </p>
        <Button variant="secondary" className="mt-4 h-8 px-3 text-xs" onClick={() => { void onRefresh() }}>
          Refresh status
        </Button>
      </Surface>
    )
  }

  return (
    <Surface className="p-5 md:p-6">
      <div className="flex flex-col justify-between gap-3 border-b border-app-border/80 pb-4 sm:flex-row sm:items-center">
        <div>
          <div className="flex items-center gap-2">
            <ShieldAlert size={20} className="text-app-danger" />
            <h3 className="font-heavy text-lg font-bold text-app-text">
              HALT Incident Recovery
            </h3>
          </div>
          <p className="mt-1 text-xs text-app-muted">
            Check recovery feasibility, review ledger before/after comparison, and confirm Reset with safe versioning and receipt tracking.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Badge tone={recovered ? 'success' : halted ? 'danger' : 'neutral'} className="text-xs">
            {recovered ? 'Recovered' : halted ? 'HALT' : 'Checking'}
          </Badge>
          {activeOperation && (
            <Badge tone="brand" className="text-xs">
              {activeOperation.status} · {stage}
            </Badge>
          )}
        </div>
      </div>

      {/* Recovered success banner */}
      {recovered && (
        <div className="mt-5 rounded-card border border-emerald-500/30 bg-emerald-500/10 p-4 text-xs text-app-text">
          <div className="flex items-start gap-3">
            <CheckCircle2 size={20} className="mt-0.5 shrink-0 text-emerald-500" />
            <div>
              <h4 className="font-bold text-sm">Recovery Succeeded</h4>
              <p className="mt-1 leading-relaxed">
                Trading remains in STOPPED state. The system will not automatically Start. Operators must review market conditions and send an explicit Start command if authorized.
              </p>
            </div>
          </div>
        </div>
      )}

      {/* Check succeeded but can_recover=false notice */}
      {isCheckSucceededButCannotRecover && (
        <div className="mt-5 rounded-card border border-amber-500/30 bg-amber-500/10 p-4 text-xs text-app-text">
          <div className="flex items-start gap-3">
            <AlertOctagon size={20} className="mt-0.5 shrink-0 text-amber-500" />
            <div>
              <h4 className="font-bold text-sm text-amber-500">Check completed but cannot recover (can_recover: false)</h4>
              <p className="mt-1 leading-relaxed">
                Recovery check completed, but backend determined conditions are not met to proceed with Reset. Please review the blockers below before retrying.
              </p>
            </div>
          </div>
        </div>
      )}

      {/* Blockers list */}
      {blockers.length > 0 && (
        <div className="mt-5 rounded-card border border-app-danger/30 bg-app-danger/5 p-4">
          <h4 className="flex items-center gap-1.5 text-xs font-bold uppercase tracking-wider text-app-danger">
            <AlertOctagon size={15} /> Recovery Blockers
          </h4>
          <ul className="mt-2.5 space-y-2 text-xs text-app-text">
            {blockers.map((blocker, index) => (
              <li key={`${blocker.code}-${index}`} className="rounded-button border border-app-border/70 bg-app-surface p-2.5">
                <span className="font-mono font-bold text-app-danger">{blocker.code}</span>
                <p className="mt-1 text-app-muted">{blocker.detail}</p>
                <p className="mt-1 text-app-muted">Next action: {blocker.next_action}</p>
              </li>
            ))}
          </ul>
        </div>
      )}

      {activeOperation?.detail && (
        <p className="mt-4 text-xs text-app-muted">{activeOperation.detail}</p>
      )}

      {/* Verification details from server */}
      {activeOperation?.verification && (
        <div className="mt-4 space-y-1">
          <span className="text-[11px] font-semibold text-app-text">Server Verification Results:</span>
          <pre className="max-h-48 overflow-auto rounded-card border border-app-border bg-app-surface-muted/40 p-3 text-[11px] text-app-muted font-mono">
            {JSON.stringify(activeOperation.verification, null, 2)}
          </pre>
        </div>
      )}

      {/* Server preview expiry */}
      {activeOperation?.expires_at && (
        <p className="mt-3 flex items-center gap-1.5 text-xs text-app-warning">
          <Clock size={14} />
          <span>
            Preview expiry: {new Date(activeOperation.expires_at).toLocaleString('en-US')} ·{' '}
            {previewValid ? `${Math.ceil((expiresAt - now) / 1000)}s remaining` : 'Expired — run new Check'}
          </span>
        </p>
      )}

      {/* Complete 7-field Before/After Comparison Table (T-23, T-25) */}
      {stage === 'review_required' && comparisonItems.length > 0 && (
        <div className="mt-5 space-y-4">
          <div className="flex items-center justify-between border-b border-app-border/60 pb-2">
            <h4 className="font-heavy text-sm font-bold text-app-text">
              Proposed Ledger Repairs Comparison (Before / After)
            </h4>
            <span className="text-[10px] text-app-muted">All 7 audit fields</span>
          </div>

          {comparisonItems.map((repair) => (
            <div key={repair.id} className="rounded-card border border-app-border overflow-hidden">
              <div className="bg-app-surface-muted/60 p-3 flex flex-wrap items-center justify-between gap-2 border-b border-app-border">
                <div>
                  <span className="font-bold text-app-text font-mono text-sm">{repair.pair}</span>
                  <span className="ml-2 text-xs text-app-muted uppercase font-mono">({repair.connector})</span>
                  <span className="ml-2 text-xs text-app-muted">· Controller: <code className="text-app-text">{repair.controllerId}</code></span>
                </div>
                <div className="text-[11px] text-app-muted">
                  Valuation Basis: <strong className="text-app-text font-mono">{repair.valuationBasis}</strong>
                </div>
              </div>

              <table className="w-full text-left text-xs">
                <thead>
                  <tr className="border-b border-app-border bg-app-surface-muted/30 text-app-muted font-semibold">
                    <th className="p-2.5 w-1/3">Field</th>
                    <th className="p-2.5 w-1/3">Before Recovery</th>
                    <th className="p-2.5 w-1/3">Proposed After</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-app-border/60 font-mono">
                  {repair.rows.map((row) => (
                    <tr key={row.field} className="hover:bg-app-surface-muted/20">
                      <td className="p-2.5 font-sans font-medium text-app-text">
                        {row.label} ({row.field})
                      </td>
                      <td className="p-2.5 text-app-muted">
                        {row.before ?? '—'} <span className="text-[10px] text-app-muted/80">{row.unit}</span>
                      </td>
                      <td className="p-2.5 font-bold text-app-action">
                        {row.after ?? '—'} <span className="text-[10px] text-app-muted/80">{row.unit}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>

              {/* Logic explanation notice */}
              <div className="bg-app-surface-muted/30 p-2.5 border-t border-app-border/60 text-[11px] text-app-muted space-y-1">
                <div className="flex items-start gap-1.5">
                  <Info size={13} className="shrink-0 mt-0.5 text-app-action" />
                  <p className="leading-relaxed">
                    <strong>Logic Explanation: </strong>
                    {repair.explanation}
                  </p>
                </div>
                <p className="text-[10px] text-app-muted pl-4">
                  Note: Reconstructed PnL of 0 does not imply refund or live PnL = 0. Live positions and actual PnL are separate exchange data feeds, not mixed into this ledger comparison.
                </p>
              </div>
            </div>
          ))}
        </div>
      )}

      {stage === 'review_required' && comparisonItems.length === 0 && (
        <p className="mt-4 text-xs text-app-muted">Receipt does not contain proposed changes to display.</p>
      )}

      {(checkError || resetError) && (
        <div role="alert" className="mt-4 rounded-card border border-app-danger/30 bg-app-danger/10 p-3 text-xs text-app-danger">
          {checkError ?? resetError}
        </div>
      )}

      {status && (
        <p className="mt-3 text-xs text-app-muted">
          Runtime observation: desired state {status.desired_state}, control version v{status.control_version}, acknowledged v{status.acknowledged_version}. Observed at: {status.observation?.observed_at ?? 'unknown'}.
        </p>
      )}

      {/* Action Buttons */}
      <div className="mt-6 flex flex-wrap items-center justify-between gap-3 border-t border-app-border/70 pt-4">
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" onClick={() => { void onRefresh() }} className="h-8 px-3 text-xs">
            Refresh status
          </Button>
          {isCheckUnknown && (
            <Button variant="secondary" onClick={() => { void onRetryCheck() }} disabled={isChecking || isResetting} className="h-8 px-3 text-xs">
              Retry same Check
            </Button>
          )}
          <Button
            variant="secondary"
            onClick={() => { void onCheckRecovery() }}
            disabled={!checkDecision.allowed || isChecking || isResetting}
            title={checkDecision.reason ?? (checkDecision.allowed ? undefined : 'Check unavailable')}
            className="h-8 px-3 text-xs gap-1.5 font-bold"
          >
            {isChecking ? (
              <>
                <Loader2 size={14} className="animate-spin" />
                <span>Checking…</span>
              </>
            ) : (
              <>
                <Wrench size={14} />
                <span>Check recovery</span>
              </>
            )}
          </Button>
        </div>

        <div className="flex gap-2">
          {isResetUnknown && (
            <Button variant="secondary" onClick={() => { void onRetryReset() }} disabled={isChecking || isResetting} className="h-8 px-3 text-xs">
              Retry same Reset
            </Button>
          )}
          {stage === 'review_required' && (
            <Button
              variant="danger"
              disabled={!reviewDecision.allowed || isChecking || isResetting || !previewValid}
              onClick={() => setResetDialogOpen(true)}
              className="h-8 px-3 text-xs font-bold"
            >
              {isResetting ? (
                <>
                  <Loader2 size={14} className="animate-spin" />
                  <span>Recovering…</span>
                </>
              ) : (
                <span>Confirm Reset</span>
              )}
            </Button>
          )}
        </div>
      </div>

      <RecoveryResetDialog
        open={resetDialogOpen}
        isResetting={isResetting}
        onOpenChange={(open) => setResetDialogOpen(open)}
        onConfirmReset={() => { void handleConfirmReset() }}
      />
    </Surface>
  )
}
