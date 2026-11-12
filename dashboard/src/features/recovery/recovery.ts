import type { ReadResponse } from '../../core/api/admin'
import type { components } from '../../core/api/schema'

export type RecoveryOverview = ReadResponse<'getRecovery'>
export type RecoveryOperation = components['schemas']['RecoveryOperationOutput']

export type RecoveryDecision = { allowed: boolean; reason?: string }

export function recoveryEntry(input: {
  permission: boolean
  overview: RecoveryOverview | undefined
  status: ReadResponse<'getRuntimeStatus'> | undefined
  statusFresh: boolean
  operationPending: boolean
}): RecoveryDecision {
  if (!input.permission) return { allowed: false, reason: 'permission_required' }
  if (!input.overview) return { allowed: false, reason: 'recovery_unavailable' }
  if (!input.overview.halted) return { allowed: false, reason: 'recovery_not_halted' }
  if (input.operationPending) return { allowed: false, reason: 'recovery_in_progress' }
  if (!input.statusFresh || !input.status) return { allowed: false, reason: 'status_unavailable' }
  const session = input.status.observed_session
  if (input.status.desired_state !== 'stopped' || input.status.acknowledged_version < input.status.control_version ||
      (session && session.status !== 'stopped' && session.status !== 'expired')) {
    return { allowed: false, reason: 'stop_acknowledgement_required' }
  }
  if (!input.overview.check_available) return { allowed: false, reason: 'check_unavailable' }
  return { allowed: true }
}

export function reviewRecovery(input: {
  permission: boolean
  overview: RecoveryOverview | undefined
  status: ReadResponse<'getRuntimeStatus'> | undefined
  statusFresh: boolean
  operation: RecoveryOperation | undefined
  now?: number
}): RecoveryDecision {
  const { operation } = input
  if (!input.permission) return { allowed: false, reason: 'permission_required' }
  if (!input.overview?.halted) return { allowed: false, reason: 'recovery_not_halted' }
  if (!input.statusFresh || !input.status || !input.overview.reset_available) return { allowed: false, reason: 'reset_unavailable' }
  const session = input.status.observed_session
  if (input.status.desired_state !== 'stopped' || input.status.acknowledged_version < input.status.control_version ||
      (session && session.status !== 'stopped' && session.status !== 'expired')) {
    return { allowed: false, reason: 'stop_acknowledgement_required' }
  }
  if (!operation || operation.action !== 'recovery_check' || operation.status !== 'succeeded' ||
      operation.stage !== 'review_required' || !operation.can_recover || !operation.confirmation_token || !operation.expires_at) {
    return { allowed: false, reason: 'recovery_check_not_reviewable' }
  }
  const expiresAt = Date.parse(operation.expires_at)
  if (!Number.isFinite(expiresAt) || expiresAt <= (input.now ?? Date.now())) return { allowed: false, reason: 'preview_expired' }
  if (operation.trading_state !== 'stopped') return { allowed: false, reason: 'trading_not_stopped' }
  return { allowed: true }
}

export function recoveryComplete(operation: RecoveryOperation | undefined): boolean {
  return operation?.action === 'recovery_reset' && operation.status === 'succeeded' && operation.stage === 'recovered'
}

export function recoveryOperationPending(operation: RecoveryOperation | undefined): boolean {
  return operation?.status === 'accepted' || operation?.status === 'applying'
}
