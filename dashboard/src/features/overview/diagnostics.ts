import type { ReadResponse } from '../../core/api/admin'
import { ApiError } from '../../core/api/client'

export type DiagnosticObservation = 'fresh' | 'stale' | 'unknown'
type Status = ReadResponse<'getRuntimeStatus'>
type Reduction = NonNullable<NonNullable<Status['markets']>[number]['inventory_reduction']>
type Incident = NonNullable<NonNullable<Status['service']>['incidents']>[number]

const TERMINAL_STAGES = new Set([
  'order_read', 'order_terminal', 'order_identity', 'fills_read', 'fill_identity',
  'fill_coverage', 'order_recheck', 'recording',
])

/** A process-only 503 never supplies an owner, control version or account state. */
export function deriveProcessDiagnostics(error: Error | null) {
  const service = error instanceof ApiError && error.status === 503 ? error.body?.service : null
  if (!service || typeof service !== 'object' || Array.isArray(service)) return null
  const value = service as Record<string, unknown>
  if (value.state_source !== 'process' || value.persisted_state_available !== false) return null
  return {
    observation: 'process' as const,
    persistedStateAvailable: false as const,
    phase: typeof value.phase === 'string' ? value.phase : null,
    reasonCode: typeof value.reason_code === 'string' ? value.reason_code : null,
    observedAt: typeof value.observed_at === 'string' ? value.observed_at : null,
    requestId: error instanceof ApiError ? error.requestId ?? null : null,
  }
}

function isFreshObservation(observedAt: string | null | undefined, stale: boolean, now: number, maxAge = Infinity): DiagnosticObservation {
  if (stale) return 'stale'
  const timestamp = Date.parse(observedAt ?? '')
  if (!Number.isFinite(timestamp) || timestamp > now) return 'unknown'
  return now - timestamp <= maxAge ? 'fresh' : 'stale'
}

function freshOwnerContext(status: Status | undefined, fresh: boolean): boolean {
  return Boolean(fresh && status?.owner_available === true && status.observation?.stale === false &&
    status.observation.session_matches === true)
}

function terminalStage(reasonCode: string): string | null {
  const prefix = 'spot_outflow_terminal_'
  if (!reasonCode.startsWith(prefix)) return null
  const stage = reasonCode.slice(prefix.length)
  return TERMINAL_STAGES.has(stage) ? stage : null
}

export type OperationalDiagnostics = {
  reductions: Array<{
    market: string
    observation: DiagnosticObservation
    observedAt: string | null
    evidence: Reduction
  }>
  outflow: {
    observation: DiagnosticObservation
    pendingTerminalOrders: number | null
    waitingForTerminalVerification: boolean
    evidence: Status['spot_outflow']
  }
  terminalIncidents: Array<Incident & { terminalStage: string }>
  service: {
    observation: DiagnosticObservation | 'process'
    evidence: Status['service']
  }
}

/** Maps current status evidence without inferring permissions or exchange liquidity. */
export function deriveOperationalDiagnostics(
  status: Status | undefined,
  fresh: boolean,
  now = Date.now(),
): OperationalDiagnostics {
  const ownerFresh = freshOwnerContext(status, fresh)
  const reductions = (status?.markets ?? []).flatMap((market) => {
    const evidence = market.inventory_reduction
    if (!evidence) return []
    const sourceObservation = isFreshObservation(evidence.observed_at, evidence.stale, now, 5000)
    return [{
      market: market.market,
      observation: !ownerFresh ? 'unknown' as const : sourceObservation,
      observedAt: evidence.observed_at,
      evidence,
    }]
  })

  const outflowEvidence = status?.spot_outflow ?? null
  const outflowSourceObservation = outflowEvidence
    ? isFreshObservation(outflowEvidence.observed_at, outflowEvidence.stale || !outflowEvidence.ready, now)
    : 'unknown'
  const outflowObservation = ownerFresh ? outflowSourceObservation : 'unknown'
  const pendingTerminalOrders = outflowEvidence?.pending_terminal_orders ?? null
  const outflow = {
    observation: outflowObservation,
    pendingTerminalOrders,
    waitingForTerminalVerification: outflowObservation === 'fresh' && pendingTerminalOrders !== null && pendingTerminalOrders > 0,
    evidence: outflowEvidence,
  }

  const serviceEvidence = status?.service ?? null
  const serviceObservation = serviceEvidence?.state_source === 'process'
    ? 'process' as const
    : serviceEvidence
      ? isFreshObservation(serviceEvidence.observed_at, serviceEvidence.stale || !fresh, now)
      : 'unknown' as const
  const terminalIncidents = (serviceEvidence?.incidents ?? []).flatMap((incident) => {
    const stage = terminalStage(incident.reason_code)
    return stage ? [{ ...incident, terminalStage: stage }] : []
  })

  return {
    reductions,
    outflow,
    terminalIncidents,
    service: { observation: serviceObservation, evidence: serviceEvidence },
  }
}
