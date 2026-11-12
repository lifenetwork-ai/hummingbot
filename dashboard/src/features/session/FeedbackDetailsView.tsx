import { useEffect, useState } from 'react'
import { AlertCircle, ChevronDown, ChevronRight, HelpCircle, RefreshCw, RotateCcw } from 'lucide-react'
import type { ApiFieldError } from '../../core/api/client'
import type { OperationFeedback, RetryGuidance } from '../../core/api/operation-feedback'
import { Badge } from '../../shared/components/Badge'
import { Button } from '../../shared/components/Button'
import { CopyableId } from '../../shared/components/CopyableId'
import { cn } from '../../shared/utils/cn'

export interface FeedbackDetailsViewProps {
  feedback?: OperationFeedback | null
  globalFields?: ApiFieldError[]
  title?: string
  onRetry?: () => void
  onRefresh?: () => void
  onReview?: () => void
  className?: string
}

const RETRY_GUIDANCE_DESCRIPTIONS: Record<RetryGuidance, string> = {
  fix_request: 'Inspect and adjust form fields according to backend requirements.',
  refresh_state: 'Session or account state has changed; refresh state before resubmitting.',
  same_request: 'Request can be safely retried with the same parameters and idempotency key.',
  wait: 'System is in cooldown; please wait before retrying.',
  reconnect: 'Log in again to re-verify identity and permissions; do not resubmit mutation directly.',
  contact_support: 'Error requires intervention from administrators or engineering team.',
  none: 'Automatic resubmission is not recommended for this request.',
}

export function FeedbackDetailsView({
  feedback,
  globalFields = [],
  title = 'Operation Feedback & Diagnostics',
  onRetry,
  onRefresh,
  onReview,
  className,
}: FeedbackDetailsViewProps) {
  const [showDiagnostics, setShowDiagnostics] = useState(false)
  const [now, setNow] = useState(Date.now)
  useEffect(() => {
    if (!feedback?.retryAt || feedback.retryAt <= Date.now()) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [feedback?.retryAt])

  if (!feedback && globalFields.length === 0) {
    return null
  }

  const retry = feedback?.retry ?? 'none'
  const retryDescription = RETRY_GUIDANCE_DESCRIPTIONS[retry]
  const isOutcomeUnknown = feedback?.outcomeUnknown === true
  const hasRequestId = Boolean(feedback?.requestId)

  return (
    <div className={cn('rounded-card border border-app-warning/40 bg-app-warning/5 p-4 text-xs space-y-3', className)}>
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-app-warning/20 pb-2.5">
        <div className="flex items-center gap-2">
          <AlertCircle size={16} className="text-app-warning" />
          <h4 className="font-heavy text-xs font-bold uppercase tracking-wider text-app-text">
            {title}
          </h4>
        </div>
        <div className="flex items-center gap-1.5">
          {feedback?.code && (
            <Badge tone="warning" className="text-[10px] font-mono">
              Code: {feedback.code}
            </Badge>
          )}
          {isOutcomeUnknown && (
            <Badge tone="danger" className="text-[10px] font-bold">
              Outcome Unknown
            </Badge>
          )}
        </div>
      </div>

      {/* Outcome Unknown Critical Warning */}
      {isOutcomeUnknown && (
        <div className="rounded border border-app-danger/30 bg-app-danger/10 p-2.5 text-[11px] text-app-danger space-y-1">
          <strong>Outcome Unknown Warning:</strong>
          <p>
            Request encountered network failure or timeout prior to receiving acknowledgment. The operation may have completed on the backend. Do not create and submit a new mutation with a new idempotency key without reconciling the previous receipt first.
          </p>
        </div>
      )}

      {/* Guidance Description */}
      <div className="space-y-1 text-app-text">
        <p className="font-semibold text-[11px]">
          Guidance: <span className="font-normal">{retryDescription}</span>
        </p>
        {feedback?.retryAt && (
          <p className="text-[10px] text-app-muted">
            Retry available at: {new Date(feedback.retryAt).toLocaleString()}
          </p>
        )}
      </div>

      {/* Global unmapped or readonly fields errors */}
      {globalFields.length > 0 && (
        <div className="rounded border border-app-border/70 bg-app-surface p-2.5 space-y-1">
          <span className="font-semibold text-[11px] text-app-text block">
            Global / Read-only Fields Errors:
          </span>
          <ul className="space-y-1">
            {globalFields.map((field, idx) => (
              <li key={idx} className="font-mono text-[10px] text-app-danger flex items-baseline gap-1.5">
                <span className="font-semibold text-app-text">{field.path}:</span>
                <span>{field.code}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Action buttons */}
      <div className="flex flex-wrap items-center justify-between gap-2 pt-1 border-t border-app-warning/20">
        <div className="flex flex-wrap gap-2">
          {onRetry && (retry === 'same_request' || retry === 'wait') && (
            <Button
              variant="secondary"
              onClick={onRetry}
              disabled={feedback?.retryAt !== null && feedback?.retryAt !== undefined && now < feedback.retryAt}
              className="h-7 px-2.5 text-xs gap-1 font-bold"
            >
              <RotateCcw size={12} />
              <span>Retry with same request</span>
            </Button>
          )}
          {onRefresh && (
            <Button
              variant="secondary"
              onClick={onRefresh}
              className="h-7 px-2.5 text-xs gap-1"
            >
              <RefreshCw size={12} />
              <span>Refresh state</span>
            </Button>
          )}
          {onReview && (
            <Button
              variant="secondary"
              onClick={onReview}
              className="h-7 px-2.5 text-xs gap-1"
            >
              <HelpCircle size={12} />
              <span>Review request</span>
            </Button>
          )}
        </div>

        {/* Collapsible Diagnostics Toggle */}
        <button
          type="button"
          onClick={() => setShowDiagnostics((prev) => !prev)}
          className="inline-flex items-center gap-1 text-[11px] font-semibold text-app-muted hover:text-app-text"
        >
          {showDiagnostics ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
          <span>{showDiagnostics ? 'Hide diagnostics details' : 'Show diagnostics details'}</span>
        </button>
      </div>

      {/* Diagnostics Panel */}
      {showDiagnostics && (
        <div className="rounded border border-app-border/80 bg-app-surface p-3 text-[11px] font-mono space-y-2 mt-2">
          <div className="flex items-center justify-between">
            <span className="text-app-muted">Request ID:</span>
            {hasRequestId && feedback?.requestId ? (
              <CopyableId id={feedback.requestId} copyLabel="Copy Request ID" />
            ) : (
              <span className="text-app-muted">No Request ID available</span>
            )}
          </div>
          <div className="flex items-center justify-between">
            <span className="text-app-muted">Response Code:</span>
            <span className="text-app-text font-bold">{feedback?.code ?? '—'}</span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-app-muted">Retry guidance:</span>
            <span className="text-app-text">{retry}</span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-app-muted">Notification surface:</span>
            <span className="text-app-text">{feedback?.surface ?? 'inline'}</span>
          </div>
        </div>
      )}
    </div>
  )
}
