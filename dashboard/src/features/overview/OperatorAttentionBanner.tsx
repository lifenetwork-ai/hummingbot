import type { components } from '../../core/api/schema'
import type { ObservationState } from './model'
import { Banner } from '../../shared/components/Banner'
import { localizeSystemMessage } from '../../shared/utils/formatters'

export interface OperatorAttentionBannerProps {
  operator?: components['schemas']['OperatorStatusOutput'] | null
  recovery?: components['schemas']['RecoverySummaryOutput'] | null
  observation?: ObservationState
  onAction?: (action: string) => void
  className?: string
}

const NEXT_ACTION_LABELS: Record<string, string> = {
  wait: 'Waiting for system conditions to synchronize...',
  contact_support: 'Contact technical support',
  start: 'Start new session',
  monitor: 'Continue monitoring activity',
  check_recovery: 'Check incident recovery (Admin)',
  none: 'No recommended action',
}

export function OperatorAttentionBanner({
  operator,
  recovery,
  observation,
  onAction,
  className,
}: OperatorAttentionBannerProps) {
  const isHalted = recovery?.halted === true
  const isStale = observation !== undefined && observation !== 'fresh'

  // If neither recovery nor operator is provided, nothing to display
  if (!isHalted && !operator) {
    return null
  }

  const operatorState = operator?.state ?? 'normal'
  const isUnavailable = operatorState === 'unavailable'
  const isNeedsAttention = operatorState === 'needs_attention'
  const isWaiting = operatorState === 'waiting'

  const variant: 'error' | 'warning' | 'info' | 'success' = isHalted || isUnavailable
    ? 'error'
    : isNeedsAttention
      ? 'warning'
      : isWaiting
        ? 'info'
        : 'info'

  const title = isHalted
    ? 'System is in EMERGENCY HALT state'
    : (operator?.label || (isWaiting ? 'Waiting for operational conditions' : isNeedsAttention ? 'Operational warning requires attention' : 'Operational status'))

  const rawDetail = isHalted
    ? (recovery?.detail || operator?.detail || 'Backend reports HALT; inspect root cause and recovery prerequisites.')
    : (operator?.detail || 'System state confirmed by controller.')
  const detail = localizeSystemMessage(rawDetail)

  const nextAction = (isHalted ? recovery?.next_action : operator?.next_action) || 'monitor'
  const canNavigate = Boolean(!isStale && onAction && ['check_recovery', 'start'].includes(nextAction))
  const actionLabel = NEXT_ACTION_LABELS[nextAction] || 'Take action now'

  return (
    <div className={className ?? 'mb-6'}>
      <Banner
        variant={variant}
        title={title}
        description={
          <div className="space-y-1.5">
            <p className="text-sm">{detail}</p>
            {isStale && (
              <p className="text-xs text-amber-500 font-medium">
                Observation data is not current (stale/unknown/session mismatch) · do not use as definitive conclusion.
              </p>
            )}
            {!canNavigate && nextAction && nextAction !== 'none' && (
              <p className="mt-1 text-xs font-medium opacity-90">
                Recommended next step: {NEXT_ACTION_LABELS[nextAction] || nextAction}
              </p>
            )}
          </div>
        }
        action={
          canNavigate && onAction
            ? {
                label: actionLabel,
                onClick: () => onAction(nextAction),
              }
            : undefined
        }
      />
    </div>
  )
}
