import { useState, useEffect } from 'react'
import { Link } from '@tanstack/react-router'
import { AlertCircle, ArrowLeft, CheckCircle2, ChevronDown, ChevronUp, Clock, Loader2, RefreshCw } from 'lucide-react'
import type { components } from '../../core/api/schema'
import { Badge } from '../../shared/components/Badge'
import { Button } from '../../shared/components/Button'
import { CopyableId } from '../../shared/components/CopyableId'
import { Surface } from '../../shared/components/Surface'

export interface CommandReceiptTrackerProps {
  receipt?: components['schemas']['CommandResponseOutput'] | null
  isLoading?: boolean
  error?: string | null
  onReset?: () => void
  defaultCollapsed?: boolean
}

function formatDuration(created?: string | null, updated?: string | null): string | null {
  if (!created || !updated) return null
  const c = new Date(created).getTime()
  const u = new Date(updated).getTime()
  if (isNaN(c) || isNaN(u)) return null
  const diffSec = Math.max(0, Math.round((u - c) / 1000))
  return `${diffSec}s`
}

export function CommandReceiptTracker({
  receipt,
  isLoading,
  error,
  onReset,
  defaultCollapsed,
}: CommandReceiptTrackerProps) {
  const status = receipt?.status || (isLoading ? 'loading' : 'unknown')
  const action = receipt?.action || 'Control command'
  const effectiveVersion = receipt?.effective_version

  const isTerminal = ['succeeded', 'failed', 'superseded'].includes(status)
  const isSuccess = status === 'succeeded'
  const isFailed = status === 'failed'
  const isSuperseded = status === 'superseded'

  const [collapsed, setCollapsed] = useState(defaultCollapsed ?? isSuccess)

  useEffect(() => {
    if (isSuccess && defaultCollapsed === undefined) {
      setCollapsed(true)
    }
  }, [isSuccess, defaultCollapsed])

  // Compact collapsed mode for Succeeded commands (saves 80% screen space)
  if (isSuccess && collapsed) {
    const duration = formatDuration(receipt?.created_at, receipt?.updated_at)
    return (
      <Surface className="p-3.5 sm:p-4 border border-emerald-500/30 bg-emerald-500/5">
        <div className="flex flex-wrap items-center justify-between gap-3 text-xs">
          <div className="flex items-center gap-3 min-w-0">
            <CheckCircle2 size={18} className="text-emerald-500 shrink-0" />
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-bold text-app-text capitalize">
                  Command {action} successfully applied
                </span>
                {effectiveVersion !== null && effectiveVersion !== undefined && (
                  <Badge tone="success" className="text-[10px] font-mono">
                    v{effectiveVersion}
                  </Badge>
                )}
                {duration && (
                  <span className="text-[11px] text-app-muted">
                    ({duration})
                  </span>
                )}
              </div>
              <div className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-app-muted text-[11px]">
                <span className="inline-flex items-center gap-1 font-mono text-app-muted">
                  <span>Command ID:</span>
                  {receipt?.id ? (
                    <CopyableId
                      id={receipt.id}
                      truncateLength={12}
                      copyLabel="Copy command ID"
                      codeClassName="text-app-muted"
                    />
                  ) : (
                    <span>—</span>
                  )}
                </span>
                {receipt?.updated_at && (
                  <span>
                    Completed: {new Date(receipt.updated_at).toLocaleString()}
                  </span>
                )}
              </div>
            </div>
          </div>

          <div className="flex items-center gap-2 shrink-0">
            <button
              type="button"
              onClick={() => setCollapsed(false)}
              className="inline-flex items-center gap-1 rounded-md border border-app-border/80 bg-app-surface px-2.5 py-1 text-[11px] font-medium text-app-text hover:bg-app-surface-muted transition-colors"
            >
              <span>View details</span>
              <ChevronDown size={13} className="text-app-muted" />
            </button>
            <Link
              to="/"
              className="inline-flex items-center gap-1 text-[11px] font-semibold text-app-action hover:underline ml-1"
            >
              <ArrowLeft size={12} />
              <span>Overview</span>
            </Link>
            {onReset && (
              <Button variant="secondary" onClick={onReset} className="h-7 px-2.5 text-[11px] gap-1">
                <RefreshCw size={12} />
                <span>New configuration</span>
              </Button>
            )}
          </div>
        </div>
      </Surface>
    )
  }

  return (
    <Surface className="p-5 md:p-6">
      <div className="border-b border-app-border/80 pb-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <h3 className="font-heavy text-lg font-bold text-app-text">
              Command Receipt Tracker
            </h3>
            {isSuccess && (
              <button
                type="button"
                onClick={() => setCollapsed(true)}
                className="inline-flex items-center gap-1 rounded-md border border-app-border/80 bg-app-surface px-2 py-0.5 text-[11px] font-medium text-app-muted hover:text-app-text transition-colors"
              >
                <span>Collapse</span>
                <ChevronUp size={12} />
              </button>
            )}
          </div>
          <Badge
            tone={
              isSuccess
                ? 'success'
                : isFailed
                  ? 'danger'
                  : isSuperseded
                    ? 'neutral'
                    : 'warning'
            }
            className="text-xs capitalize"
          >
            {status}
          </Badge>
        </div>
      </div>

      <div className="mt-6 space-y-4">
        {/* Progress Bar / Visual steps */}
        <div className="rounded-card border border-app-border/80 bg-app-surface-muted/40 p-4">
          <div className="flex items-center justify-between text-xs font-semibold text-app-text">
            <span className="flex items-center gap-1.5">
              {isSuccess ? (
                <CheckCircle2 size={16} className="text-emerald-500" />
              ) : isFailed ? (
                <AlertCircle size={16} className="text-app-danger" />
              ) : (
                <Loader2 size={16} className="animate-spin text-app-action" />
              )}
              <span className="capitalize">{action}: {status}</span>
            </span>
            <span className="inline-flex items-center gap-1 font-mono text-app-muted">
              <span>Command ID:</span>
              {receipt?.id ? (
                <CopyableId
                  id={receipt.id}
                  truncateLength={12}
                  copyLabel="Copy command ID"
                  codeClassName="text-app-muted"
                />
              ) : (
                <span>—</span>
              )}
            </span>
          </div>

          <div className="mt-3 h-2 w-full overflow-hidden rounded-full bg-app-border">
            <div
              className={`h-full transition-all duration-500 ${
                isSuccess
                  ? 'w-full bg-emerald-500'
                  : isFailed
                    ? 'w-full bg-app-danger'
                    : isSuperseded
                      ? 'w-full bg-app-muted'
                      : status === 'applying'
                        ? 'w-2/3 bg-app-action animate-pulse'
                        : 'w-1/3 bg-app-action animate-pulse'
              }`}
            />
          </div>

          <div className="mt-4 grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs">
            <div>
              <span className="text-app-muted">Action:</span>
              <p className="font-semibold text-app-text capitalize">{action}</p>
            </div>
            <div>
              <span className="text-app-muted">Effective version:</span>
              <p className="font-mono font-semibold text-app-text">
                {effectiveVersion === null || effectiveVersion === undefined ? 'No acknowledgement yet' : `v${effectiveVersion}`}
              </p>
            </div>
            <div>
              <span className="text-app-muted">Submitted at:</span>
              <p className="font-mono text-app-muted">
                {receipt?.created_at ? new Date(receipt.created_at).toLocaleString() : '—'}
              </p>
            </div>
            <div>
              <span className="text-app-muted">Updated at:</span>
              <p className="font-mono text-app-muted">
                {receipt?.updated_at ? new Date(receipt.updated_at).toLocaleString() : '—'}
              </p>
            </div>
          </div>
        </div>

        {/* Message for Success */}
        {isSuccess && (
          <div className="rounded-card border border-emerald-500/30 bg-emerald-500/10 p-4 text-xs text-app-text flex items-start gap-3">
            <CheckCircle2 size={18} className="text-emerald-500 mt-0.5 shrink-0" />
            <div>
              <h4 className="font-bold text-emerald-500">Command successfully applied!</h4>
              <p className="mt-1">
                The receipt records the application result of a past operation. Current status, orders, and position observations should still be inspected; not proof of achieving liquidity goals.
              </p>
            </div>
          </div>
        )}

        {/* Message for Superseded */}
        {isSuperseded && (
          <div className="rounded-card border border-app-border bg-app-surface-muted/60 p-4 text-xs text-app-muted flex items-start gap-3">
            <Clock size={18} className="mt-0.5 shrink-0" />
            <div>
              <h4 className="font-bold text-app-text">Command Superseded</h4>
              <p className="mt-1">
                A newer configuration command was submitted by another operator or system, overwriting this command.
              </p>
            </div>
          </div>
        )}

        {/* Message for Failed / Error */}
        {(isFailed || error) && (
          <div className="rounded-card border border-app-danger/30 bg-app-danger/10 p-4 text-xs text-app-danger flex items-start gap-3">
            <AlertCircle size={18} className="mt-0.5 shrink-0" />
            <div>
              <h4 className="font-bold">{isFailed ? 'Command execution failed' : 'Operation outcome unverified'}</h4>
              <p className="mt-1 text-app-text">
                {receipt?.error || error || 'Backend rejected configuration command. Please verify control version and permissions.'}
              </p>
            </div>
          </div>
        )}

        {/* Action navigation buttons */}
        <div className="flex flex-wrap items-center justify-between gap-3 pt-4 border-t border-app-border/70">
          <Link
            to="/"
            className="inline-flex items-center gap-1.5 text-xs font-semibold text-app-action hover:underline"
          >
            <ArrowLeft size={14} />
            <span>Back to Overview</span>
          </Link>

          {onReset && isTerminal && (
            <Button variant="secondary" onClick={onReset} className="gap-1.5 text-xs">
              <RefreshCw size={14} />
              <span>Configure new session</span>
            </Button>
          )}
        </div>
      </div>
    </Surface>
  )
}
