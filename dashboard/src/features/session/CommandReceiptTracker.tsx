import { useState, useEffect } from 'react'
import { Link } from '@tanstack/react-router'
import { AlertCircle, ArrowLeft, ArrowRight, CheckCircle2, ChevronDown, ChevronUp, Clock, Loader2, RefreshCw } from 'lucide-react'
import type { components } from '../../core/api/schema'
import { Badge } from '../../shared/components/Badge'
import { Button } from '../../shared/components/Button'
import { CopyableId } from '../../shared/components/CopyableId'
import { ProfileSignatureChip } from '../../shared/components/ProfileSignatureChip'
import { Surface } from '../../shared/components/Surface'
import { configPayloadOf, type CommandReceipt, type SessionConfigPayload } from './usePreviousSessionCommand'

export interface CommandReceiptTrackerProps {
  receipt?: components['schemas']['CommandResponseOutput'] | null
  /** Latest earlier start/switch receipt for the same session; the configuration a switch replaces. */
  previousReceipt?: CommandReceipt | null
  previousLoading?: boolean
  isLoading?: boolean
  error?: string | null
  onReset?: () => void
  defaultCollapsed?: boolean
}

function durationLabel(payload: SessionConfigPayload): string {
  if (payload.duration_mode === 'until_stopped') return 'Until stopped'
  if (payload.duration_seconds !== null && payload.duration_seconds !== undefined) {
    return `${Math.round(payload.duration_seconds / 60)} min`
  }
  return 'YAML default'
}

function profileLabel(payload: SessionConfigPayload): string {
  if (payload.selection === 'random') {
    const pool = payload.profile_ids ?? []
    return pool.length > 0 ? `Random pool: ${pool.join(', ')}` : 'Random pool: catalogue default'
  }
  return payload.profile_id ?? '—'
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
  previousReceipt,
  previousLoading,
  isLoading,
  error,
  onReset,
  defaultCollapsed,
}: CommandReceiptTrackerProps) {
  const status = receipt?.status || (isLoading ? 'loading' : 'unknown')
  const action = receipt?.action || 'Control command'
  const effectiveVersion = receipt?.effective_version
  const payload = configPayloadOf(receipt)
  const previousPayload = configPayloadOf(previousReceipt)

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
      <Surface className="p-3.5 sm:p-4 border border-app-success/30 bg-app-success/5">
        <div className="flex flex-wrap items-center justify-between gap-3 text-xs">
          <div className="flex items-center gap-3 min-w-0">
            <CheckCircle2 size={18} className="text-app-success shrink-0" />
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
                {payload && (
                  <span className="inline-flex items-center gap-1 font-mono">
                    {previousPayload && payload.action === 'switch' ? (
                      <>
                        <span className="truncate max-w-[140px]">{profileLabel(previousPayload)}</span>
                        <ArrowRight size={11} className="shrink-0" />
                      </>
                    ) : (
                      <span>Config:</span>
                    )}
                    <span className="truncate max-w-[180px] font-semibold text-app-text">{profileLabel(payload)}</span>
                  </span>
                )}
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
                <CheckCircle2 size={16} className="text-app-success" />
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
                  ? 'w-full bg-app-success'
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

        {/* Switch context: what configuration this command replaced */}
        {receipt?.action === 'switch' && (
          <div className="rounded-card border border-app-border/80 bg-app-surface-muted/40 p-4">
            <h4 className="text-xs font-bold text-app-text">Switched Configuration</h4>
            {previousPayload ? (
              <div className="mt-3 overflow-hidden rounded-card border border-app-border">
                <table className="w-full text-left text-xs">
                  <thead>
                    <tr className="border-b border-app-border bg-app-surface-muted/60 font-semibold text-app-muted">
                      <th className="p-2.5 w-1/4">Property</th>
                      <th className="p-2.5 w-[30%]">Replaced (previous)</th>
                      <th className="p-2.5 w-[30%]">Applied (this command)</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-app-border/60">
                    {(() => {
                      const current = payload ?? previousPayload
                      if (!current) return null
                      const profileChanged = previousPayload.selection !== current.selection || profileLabel(previousPayload) !== profileLabel(current)
                      const marketChanged = previousPayload.market_mode !== current.market_mode
                      const durationChanged = durationLabel(previousPayload) !== durationLabel(current)
                      const rows: Array<{ label: string; from: string; to: string; changed: boolean }> = [
                        { label: 'Task profile', from: profileLabel(previousPayload), to: profileLabel(current), changed: profileChanged },
                        { label: 'Market mode', from: previousPayload.market_mode, to: current.market_mode, changed: marketChanged },
                        { label: 'Duration', from: durationLabel(previousPayload), to: durationLabel(current), changed: durationChanged },
                      ]
                      return rows.map(row => (
                        <tr key={row.label} className={row.changed ? 'bg-app-action/5' : ''}>
                          <td className="p-2.5 font-semibold text-app-text">{row.label}</td>
                          <td className="p-2.5 font-mono text-app-muted">
                            <span className={row.changed ? 'line-through decoration-app-danger/60' : ''}>{row.from}</span>
                          </td>
                          <td className="p-2.5 font-mono font-bold text-app-text">
                            <span className="inline-flex flex-wrap items-center gap-2">
                              <span className={row.changed ? 'text-app-action' : ''}>{row.to}</span>
                              {row.changed && <Badge tone="brand" className="text-[10px]">Changed</Badge>}
                            </span>
                          </td>
                        </tr>
                      ))
                    })()}
                  </tbody>
                </table>
              </div>
            ) : previousLoading ? (
              <p className="mt-2 inline-flex items-center gap-1.5 text-xs text-app-muted">
                <Loader2 size={13} className="animate-spin" />
                Loading the previous configuration for this session…
              </p>
            ) : (
              <p className="mt-2 text-xs text-app-muted">
                No earlier configuration receipt was found for this session — this may be the first recorded command, or session history is unavailable.
              </p>
            )}
          </div>
        )}

        {/* Submitted configuration from the recorded payload */}
        {receipt?.action === 'stop' ? (
          <div className="rounded-card border border-app-border/80 bg-app-surface-muted/40 p-4 text-xs text-app-muted">
            <h4 className="font-bold text-app-text">Stop Command</h4>
            <p className="mt-1">
              Stops session <span className="font-mono">{receipt.session_id}</span> (expected version v{receipt.expected_version}). Held positions are not automatically closed; inspect actual orders after stopping.
            </p>
          </div>
        ) : payload ? (
          <div className="rounded-card border border-app-border/80 bg-app-surface-muted/40 p-4">
            <h4 className="text-xs font-bold text-app-text">Submitted Configuration</h4>
            <p className="mt-0.5 text-[11px] text-app-muted">Recorded exactly as submitted with this command.</p>
            <dl className="mt-3 grid grid-cols-1 gap-x-6 gap-y-3 text-xs sm:grid-cols-2 lg:grid-cols-3">
              <div>
                <dt className="text-app-muted">Task profile</dt>
                <dd className="mt-0.5">
                  <ProfileSignatureChip
                    profileId={payload.selection === 'random' ? 'random_pool' : payload.profile_id}
                    selection={payload.selection}
                    size="sm"
                  />
                </dd>
              </div>
              <div>
                <dt className="text-app-muted">Selection</dt>
                <dd className="mt-0.5 font-mono font-semibold text-app-text capitalize">{payload.selection}</dd>
              </div>
              {payload.selection === 'random' && payload.profile_ids && payload.profile_ids.length > 0 && (
                <div>
                  <dt className="text-app-muted">Profile pool</dt>
                  <dd className="mt-0.5 font-mono text-app-text">{payload.profile_ids.join(', ')}</dd>
                </div>
              )}
              <div>
                <dt className="text-app-muted">Market mode</dt>
                <dd className="mt-0.5 font-mono font-semibold text-app-text capitalize">{payload.market_mode}</dd>
              </div>
              <div>
                <dt className="text-app-muted">Duration</dt>
                <dd className="mt-0.5 font-mono font-semibold text-app-text">{durationLabel(payload)}</dd>
              </div>
              {payload.selection === 'random' && payload.step_duration_seconds !== null && payload.step_duration_seconds !== undefined && (
                <div>
                  <dt className="text-app-muted">Step duration</dt>
                  <dd className="mt-0.5 font-mono font-semibold text-app-text">{Math.round(payload.step_duration_seconds / 60)} min</dd>
                </div>
              )}
              {payload.references && payload.references.length > 0 && (
                <div className="sm:col-span-2 lg:col-span-3">
                  <dt className="text-app-muted">Reference pairs</dt>
                  <dd className="mt-1 flex flex-wrap gap-1.5">
                    {payload.references.map(ref => (
                      <span
                        key={ref.pair}
                        className="inline-flex items-center rounded-pill border border-app-border/80 bg-app-surface px-2 py-0.5 font-mono text-[11px] text-app-text"
                      >
                        {ref.pair}
                        <span className="mx-1 text-app-muted">·</span>
                        <span className="text-app-muted">{ref.weight}</span>
                      </span>
                    ))}
                  </dd>
                </div>
              )}
              <div>
                <dt className="text-app-muted">Expected version</dt>
                <dd className="mt-0.5 font-mono font-semibold text-app-text">v{receipt?.expected_version}</dd>
              </div>
              {payload.expected_catalogue_hash && (
                <div>
                  <dt className="text-app-muted">Catalogue hash</dt>
                  <dd className="mt-0.5 font-mono text-app-muted" title={payload.expected_catalogue_hash}>
                    {payload.expected_catalogue_hash.slice(0, 12)}…
                  </dd>
                </div>
              )}
            </dl>
          </div>
        ) : null}

        {/* Message for Success */}
        {isSuccess && (
          <div className="rounded-card border border-app-success/30 bg-app-success/10 p-4 text-xs text-app-text flex items-start gap-3">
            <CheckCircle2 size={18} className="text-app-success mt-0.5 shrink-0" />
            <div>
              <h4 className="font-bold text-app-success">Command successfully applied!</h4>
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
