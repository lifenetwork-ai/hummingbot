import type { ReadResponse } from '../../core/api/admin'
import type { components } from '../../core/api/schema'
import type { RecoveryDecision, RecoveryOverview, RecoveryOperation } from './recovery'
import { buildRecoveryComparison } from './comparison'
import { deriveRecoveryPresentation, deriveRecoveryVerification } from './presentation'
import { recoveryComplete } from './recovery'

export type UncertainOrdersViewModel = {
  ids: readonly string[]
  validationError: string | null
  proposed: components['schemas']['RecoveryUncertainOrderRepairOutput'][]
  repaired: components['schemas']['RecoveryUncertainOrderRepairOutput'][]
  completionVerified: boolean
  reviewAvailable: boolean
  selectionLocked: boolean
}

export type RecoveryViewModel = {
  uncertainOrders?: UncertainOrdersViewModel
  hasAccess: boolean
  summary?: RecoveryOverview | null
  status?: ReadResponse<'getRuntimeStatus'> | null
  operation?: RecoveryOperation
  presentation: ReturnType<typeof deriveRecoveryPresentation>
  verification: ReturnType<typeof deriveRecoveryVerification>
  comparison: ReturnType<typeof buildRecoveryComparison>
  checkDecision: RecoveryDecision
  reviewDecision: RecoveryDecision
  canRetryCheck: boolean
  canRetryReset: boolean
  isChecking: boolean
  isResetting: boolean
  checkError?: string
  resetError?: string
  halted: boolean
  stage: string
  recovered: boolean
  blockers: components['schemas']['RecoveryBlockerOutput'][]
  proposedChanges: components['schemas']['RecoveryPositionRepairOutput'][]
  automaticEngaged: boolean
  isCheckSucceededButCannotRecover: boolean
  previewValid: boolean
  previewSecondsRemaining: number
}

export function deriveRecoveryViewModel(input: {
  uncertainOrders?: UncertainOrdersViewModel
  permission: boolean
  summary?: RecoveryOverview | null
  status?: ReadResponse<'getRuntimeStatus'> | null
  operation?: RecoveryOperation
  checkDecision: RecoveryDecision
  reviewDecision: RecoveryDecision
  canRetryCheck: boolean
  canRetryReset: boolean
  isChecking: boolean
  isResetting: boolean
  checkError?: string
  resetError?: string
  fresh: boolean
  now: number
}): RecoveryViewModel {
  const { operation, summary, status } = input
  const presentation = deriveRecoveryPresentation(status ?? undefined, input.fresh, input.now)
  const automatic = presentation.automatic
  const stage = operation?.stage ?? 'initial'
  const proposedChanges = operation?.proposed_changes ?? []
  const expiresAt = Date.parse(operation?.expires_at ?? '')
  const previewValid = Number.isFinite(expiresAt) && expiresAt > input.now
  return {
    uncertainOrders: input.uncertainOrders,
    hasAccess: input.permission,
    summary,
    status,
    operation,
    presentation,
    verification: deriveRecoveryVerification(operation),
    comparison: buildRecoveryComparison(proposedChanges),
    checkDecision: input.checkDecision,
    reviewDecision: input.reviewDecision,
    canRetryCheck: input.canRetryCheck,
    canRetryReset: input.canRetryReset,
    isChecking: input.isChecking,
    isResetting: input.isResetting,
    checkError: input.checkError,
    resetError: input.resetError,
    halted: summary?.halted === true,
    stage,
    recovered: recoveryComplete(operation) && (!input.uncertainOrders?.ids.length || input.uncertainOrders.completionVerified),
    blockers: operation?.blockers ?? summary?.blockers ?? [],
    proposedChanges,
    automaticEngaged: automatic !== null && (presentation.active || presentation.isolated ||
      (presentation.quarantinedOrders ?? 0) > 0 || automatic.phase === 'manual_required' ||
      automatic.phase === 'reconciling' || automatic.phase === 'bootstrapping_recovery' ||
      automatic.phase === 'waiting_for_readiness' || automatic.phase === 'settled_stopped' ||
      Object.keys(presentation.reservedBase ?? {}).length > 0 ||
      (presentation.reservedQuoteWorstCase !== null && presentation.reservedQuoteWorstCase !== '0')),
    isCheckSucceededButCannotRecover: operation?.action === 'recovery_check' &&
      operation.status === 'succeeded' && stage !== 'review_required',
    previewValid,
    previewSecondsRemaining: previewValid ? Math.ceil((expiresAt - input.now) / 1000) : 0,
  }
}
