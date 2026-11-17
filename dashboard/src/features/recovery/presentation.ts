import type { ReadResponse } from '../../core/api/admin'
import type { components } from '../../core/api/schema'
import type { RecoveryOperation } from './recovery'

type Observation = 'fresh' | 'stale' | 'unknown'

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null
}

function numeric(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function epochMilliseconds(value: unknown): number | null {
  const seconds = numeric(value)
  return seconds === null ? null : seconds * 1000
}

export function deriveRecoveryPresentation(
  status: ReadResponse<'getRuntimeStatus'> | undefined,
  fresh: boolean,
  now = Date.now(),
) {
  const recovery = status?.recovery
  const automatic = status?.automatic_recovery ?? null
  const statusObservation = status?.observation
  const observation: Observation = !status ? 'unknown'
    : !fresh || status.owner_available === false || statusObservation?.stale === true || statusObservation?.session_matches === false || automatic?.stale
      ? 'stale'
      : status.owner_available !== true || !statusObservation?.observed_at || statusObservation.session_matches !== true
        ? 'unknown'
        : 'fresh'
  const activePhases = ['reconciling', 'bootstrapping_recovery', 'waiting_for_readiness']
  const active = observation === 'fresh' && automatic !== null && activePhases.includes(automatic.phase)
  const phase = automatic?.phase ?? null
  const waitingForShutdown = Boolean(recovery?.halted && recovery.next_action === 'wait')
  const showManualHalt = Boolean(recovery?.halted && !waitingForShutdown)
  const halted = recovery?.halted === true
  const showNoRecoveryNeeded = observation === 'fresh' && recovery?.halted === false &&
    (automatic === null || automatic.phase === 'idle')
  const retryAtSeconds = automatic ? numeric(automatic.next_retry_at) : null

  return {
    automatic,
    phase,
    active,
    observation,
    showManualHalt,
    waitingForShutdown,
    showNoRecoveryNeeded,
    retryAt: active && retryAtSeconds !== null && retryAtSeconds * 1000 > now
      ? retryAtSeconds * 1000
      : null,
    isolated: automatic?.isolated ?? false,
    scopedSellOnly: observation === 'fresh' && active && status?.desired_state === 'running' &&
      automatic?.isolated === true && automatic.admission_mode === 'funded_spot_sell',
    tradingStopped: status?.desired_state === 'stopped' || phase === 'settled_stopped',
    admissionMode: automatic?.admission_mode ?? null,
    quarantinedOrders: automatic?.quarantined_orders ?? null,
    reservedBase: automatic?.reserved_base ?? null,
    reservedQuoteWorstCase: automatic?.reserved_quote_worst_case ?? null,
    operatorRequired: automatic?.operator_required ?? false,
    reasonCode: automatic?.reason_code ?? null,
    startedAt: epochMilliseconds(automatic?.started_at),
    lastProgressAt: epochMilliseconds(automatic?.last_progress_at),
    orderId: automatic?.order_id ?? null,
    coldRecoverySupported: automatic?.cold_recovery_supported ?? null,
    halted,
  }
}

export function deriveRecoveryVerification(operation: RecoveryOperation | undefined) {
  const verification = record(operation?.verification)
  const scope = typeof verification?.scope === 'string' ? verification.scope : null
  const ordersChecked = numeric(verification?.orders_checked)
  const fillsChecked = numeric(verification?.fills_checked)
  const proposedChanges: components['schemas']['RecoveryPositionRepairOutput'][] = operation?.proposed_changes ?? []

  return {
    scope,
    checkpointPreserved: scope === 'verified_checkpoint',
    ordersChecked,
    fillsChecked,
    ordersCheckedMeaning: scope === 'verified_checkpoint' && ordersChecked === 0
      ? 'no_historical_per_order_reads' as const
      : null,
    proposedChanges,
    riskStopBlockers: (operation?.blockers ?? []).filter((blocker) => blocker.code === 'recovery_risk_stop'),
  }
}
