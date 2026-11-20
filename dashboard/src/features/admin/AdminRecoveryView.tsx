import { AlertOctagon, CheckCircle2, Clock, Info, Loader2, RefreshCw, ShieldAlert, Wrench } from 'lucide-react'
import { useState } from 'react'
import type { RecoveryViewModel } from '../recovery/view-model'
import { Badge } from '../../shared/components/Badge'
import { Button } from '../../shared/components/Button'
import { Surface } from '../../shared/components/Surface'
import { RecoveryResetDialog } from './RecoveryResetDialog'

export interface AdminRecoveryViewProps {
  model: RecoveryViewModel
  onCheckRecovery: () => Promise<unknown>
  onRetryCheck: () => Promise<unknown>
  onResetRecovery: () => Promise<unknown>
  onRetryReset: () => Promise<unknown>
  onRefresh: () => Promise<unknown>
}

export function AdminRecoveryView({
  onCheckRecovery,
  onRetryCheck,
  onResetRecovery,
  onRetryReset,
  onRefresh,
  model,
}: AdminRecoveryViewProps) {
  const [resetDialogOpen, setResetDialogOpen] = useState(false)
  const { recoverySummary, status, activeOperation, checkDecision, reviewDecision, canRetryCheck, canRetryReset,
    isChecking, isResetting, checkError, resetError, hasAccess, presentation, verification, comparisonItems,
    halted, stage, recovered, blockers, proposedChanges, automaticEngaged, isCheckSucceededButCannotRecover,
    previewValid, previewSecondsRemaining } = {
    recoverySummary: model.summary,
    status: model.status,
    activeOperation: model.operation,
    checkDecision: model.checkDecision,
    reviewDecision: model.reviewDecision,
    canRetryCheck: model.canRetryCheck,
    canRetryReset: model.canRetryReset,
    isChecking: model.isChecking,
    isResetting: model.isResetting,
    checkError: model.checkError,
    resetError: model.resetError,
    hasAccess: model.hasAccess,
    presentation: model.presentation,
    verification: model.verification,
    comparisonItems: model.comparison,
    halted: model.halted,
    stage: model.stage,
    recovered: model.recovered,
    blockers: model.blockers,
    proposedChanges: model.proposedChanges,
    automaticEngaged: model.automaticEngaged,
    isCheckSucceededButCannotRecover: model.isCheckSucceededButCannotRecover,
    previewValid: model.previewValid,
    previewSecondsRemaining: model.previewSecondsRemaining,
  }

  // 1. Permission Denied Entry (T-22)
  if (!hasAccess || checkDecision.reason === 'permission_required') {
    return (
      <Surface className="p-6 text-center">
        <div className="mx-auto grid h-12 w-12 place-items-center rounded-full bg-app-warning/10 text-app-warning">
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

  const automatic = presentation.automatic

  // Check succeeded but can_recover=false (T-23)
  const isCheckUnknown = canRetryCheck
  const isResetUnknown = canRetryReset

  const handleConfirmReset = async () => {
    try {
      await onResetRecovery()
      setResetDialogOpen(false)
    } catch {
      // Keep confirmation open; error is supplied by the controller owner.
    }
  }

  // No recovery needed state — only when a fresh observation confirms the HALT flag is
  // clear AND automatic recovery is idle. Automatic work or an unknown/stale observation
  // must never be reported as "no recovery needed".
  if (recoverySummary && !halted && !activeOperation && presentation.showNoRecoveryNeeded) {
    return (
      <Surface className="p-6 text-center">
        <div className="mx-auto grid h-12 w-12 place-items-center rounded-full bg-app-success/10 text-app-success">
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
              {presentation.waitingForShutdown ? status?.operator?.label || 'Verifying shutdown' : halted ? 'HALT Incident Recovery' : 'Incident Recovery'}
            </h3>
          </div>
          <p className="mt-1 text-xs text-app-muted">
            Check recovery feasibility, review ledger before/after comparison, and confirm Reset with safe versioning and receipt tracking.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Badge tone={recovered ? 'success' : halted ? 'danger' : 'neutral'} className="text-xs">
            {recovered ? 'Recovered' : presentation.waitingForShutdown ? 'Waiting' : halted ? 'HALT' : 'Observation'}
          </Badge>
          {activeOperation && (
            <Badge tone="brand" className="text-xs">
              {activeOperation.status} · {stage}
            </Badge>
          )}
        </div>
      </div>

      {/* Automatic recovery observation — shown whenever automatic work, scoped trading
          or a retained reservation is engaged, even while halted=false. */}
      {automatic && automaticEngaged && (
        <div className="mt-5 rounded-card border border-app-action/30 bg-app-action/5 p-4 text-xs text-app-text">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h4 className="flex items-center gap-1.5 text-xs font-bold uppercase tracking-wider text-app-action">
              <RefreshCw size={14} /> Automatic order recovery {presentation.active ? 'in progress' : 'reported'}
            </h4>
            <div className="flex flex-wrap gap-2">
              <Badge tone={presentation.active ? 'brand' : 'neutral'} className="text-[11px]">{automatic.phase}</Badge>
              <Badge tone={presentation.observation === 'fresh' ? 'success' : presentation.observation === 'stale' ? 'warning' : 'neutral'} className="text-[11px]">
                {presentation.observation === 'fresh' ? 'Live observation' : presentation.observation === 'stale' ? 'Stale observation' : 'Observation unknown'}
              </Badge>
            </div>
          </div>
          <p className="mt-1.5 leading-relaxed">
            Manual Check/Recover address the reviewed HALT procedure only and do not start trading, cancel orders or close exposure.
            Automatic reconciliation verifies uncertain orders and rechecks current admission conditions before allowing trading.
            {presentation.operatorRequired && <span className="font-semibold text-app-warning"> Operator investigation is required — contact support; do not discard the uncertain reservation.</span>}
          </p>
          <dl className="mt-2 grid gap-1 font-mono text-[11px] text-app-muted sm:grid-cols-2">
            {presentation.reasonCode !== null && <div>reason_code: <span className="text-app-text">{presentation.reasonCode}</span></div>}
            {presentation.orderId !== null && <div>order_id: <span className="text-app-text">{presentation.orderId}</span></div>}
            {presentation.startedAt !== null && <div>started_at: <span className="text-app-text">{new Date(presentation.startedAt).toLocaleString('en-US')}</span></div>}
            {presentation.lastProgressAt !== null && <div>last_progress_at: <span className="text-app-text">{new Date(presentation.lastProgressAt).toLocaleString('en-US')}</span></div>}
            {presentation.isolated && <div>admission_mode: <span className="text-app-text">{presentation.admissionMode ?? 'paused'}</span> ({presentation.tradingStopped ? 'trading remains stopped' : presentation.scopedSellOnly ? 'only funded Spot SELLs may continue' : 'current admission unverified'})</div>}
            {(presentation.quarantinedOrders ?? 0) > 0 && <div>quarantined_orders: <span className="text-app-text">{presentation.quarantinedOrders}</span></div>}
            {presentation.reservedBase && Object.keys(presentation.reservedBase).length > 0 && (
              <div>reserved_base: <span className="text-app-text">{Object.entries(presentation.reservedBase).map(([asset, amount]) => `${amount} ${asset}`).join(' · ')}</span></div>
            )}
            {presentation.reservedQuoteWorstCase !== null && <div>reserved_quote_worst_case: <span className="text-app-text">{presentation.reservedQuoteWorstCase} USDT</span></div>}
            {presentation.coldRecoverySupported !== null && <div>cold_recovery_supported: <span className="text-app-text">{String(presentation.coldRecoverySupported)}</span></div>}
          </dl>
        </div>
      )}

      {presentation.waitingForShutdown && <p className="mt-4 text-sm text-app-muted">{status?.operator?.detail || 'Wait for shutdown verification. Manual Check and Recover are unavailable.'}</p>}

      {/* Observation not fresh while a HALT is reported */}
      {halted && presentation.observation !== 'fresh' && (
        <p className="mt-4 text-xs font-medium text-app-warning">
          Recovery observation is {presentation.observation === 'stale' ? 'stale' : 'unknown'} — this panel reflects the last known state, not a live conclusion.
        </p>
      )}

      {/* Recovered success banner */}
      {recovered && (
        <div className="mt-5 rounded-card border border-app-success/30 bg-app-success/10 p-4 text-xs text-app-text">
          <div className="flex items-start gap-3">
            <CheckCircle2 size={20} className="mt-0.5 shrink-0 text-app-success" />
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
        <div className="mt-5 rounded-card border border-app-warning/30 bg-app-warning/10 p-4 text-xs text-app-text">
          <div className="flex items-start gap-3">
            <AlertOctagon size={20} className="mt-0.5 shrink-0 text-app-warning" />
            <div>
              <h4 className="font-bold text-sm text-app-warning">Check completed but cannot recover (can_recover: false)</h4>
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
                {blocker.code === 'recovery_risk_stop' && (
                  <p className="mt-1 text-[11px] font-medium text-app-warning">
                    A valid drawdown breach is a risk stop, not checkpoint corruption. Recover stays disabled — this API does not
                    reset risk limits, PnL peaks or stop latches.
                  </p>
                )}
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

      {/* Verification details from server, presented per the server-selected proof scope */}
      {activeOperation?.verification && (
        <div className="mt-4 space-y-1.5">
          <span className="text-[11px] font-semibold text-app-text">Server Verification Results:</span>
          <div className="rounded-card border border-app-border bg-app-surface-muted/40 p-3 text-xs">
            <p className="text-app-text">
              Proof scope:{' '}
              <span className="font-mono font-semibold">
                {verification.scope ?? 'unknown'}
              </span>
              {verification.scope === 'verified_checkpoint' && (
                <span className="block mt-1 text-app-muted">
                  Verified checkpoint — financial rows, remaining cost basis and risk checkpoints are preserved as-is.
                </span>
              )}
              {verification.scope === 'bounded_complete_local_history_and_dedicated_account' && (
                <span className="block mt-1 text-app-muted">
                  Bounded complete local history on a dedicated account. Review the check outcome and blockers to determine whether verification completed.
                </span>
              )}
            </p>
            <dl className="mt-2 grid gap-1 font-mono text-[11px] text-app-muted sm:grid-cols-2">
              <div>orders_checked: <span className="text-app-text">{verification.ordersChecked ?? '—'}</span></div>
              <div>fills_checked: <span className="text-app-text">{verification.fillsChecked ?? '—'}</span></div>
            </dl>
            {verification.ordersCheckedMeaning === 'no_historical_per_order_reads' && (
              <p className="mt-1.5 text-[11px] text-app-muted">
                orders_checked = 0 means no historical per-order REST reads were needed on this route — the exchange account is still
                verified against the checkpoint journal. It does not mean the account was left unchecked.
              </p>
            )}
          </div>
        </div>
      )}

      {/* Server preview expiry */}
      {activeOperation?.expires_at && (
        <p className="mt-3 flex items-center gap-1.5 text-xs text-app-warning">
          <Clock size={14} />
          <span>
            Preview expiry: {new Date(activeOperation.expires_at).toLocaleString('en-US')} ·{' '}
            {previewValid ? `${previewSecondsRemaining}s remaining` : 'Expired — run new Check'}
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
        verification.checkpointPreserved ? (
          <div className="mt-4 rounded-card border border-app-success/30 bg-app-success/5 p-3 text-xs text-app-text">
            <h4 className="font-bold text-sm">No ledger changes proposed — this is expected on the checkpoint route</h4>
            <p className="mt-1 text-app-muted">
              The verified-checkpoint scope preserves financial rows, remaining cost basis and risk checkpoints. An empty
              proposed-change list is a valid review outcome, not a missing preview.
            </p>
          </div>
        ) : (
          <p className="mt-4 text-xs text-app-muted">Receipt does not contain proposed changes to display.</p>
        )
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
            <Button variant="secondary" onClick={() => { void onRetryCheck() }} disabled={presentation.waitingForShutdown || isChecking || isResetting} className="h-8 px-3 text-xs">
              Retry same Check
            </Button>
          )}
          <Button
            variant="secondary"
            onClick={() => { void onCheckRecovery() }}
            disabled={presentation.waitingForShutdown || !checkDecision.allowed || isChecking || isResetting}
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
            <Button variant="secondary" onClick={() => { void onRetryReset() }} disabled={presentation.waitingForShutdown || isChecking || isResetting} className="h-8 px-3 text-xs">
              Retry same Reset
            </Button>
          )}
          {stage === 'review_required' && (
            <Button
              variant="danger"
              disabled={presentation.waitingForShutdown || !reviewDecision.allowed || isChecking || isResetting || !previewValid}
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
        checkpointPreserved={verification.checkpointPreserved && proposedChanges.length === 0}
        onOpenChange={(open) => setResetDialogOpen(open)}
        onConfirmReset={() => { void handleConfirmReset() }}
      />
    </Surface>
  )
}
