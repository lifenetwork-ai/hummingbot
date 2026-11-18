import { Activity, AlertTriangle, ArrowDownUp, ChevronDown, Database } from 'lucide-react'
import type { OperationalDiagnostics, DiagnosticObservation, deriveProcessDiagnostics } from './diagnostics'
import { Badge } from '../../shared/components/Badge'
import { Surface } from '../../shared/components/Surface'

const OBSERVATION_LABELS: Record<string, string> = {
  fresh: 'Live',
  stale: 'Stale',
  unknown: 'Unknown',
  process: 'Process-reported',
}

const REDUCTION_REASON_LABELS: Record<string, string> = {
  reduction_price_not_profitable: 'Waiting for a maker price that covers the allocated opening and closing fees',
  reduction_basis_unavailable: 'Waiting for a verified remaining cost basis',
  reduction_fee_unavailable: 'Waiting for authenticated fee data',
  reduction_reserved: 'Waiting for existing orders and financial confirmation',
  reduction_dust: 'Below the venue minimum order size',
}

const TERMINAL_STAGE_LABELS: Record<string, string> = {
  order_read: 'reading orders',
  order_terminal: 'checking order completion',
  order_identity: 'matching order identity',
  fills_read: 'reading fills',
  fill_identity: 'matching fill identity',
  fill_coverage: 'checking fill coverage',
  order_recheck: 're-checking orders',
  recording: 'recording results',
}

function ObservationBadge({ observation }: { observation: DiagnosticObservation | 'process' }) {
  const tone = observation === 'fresh' ? 'success' : observation === 'process' ? 'brand' : observation === 'stale' ? 'warning' : 'neutral'
  return <Badge tone={tone} className="text-[10px]">{OBSERVATION_LABELS[observation] ?? observation}</Badge>
}

function formatTimestamp(value: string | null | undefined): string {
  if (!value) return '—'
  const parsed = Date.parse(value)
  return Number.isFinite(parsed) ? new Date(parsed).toLocaleString('en-US') : '—'
}

function ServicePhaseLabel({ phase, reasonCode }: { phase: string | null; reasonCode: string | null }) {
  if (phase === 'standby' && reasonCode === 'migration_pending') {
    return <><span className="font-bold text-app-text">Update waiting</span> — the previous process must finish before the update continues.</>
  }
  if (phase === 'migrating' && reasonCode === 'migration_in_progress') {
    return <><span className="font-bold text-app-warning">Updating</span> — a bounded database update step is in progress; trading readiness is off for this revision.</>
  }
  return <><span className="font-mono font-bold text-app-text">{phase ?? 'unknown'}</span>{reasonCode ? <> · <span className="font-mono">{reasonCode}</span></> : null}</>
}

/**
 * Operational diagnostics: service phase/incidents, optional perpetual
 * inventory-reduction evidence per market/side, and the Spot BUY
 * terminal-order verification wait. Evidence only — never a claim about
 * permissions, admission or live liquidity. Stale/unknown is explicit.
 */
export type ProcessDiagnostics = ReturnType<typeof deriveProcessDiagnostics>

export function OperationalDiagnosticsCard({ diagnostics, processDiagnostics, className }: {
  diagnostics: OperationalDiagnostics
  processDiagnostics?: ProcessDiagnostics
  className?: string
}) {
  const { reductions, outflow, service, terminalIncidents } = diagnostics
  const terminalIncidentIds = new Set(terminalIncidents.map((incident) => `${incident.scope}\0${incident.reason_code}\0${incident.first_seen_at}`))
  const otherIncidents = (service.evidence?.incidents ?? []).filter((incident) =>
    !terminalIncidentIds.has(`${incident.scope}\0${incident.reason_code}\0${incident.first_seen_at}`))
  return (
    <Surface className={className ?? 'p-4 md:p-5'} aria-label="Troubleshooting details">
      <details className="group/diagnostics">
        <summary className="flex min-h-11 cursor-pointer list-none flex-wrap items-center gap-2 rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-app-action [&::-webkit-details-marker]:hidden">
          <Activity size={16} className="text-app-action" aria-hidden="true" />
          <h3 className="text-sm font-bold text-app-text">Troubleshooting details</h3>
          <span className="text-[10px] text-app-muted">Supporting evidence — not trading permission or live liquidity</span>
          <ChevronDown size={16} className="ml-auto shrink-0 text-app-muted group-open/diagnostics:rotate-180" aria-hidden="true" />
        </summary>

        {/* Service phase & incidents */}
        <section className="mt-3" aria-label="Service health">
          {processDiagnostics && (
            <div className="mb-2 rounded-card border border-app-warning/30 bg-app-warning/5 p-2.5 text-xs">
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone="brand" className="text-[10px]">Process-reported</Badge>
                <span className="font-mono font-semibold text-app-text">{processDiagnostics.phase ?? 'unknown'}</span>
                {processDiagnostics.reasonCode && <span className="font-mono text-app-muted">{processDiagnostics.reasonCode}</span>}
              </div>
              <p className="mt-1 text-app-warning">
                The service process is responding. Persisted control state is unavailable; no current owner, session or account state is reported.
              </p>
              <p className="mt-1 font-mono text-[10px] text-app-muted">
                observed {formatTimestamp(processDiagnostics.observedAt)}
                {processDiagnostics.requestId && <> · request {processDiagnostics.requestId}</>}
              </p>
            </div>
          )}
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h4 className="flex items-center gap-1.5 text-xs font-bold uppercase tracking-wider text-app-muted">
              <Database size={13} aria-hidden="true" /> Service
            </h4>
            {service.evidence ? <ObservationBadge observation={service.observation} /> : <Badge tone="neutral" className="text-[10px]">No data</Badge>}
          </div>
          {!service.evidence && <p className="mt-1.5 text-xs text-app-muted">Service health is not available in the current status observation.</p>}
          {service.evidence && (
            <div className="mt-1.5 space-y-1 text-xs text-app-muted">
              <p><ServicePhaseLabel phase={service.evidence.phase} reasonCode={service.evidence.reason_code} /></p>
              <p className="font-mono text-[11px]">
                observed {formatTimestamp(service.evidence.observed_at)}
                {service.evidence.observer_age_seconds !== null && <> · observer age {service.evidence.observer_age_seconds}s</>}
              </p>
              {service.evidence.state_source === 'process' && (
                <p className="font-medium text-app-warning">
                  Persisted control state is unavailable — this is process-reported only; commands are not accepted without durable storage.
                </p>
              )}
            </div>
          )}
          {terminalIncidents.length > 0 && (
            <ul className="mt-2 space-y-1.5">
              {terminalIncidents.map((incident) => (
                <li key={`${incident.scope}-${incident.reason_code}-${incident.first_seen_at}`} className="rounded-card border border-app-border bg-app-surface-muted/30 p-2.5 text-xs">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge tone={incident.severity === 'error' ? 'danger' : 'warning'} className="text-[10px]">{incident.severity}</Badge>
                    <span className="font-mono text-app-text">{incident.reason_code}</span>
                  </div>
                  <p className="mt-1 text-app-muted">
                    Spot BUY terminal verification — {TERMINAL_STAGE_LABELS[incident.terminalStage] ?? incident.terminalStage}
                    {incident.operator_action_required && <span className="font-semibold text-app-warning"> · operator action required</span>}
                  </p>
                  {incident.detail && <p className="mt-1 text-app-muted">{incident.detail}</p>}
                  <p className="mt-1 font-mono text-[10px] text-app-muted">
                    first {formatTimestamp(incident.first_seen_at)} · last {formatTimestamp(incident.last_seen_at)}
                    {incident.retry_count > 0 && <> · retries {incident.retry_count}</>}
                    {incident.next_retry_at && <> · next retry {formatTimestamp(incident.next_retry_at)}</>}
                  </p>
                </li>
              ))}
            </ul>
          )}
          {otherIncidents.length > 0 && (
            <details className="mt-2 text-xs">
              <summary className="cursor-pointer text-app-muted">Other service incidents ({otherIncidents.length})</summary>
              <ul className="mt-1.5 space-y-1">
                {otherIncidents.map((incident) => (
                  <li key={`${incident.scope}-${incident.reason_code}-${incident.first_seen_at}`} className="rounded-card border border-app-border p-2">
                    <Badge tone={incident.severity === 'error' ? 'danger' : 'warning'} className="text-[10px]">{incident.severity}</Badge>{' '}
                    <span className="font-mono text-app-text">{incident.reason_code}</span> · <span className="text-app-muted">{incident.scope}</span> · <span className="text-app-muted">{incident.detail ?? ''}</span>
                  </li>
                ))}
              </ul>
          </details>
        )}
      </section>

      {/* Perpetual inventory reduction */}
      <section className="mt-4 border-t border-app-border/60 pt-3" aria-label="Perpetual inventory reduction">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h4 className="flex items-center gap-1.5 text-xs font-bold uppercase tracking-wider text-app-muted">
            <ArrowDownUp size={13} aria-hidden="true" /> Perpetual inventory reduction
          </h4>
          {reductions.length === 0 && <Badge tone="neutral" className="text-[10px]">Not reported</Badge>}
        </div>
        {reductions.length === 0 && (
          <p className="mt-1.5 text-xs text-app-muted">
            Inventory reduction is not reported for any market. The policy state cannot be determined from this observation.
          </p>
        )}
        {reductions.map((reduction) => (
          <div key={reduction.market} className="mt-2 rounded-card border border-app-border p-2.5">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="text-xs font-semibold text-app-text">{reduction.market}</span>
              <div className="flex items-center gap-2">
                {reduction.evidence.fee_ready === false && <Badge tone="warning" className="text-[10px]">Fees pending</Badge>}
                <ObservationBadge observation={reduction.observation} />
              </div>
            </div>
            <p className="mt-1 font-mono text-[10px] text-app-muted">
              observed {formatTimestamp(reduction.observedAt)}
              {reduction.observation !== 'fresh' && <span className="font-sans font-medium"> · evaluation expired or not current; it cannot imply an active quote</span>}
            </p>
            <div className="mt-1.5 grid gap-1.5 sm:grid-cols-2">
              {Object.entries(reduction.evidence.sides).map(([side, evidence]) => (
                <div key={side} className="rounded-card border border-app-border/70 bg-app-surface-muted/20 p-2 text-[11px]">
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-bold text-app-text">{side}</span>
                    <span className={evidence.effect === 'CLOSE' ? 'font-semibold text-app-success' : 'font-semibold text-app-muted'}>
                      {evidence.effect ?? 'effect unknown'}{evidence.effect ? ` · ${evidence.target_leg}` : ''}
                    </span>
                  </div>
                  <dl className="mt-1 space-y-0.5 font-mono text-[10px] text-app-muted">
                    <div className="flex justify-between gap-2"><dt>owned / venue</dt><dd className="text-app-text">{evidence.owned_base ?? '—'} / {evidence.venue_base}</dd></div>
                    <div className="flex justify-between gap-2"><dt>reserved / available</dt><dd className="text-app-text">{evidence.reserved_base} / {evidence.available_base}</dd></div>
                  </dl>
                  {evidence.reason_code && (
                    <p className="mt-1 text-[10px] text-app-muted">
                      {REDUCTION_REASON_LABELS[evidence.reason_code] ?? <span className="font-mono">{evidence.reason_code}</span>}
                    </p>
                  )}
                </div>
              ))}
            </div>
          </div>
        ))}
      </section>

      {/* Spot BUY terminal verification */}
      <section className="mt-4 border-t border-app-border/60 pt-3" aria-label="Spot BUY order verification">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h4 className="text-xs font-bold uppercase tracking-wider text-app-muted">Spot BUY verification</h4>
          <ObservationBadge observation={outflow.observation} />
        </div>
        {!outflow.evidence && <p className="mt-1.5 text-xs text-app-muted">Spot outflow policy is not reported in the current status.</p>}
        {outflow.evidence && (
          <div className="mt-1.5 text-xs">
            {outflow.waitingForTerminalVerification ? (
              <p className="flex items-start gap-1.5 font-medium text-app-text">
                <AlertTriangle size={14} className="mt-0.5 shrink-0 text-app-warning" aria-hidden="true" />
                {outflow.pendingTerminalOrders} Spot BUY {outflow.pendingTerminalOrders === 1 ? 'order is' : 'orders are'} waiting for completion verification.
                Reservations are retained — this is a verification wait, not proof that the spending cap is exhausted.
              </p>
            ) : outflow.observation === 'fresh' && outflow.pendingTerminalOrders === 0 ? (
              <p className="text-app-muted">No Spot BUY orders are waiting for completion verification.</p>
            ) : outflow.pendingTerminalOrders !== null ? (
              <p className="text-app-muted">
                Last recorded pending terminal orders: {outflow.pendingTerminalOrders}. Current count is unverified; do not infer whether orders remain.
              </p>
            ) : (
              <p className="text-app-muted">Spot BUY verification count is unknown in the current observation.</p>
            )}
            <p className="mt-1 font-mono text-[10px] text-app-muted">observed {formatTimestamp(outflow.evidence.observed_at)}</p>
          </div>
        )}
      </section>
      </details>
    </Surface>
  )
}
