import { AlertTriangle, RefreshCw, ShieldCheck } from 'lucide-react'
import type { deriveRecoveryPresentation } from '../recovery/presentation'
import { Badge } from '../../shared/components/Badge'
import { Surface } from '../../shared/components/Surface'
import { parseAmount } from './parseAmount'
import { formatUsdtForDisplay } from './formatNumbers'

export type RecoveryPresentation = ReturnType<typeof deriveRecoveryPresentation>

const PHASE_LABELS: Record<string, { title: string; detail: string }> = {
  idle: { title: 'No automatic recovery pending', detail: 'Ordinary guard and session admission still apply.' },
  reconciling: {
    title: 'Verifying an uncertain order outcome',
    detail: 'New shared-account risk is paused while the order result is verified. Stop stays available.',
  },
  bootstrapping_recovery: {
    title: 'Replacement owner is restoring recorded effects',
    detail: 'A replacement engine is restoring journaled effects before trading can resume.',
  },
  waiting_for_readiness: {
    title: 'Order proof complete — waiting for readiness',
    detail: 'Verification finished; the system is waiting for current market and risk conditions.',
  },
  settled_stopped: {
    title: 'Reservation kept after stop',
    detail: 'Proof cannot override Stop, expiry or a control change. The reservation is retained, not rejected or erased.',
  },
  manual_required: {
    title: 'Conflicting evidence — reviewed recovery required',
    detail: 'Evidence conflicts and needs the reviewed recovery procedure. Contact technical support.',
  },
}

const OBSERVATION_LABELS: Record<string, string> = {
  fresh: 'Live observation',
  stale: 'Stale observation',
  unknown: 'Observation unknown',
}

function formatTime(value: number | null): string | null {
  return value !== null ? new Date(value).toLocaleString('en-US') : null
}

/**
 * Automatic order reconciliation + scoped recovery presentation.
 * Renders only while there is something to report: active automatic work,
 * degraded scoped trading, or a retained reservation. Never claims "no
 * recovery needed" while automatic recovery is operating.
 */
export function AutomaticRecoveryCard({ presentation, className }: { presentation: RecoveryPresentation; className?: string }) {
  const { automatic, phase, observation, isolated, quarantinedOrders, reservedBase, reservedQuoteWorstCase, operatorRequired, reasonCode, startedAt, lastProgressAt, retryAt, orderId } = presentation
  if (!automatic) return null

  const reservedBaseEntries = Object.entries(reservedBase ?? {})
  // '0' (nothing reserved) and null/missing (not reported) are distinct from a real reservation.
  const reservedQuoteValue = parseAmount(reservedQuoteWorstCase ?? null)
  const hasReservedQuote = reservedQuoteValue !== null && reservedQuoteValue > 0
  const reservedQuote = reservedQuoteValue !== null ? formatUsdtForDisplay(reservedQuoteWorstCase) : null
  const hasReserved = isolated || (quarantinedOrders ?? 0) > 0 || reservedBaseEntries.length > 0 || hasReservedQuote
  const hasWork = phase !== null && phase !== 'idle'
  if (!hasWork && !hasReserved) return null

  const phaseLabel = phase !== null ? PHASE_LABELS[phase] : undefined
  const active = presentation.active

  return (
    <Surface className={className ?? 'p-4 md:p-5'} aria-label="Automatic order recovery">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="flex items-center gap-2">
          {active ? <RefreshCw size={16} className="animate-spin text-app-action" aria-hidden="true" /> : <ShieldCheck size={16} className="text-app-muted" aria-hidden="true" />}
          <h3 className="text-sm font-bold text-app-text">Automatic order recovery</h3>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={operatorRequired ? 'warning' : active ? 'brand' : 'neutral'} className="text-[11px]">
            {phaseLabel?.title ?? phase ?? 'phase unknown'}
          </Badge>
          <Badge tone={observation === 'fresh' ? 'success' : observation === 'stale' ? 'warning' : 'neutral'} className="text-[11px]">
            {OBSERVATION_LABELS[observation] ?? observation}
          </Badge>
        </div>
      </div>

      {observation !== 'fresh' && (
        <p className="mt-2 text-xs font-medium text-app-warning">
          {observation === 'stale'
            ? 'This recovery status is not current (stale) — treat it as the last known state, not a live conclusion.'
            : 'Recovery status is unknown — no status observation is available yet.'}
        </p>
      )}

      {phaseLabel && <p className="mt-2 text-xs text-app-muted">{phaseLabel.detail}</p>}

      {operatorRequired && (
        <p className="mt-2 flex items-start gap-1.5 text-xs font-semibold text-app-warning">
          <AlertTriangle size={14} className="mt-0.5 shrink-0" aria-hidden="true" />
          Needs operator investigation — contact support. Do not discard the reservation; the order outcome is still uncertain.
        </p>
      )}

      {isolated && (
        <div className="mt-3 rounded-card border border-app-warning/30 bg-app-warning/5 p-3">
          <h4 className="text-xs font-bold uppercase tracking-wider text-app-warning">{presentation.scopedSellOnly ? 'Degraded trading — scoped recovery active' : 'Scoped order uncertainty retained'}</h4>
          <p className="mt-1 text-xs text-app-text">
            {presentation.tradingStopped ? 'Trading remains stopped. The uncertain order and its reservation are retained.'
              : presentation.scopedSellOnly ? 'Only funded Spot cash SELLs may continue. New BUYs and every Perpetual change stay paused until the uncertain order is verified. This is not two-sided quoting and not completed reconciliation.'
                : 'Scoped recovery was reported. Current trading admission is unverified; this observation grants no trading permission.'}
          </p>
        </div>
      )}

      {(hasReserved) && (
        <dl className="mt-3 grid gap-2 text-xs sm:grid-cols-2">
          <div className="rounded-card border border-app-border bg-app-surface-muted/30 p-2.5">
            <dt className="text-app-muted">Orders held for verification</dt>
            <dd className="mt-0.5 font-mono text-sm font-bold text-app-text">{quarantinedOrders ?? '—'}</dd>
            <p className="mt-1 text-[10px] text-app-muted">Held back from new risk, not cancelled or lost.</p>
          </div>
          <div className="rounded-card border border-app-border bg-app-surface-muted/30 p-2.5">
            <dt className="text-app-muted">Capital reserved (worst case)</dt>
            <dd className="mt-0.5 font-mono text-sm font-bold text-app-text" title={reservedQuoteWorstCase ?? undefined}>
              {reservedQuote !== null ? `≈ ${reservedQuote} USDT` : '—'}
            </dd>
            {reservedBaseEntries.length > 0 && (
              <p className="mt-1 font-mono text-[10px] text-app-muted">
                {reservedBaseEntries.map(([asset, amount]) => `${amount} ${asset}`).join(' · ')}
              </p>
            )}
            <p className="mt-1 text-[10px] text-app-muted">Reserved until the order outcome is verified — not spendable, not lost.</p>
          </div>
        </dl>
      )}

      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-app-muted">
        {startedAt !== null && <span>Started: <span className="font-mono text-app-text">{formatTime(startedAt) ?? '—'}</span></span>}
        {lastProgressAt !== null && <span>Last progress: <span className="font-mono text-app-text">{formatTime(lastProgressAt) ?? '—'}</span></span>}
        {retryAt !== null && <span>Next automatic retry: <span className="font-mono text-app-text">{formatTime(retryAt) ?? '—'}</span></span>}
        {reasonCode !== null && <span>Reason code: <span className="font-mono text-app-text">{reasonCode}</span></span>}
        {orderId !== null && <span>Order: <span className="font-mono text-app-text">{orderId}</span></span>}
      </div>
    </Surface>
  )
}
