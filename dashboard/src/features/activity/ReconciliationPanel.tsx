import { AlertCircle, CheckCircle2, Clock, GitCompare, Loader2, RefreshCw } from 'lucide-react'
import type { components } from '../../core/api/schema'
import { Badge } from '../../shared/components/Badge'
import { Button } from '../../shared/components/Button'
import { Surface } from '../../shared/components/Surface'

export interface ReconciliationPanelProps {
  reconciliation?: components['schemas']['ReconciliationResponseOutput'] | null
  receipt?: components['schemas']['ReconciliationReceiptOutput'] | null
  canRequest?: boolean
  isChecking?: boolean
  onRequestCheck?: () => void
}

const STATE_CONFIG: Record<
  string,
  { label: string; tone: 'success' | 'danger' | 'warning' | 'neutral'; description: string }
> = {
  matched: {
    label: 'Fully Matched',
    tone: 'success',
    description: 'Data matches within inspected scope and timestamp; does not prove entire exchange account is reconciled.',
  },
  mismatch: {
    label: 'Discrepancy Detected',
    tone: 'danger',
    description: 'Discrepancy detected between bot-recorded orders/positions and actual state on OKX exchange.',
  },
  incomplete: {
    label: 'Incomplete',
    tone: 'warning',
    description: 'Reconciliation did not complete the full scope or exceeded query pagination limit.',
  },
  unavailable: {
    label: 'Unavailable',
    tone: 'neutral',
    description: 'Unable to connect to OKX exchange for reconciliation at this time.',
  },
  not_checked: {
    label: 'Not Checked',
    tone: 'neutral',
    description: 'No reconciliation request has been executed in the current session.',
  },
  checking: {
    label: 'Reconciling',
    tone: 'warning',
    description: 'Submitting request and comparing reconciliation data with OKX exchange…',
  },
}

export function ReconciliationPanel({
  reconciliation,
  receipt,
  isChecking = false,
  canRequest = false,
  onRequestCheck,
}: ReconciliationPanelProps) {
  const report = receipt?.report || reconciliation?.report
  const state = isChecking ? 'checking' : (report?.state || 'not_checked')
  const stateMeta = STATE_CONFIG[state] || STATE_CONFIG.not_checked
  const markets = report?.markets ?? []

  return (
    <Surface className="p-5 md:p-6">
      <div className="flex flex-col justify-between gap-3 border-b border-app-border/80 pb-4 sm:flex-row sm:items-center">
        <div>
          <div className="flex items-center gap-2">
            <GitCompare size={18} className="text-app-action" />
            <h3 className="font-heavy text-lg font-bold text-app-text">
              OKX Data Reconciliation
            </h3>
          </div>
          <p className="mt-1 text-xs text-app-muted">
            Independent comparison between internal bot records and actual state on OKX exchange.
          </p>
        </div>

        <div className="flex items-center gap-2.5">
          <Badge tone={stateMeta.tone} className="px-3 py-1 text-xs font-semibold">
            {stateMeta.label}
          </Badge>
          {onRequestCheck && (
            <Button
              variant="secondary"
              onClick={onRequestCheck}
              disabled={isChecking || !canRequest}
              className="h-8 px-3 text-xs gap-1.5 font-bold"
            >
              {isChecking ? (
                <>
                  <Loader2 size={13} className="animate-spin" />
                  <span>Reconciling…</span>
                </>
              ) : (
                <>
                  <RefreshCw size={13} />
                  <span>Request Reconciliation</span>
                </>
              )}
            </Button>
          )}
        </div>
      </div>

      {/* State banner */}
      <div className="mt-4 rounded-card border border-app-border/80 bg-app-surface-muted/40 p-4">
        <div className="flex items-start gap-3">
          {state === 'matched' ? (
            <CheckCircle2 size={18} className="text-emerald-500 mt-0.5 shrink-0" />
          ) : state === 'mismatch' ? (
            <AlertCircle size={18} className="text-app-danger mt-0.5 shrink-0" />
          ) : (
            <Clock size={18} className="text-app-muted mt-0.5 shrink-0" />
          )}
          <div>
            <h4 className="text-xs font-bold text-app-text">
              Status: {stateMeta.label}
            </h4>
            <p className="mt-0.5 text-xs text-app-muted leading-relaxed">
              {stateMeta.description}
            </p>
          </div>
        </div>

        <div className="mt-3 flex flex-wrap items-center justify-between border-t border-app-border/40 pt-2.5 text-[11px] text-app-muted">
          <span>Scope: <strong className="text-app-text">bot_owned_orders_and_perpetual_exposure</strong></span>
          <span>Completed at: <span className="font-mono text-app-text">{report?.finished_at ? new Date(report.finished_at).toLocaleString() : '—'}</span></span>
        </div>
      </div>

      {/* Markets checked table */}
      {markets.length > 0 && (
        <div className="mt-5 overflow-hidden rounded-card border border-app-border">
          <div className="bg-app-surface-muted/60 px-3 py-2 text-xs font-bold text-app-text border-b border-app-border">
            Inspected Markets Detail ({markets.length})
          </div>
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="border-b border-app-border bg-app-surface-muted/30 text-app-muted font-semibold">
                <th className="p-2.5">Market</th>
                <th className="p-2.5">Status</th>
                <th className="p-2.5">Orders Checked</th>
                <th className="p-2.5">Fills Checked</th>
                <th className="p-2.5">Positions</th>
                <th className="p-2.5">Discrepancy Reason</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-app-border/60">
              {markets.map((m) => (
                <tr key={m.market}>
                  <td className="p-2.5 font-bold uppercase text-app-text">{m.market}</td>
                  <td className="p-2.5">
                    <Badge tone={m.state === 'matched' ? 'success' : 'danger'} className="text-[10px]">
                      {m.state === 'matched' ? 'Match' : 'Mismatch'}
                    </Badge>
                  </td>
                  <td className="p-2.5 font-mono">{m.orders_checked}</td>
                  <td className="p-2.5 font-mono">{m.fills_checked}</td>
                  <td className="p-2.5 font-mono">{m.positions_checked}</td>
                  <td className="p-2.5 text-app-muted font-mono text-[11px]">
                    {m.reasons.length > 0 ? m.reasons.join(', ') : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Operator notice */}
      <div className="mt-4 rounded-button bg-app-surface-muted/60 p-3 text-xs text-app-muted">
        <strong>Notice: </strong>
        Reconciliation is a read-only operation. The system does not automatically modify, cancel, or intervene in exchange positions upon detecting discrepancies.
      </div>
    </Surface>
  )
}
