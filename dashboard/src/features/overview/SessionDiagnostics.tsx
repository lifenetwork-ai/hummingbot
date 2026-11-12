import { ChevronDown, Server } from 'lucide-react'
import type { ObservationState, OverviewModel } from './model'
import { Badge } from '../../shared/components/Badge'
import { CopyableId } from '../../shared/components/CopyableId'
import { cn } from '../../shared/utils/cn'

export interface SessionDiagnosticsProps {
  overview: OverviewModel
  observation: ObservationState
  className?: string
}

/**
 * Low-frequency technical diagnostics (Session ID, exchange account, runtime owner,
 * operator) collapsed in an accordion — reduces clutter from the primary operational view.
 */
export function SessionDiagnostics({ overview, observation, className }: SessionDiagnosticsProps) {
  const status = overview.status.data
  const liquiditySessionId = overview.liquidity.data?.session_id ?? null
  const observedSessionId = status?.observed_session?.id ?? null
  const requestedSessionId = status?.session?.id ?? null
  const ownerAvailable = overview.task.runtime.ownerAvailable
  const operator = overview.task.operator

  return (
    <details className={cn('group rounded-card', className)}>
      <summary className="flex cursor-pointer select-none items-center justify-between text-[11px] text-app-muted hover:text-app-text">
        <span className="flex items-center gap-1.5 font-semibold">
          <Server size={12} className="text-app-action" />
          <span>Session Information & Runtime</span>
        </span>
        <ChevronDown size={13} className="transition-transform group-open:rotate-180" />
      </summary>
      <div className="mt-2 space-y-2 rounded-card bg-app-surface-muted/20 p-3 text-[11px]">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5">
          <span className="flex items-center gap-1 text-app-muted">
            Session ID:
            {observedSessionId ?? liquiditySessionId ?? requestedSessionId ? (
              <CopyableId
                id={(observedSessionId ?? liquiditySessionId ?? requestedSessionId) as string}
                copyLabel="Copy session ID"
              />
            ) : (
              <code className="font-mono text-app-text">—</code>
            )}
          </span>
          {requestedSessionId && (
            <span className="flex items-center gap-1 text-app-muted">
              Requested: <CopyableId id={requestedSessionId} copyLabel="Copy requested session ID" truncateLength={12} />
            </span>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <span className="text-app-muted">Runtime Owner:</span>
          <Badge tone={ownerAvailable ? 'success' : 'neutral'} className="text-[10px]">
            {ownerAvailable ? 'Available' : 'Unavailable'}
          </Badge>
          <span className="text-[10px] text-app-muted">(does not imply exchange is connected)</span>
        </div>

        {operator?.label && (
          <div className="flex items-center gap-1.5">
            <span className="text-app-muted">Operator:</span>
            <span className="font-medium text-amber-500">{operator.label}</span>
          </div>
        )}

        <p className="text-[10px] text-app-muted">
          Observation status: {observation} · does not automatically infer step transitions or bot stops when countdown reaches 0 (awaits backend confirmation via new observation).
        </p>
      </div>
    </details>
  )
}
