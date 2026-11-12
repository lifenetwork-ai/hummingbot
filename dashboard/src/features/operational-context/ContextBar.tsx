import { useEffect, useState } from 'react'
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion'
import { AlertCircle, AlertTriangle, ChevronUp, Clock, Loader2, X } from 'lucide-react'
import { Badge } from '../../shared/components/Badge'
import { CopyableId } from '../../shared/components/CopyableId'
import { Surface } from '../../shared/components/Surface'
import { cn } from '../../shared/utils/cn'
import type { OperationalContextModel } from './context'
import { findReceiptAcceptedAt, type ReceiptReference } from '../../core/api/receipt-tracking'
import type { TrackedReceiptInspection } from '../../core/api/hooks'
import type { ReceiptOutcome } from '../../core/api/receipt-inspection'

function CardRefreshingOverlay({ active, label }: { active: boolean; label: string }) {
  const reducedMotion = useReducedMotion()

  return (
    <div
      className={`pointer-events-none absolute inset-0 z-10 transition-opacity duration-200 ${
        active ? 'opacity-100' : 'opacity-0'
      }`}
      aria-hidden="true"
    >
      <div className="absolute inset-x-0 top-0 z-20 h-[2px] overflow-hidden bg-app-border/20">
        <div
          className={`h-full w-1/3 rounded-pill bg-gradient-to-r from-transparent via-app-action to-transparent ${
            reducedMotion ? 'w-full opacity-60' : 'animate-shimmer'
          }`}
        />
      </div>
      <div className="absolute inset-0 bg-app-surface/30 backdrop-blur-[0.5px]" />
      <div className="relative z-10 flex items-start justify-end p-3">
        <span className="inline-flex items-center gap-1.5 rounded-pill border border-app-border/80 bg-app-surface/95 px-2.5 py-0.5 text-[11px] font-medium text-app-muted shadow-sm backdrop-blur-sm">
          <Loader2 size={11} className="animate-spin text-app-action shrink-0" />
          <span>{label}</span>
        </span>
      </div>
    </div>
  )
}

export type ContextBarProps = {
  context: OperationalContextModel
  apiBaseUrl: string
  trackedReceipts?: ReceiptReference[]
  selectedReceipt?: ReceiptReference | null
  receiptInspection?: TrackedReceiptInspection | null
  onSelectReceipt?: (receipt: ReceiptReference | null) => void
  defaultOpen?: boolean
  open?: boolean
  onOpenChange?: (open: boolean) => void
}

function environmentLabel(value: string): string {
  if (value === 'demo') return 'Demo'
  if (value === 'live') return 'Live'
  return 'Unknown'
}

function observationLabel(value: 'fresh' | 'stale' | 'unknown'): string {
  if (value === 'fresh') return 'Latest observation'
  if (value === 'stale') return 'Stale data'
  return 'No observation'
}

function getStatusIndicator(context: OperationalContextModel) {
  const { status, balances, environmentMismatch, observationState } = context
  if (status.phase === 'error' || environmentMismatch || observationState === 'session_mismatch') {
    return {
      dotColor: 'bg-app-danger',
      pingColor: 'bg-app-danger',
      ping: false,
      label: status.phase === 'error'
        ? 'Bot connection error'
        : environmentMismatch
          ? 'Environment mismatch'
          : 'Session mismatch',
      tone: 'danger' as const,
    }
  }
  if (observationState === 'stale' || balances.phase === 'error') {
    return {
      dotColor: 'bg-amber-500',
      pingColor: 'bg-amber-400',
      ping: false,
      label: balances.phase === 'error' ? 'Account error' : 'Stale data',
      tone: 'warning' as const,
    }
  }
  if (status.phase === 'loading' || observationState === 'unknown') {
    return {
      dotColor: 'bg-app-muted',
      pingColor: 'bg-app-muted',
      ping: false,
      label: status.phase === 'loading' ? 'Loading' : 'Unknown',
      tone: 'neutral' as const,
    }
  }
  return {
    dotColor: 'bg-emerald-500',
    pingColor: 'bg-emerald-400',
    ping: true,
    label: 'Connected',
    tone: 'success' as const,
  }
}

function ReceiptOutcomeView({ outcome, status }: { outcome: ReceiptOutcome; status?: string }) {
  if (outcome.kind === 'command') {
    return (
      <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
        <dt className="text-app-muted">Command</dt><dd className="text-app-text">{outcome.action}</dd>
        {outcome.sessionId && (
          <>
            <dt className="text-app-muted">Session</dt>
            <dd className="break-all font-mono text-app-text">
              <CopyableId id={outcome.sessionId} copyLabel="Copy session ID" />
            </dd>
          </>
        )}
        {outcome.error && <><dt className="text-app-muted">Error</dt><dd className="text-app-danger">{outcome.error}</dd></>}
        <dt className="col-span-2 text-app-muted">Receipt does not confirm current order status or position.</dt>
      </dl>
    )
  }

  if (outcome.kind === 'credential') {
    return (
      <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
        <dt className="text-app-muted">Action</dt><dd className="text-app-text">{outcome.action}</dd>
        <dt className="text-app-muted">Stage</dt><dd className="text-app-text">{outcome.stage}</dd>
        {outcome.operatorActionRequired && <><dt className="text-app-muted">Action required</dt><dd className="text-app-text">Yes</dd></>}
        {outcome.reasonCode && <><dt className="text-app-muted">Reason code</dt><dd className="font-mono text-app-text">{outcome.reasonCode}</dd></>}
        {outcome.error && <><dt className="text-app-muted">Error</dt><dd className="text-app-danger">{outcome.error}</dd></>}
        <dt className="col-span-2 text-app-muted">Receipt does not yet confirm current connection state.</dt>
      </dl>
    )
  }

  if (outcome.kind === 'reconciliation') {
    const stateLabel = {
      not_checked: 'Not checked', checking: 'Checking', matched: 'Matched', mismatch: 'Mismatch',
      incomplete: 'Incomplete', unavailable: 'Unavailable',
    }[outcome.state]
    return (
      <div className="mt-2 text-xs">
        <p className="font-medium text-app-text">Reconciliation result: {stateLabel}</p>
        {outcome.reasons.length > 0 && <ul className="mt-1 list-disc pl-4 text-app-muted">{outcome.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>}
      </div>
    )
  }

  const recovered = status === 'succeeded' && outcome.stage === 'recovered'
  return (
    <div className="mt-2 space-y-1 text-xs">
      <p className="text-app-text">Action: {outcome.action} · Stage: {outcome.stage}</p>
      <p className="text-app-muted">{recovered ? 'Recovered per receipt.' : outcome.canRecover ? 'Backend indicates recoverable; follow review procedure.' : 'Backend has not confirmed recoverable.'}</p>
      {outcome.blockers.length > 0 && <ul className="space-y-1 text-app-muted">
        {outcome.blockers.map((blocker, index) => <li key={`${blocker.code}:${index}`}>
          <span className="font-mono">{blocker.code}</span>: {blocker.detail}{blocker.next_action && ` · ${blocker.next_action}`}
        </li>)}
      </ul>}
    </div>
  )
}

export function ContextBar({
  context,
  apiBaseUrl,
  trackedReceipts = [],
  selectedReceipt: controlledSelected,
  receiptInspection: propReceiptInspection,
  onSelectReceipt,
  defaultOpen = false,
  open: controlledOpen,
  onOpenChange,
}: ContextBarProps) {
  const [internalOpen, setInternalOpen] = useState(defaultOpen)
  const isControlledOpen = controlledOpen !== undefined
  const isOpen = isControlledOpen ? controlledOpen : internalOpen

  const reducedMotion = useReducedMotion()

  const [internalSelected, setInternalSelected] = useState<ReceiptReference | null>(null)
  const isControlledSelected = controlledSelected !== undefined
  const activeSelected = isControlledSelected ? controlledSelected : internalSelected

  const {
    identity,
    status,
    balances,
    botId,
    processEnvironment,
    accounts,
    markets,
    environmentMismatch,
    actions,
    observation,
    observationState,
    controlVersion,
  } = context
  const roleLabel = identity?.role_label ?? 'Unknown identity'
  const activePermissions = Object.entries(identity?.permissions ?? {}).filter(([, allowed]) => allowed).map(([name]) => name)
  const indicator = getStatusIndicator(context)

  // Close panel on Escape key
  useEffect(() => {
    if (!isOpen) return
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (!isControlledOpen) {
          setInternalOpen(false)
        }
        onOpenChange?.(false)
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [isOpen, isControlledOpen, onOpenChange])

  const handleToggleReceipt = (receipt: ReceiptReference) => {
    const isSame = activeSelected?.kind === receipt.kind && activeSelected?.id === receipt.id
    const next = isSame ? null : receipt
    if (!isControlledSelected) {
      setInternalSelected(next)
    }
    onSelectReceipt?.(next)
  }

  const handleToggleOpen = () => {
    const nextOpen = !isOpen
    if (!isControlledOpen) {
      setInternalOpen(nextOpen)
    }
    onOpenChange?.(nextOpen)
  }

  return (
    <>
      {/* Screen reader live refresh announcement */}
      <div role="status" aria-live="polite" className="sr-only">
        {status.refreshing && <p>Updating bot status.</p>}
        {balances.refreshing && <p>Updating account balances.</p>}
      </div>

      {/* Flyout Panel from bottom when expanded */}
      <AnimatePresence initial={false}>
        {isOpen && (
          <>
            {/* Backdrop */}
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={reducedMotion ? { duration: 0 } : { duration: 0.15 }}
              className="fixed inset-0 z-30 bg-black/40 backdrop-blur-[1px]"
              aria-hidden="true"
              onClick={handleToggleOpen}
            />

            {/* Expanded Content Drawer from Bottom */}
            <motion.div
              id="operational-context-panel"
              role="region"
              aria-labelledby="operational-context-trigger"
              initial={reducedMotion ? { opacity: 0 } : { opacity: 0, y: 30 }}
              animate={reducedMotion ? { opacity: 1 } : { opacity: 1, y: 0 }}
              exit={reducedMotion ? { opacity: 0 } : { opacity: 0, y: 30 }}
              transition={reducedMotion ? { duration: 0 } : { duration: 0.2, ease: 'easeOut' }}
              className="fixed inset-x-0 bottom-9 z-40 max-h-[82vh] overflow-y-auto border-t border-app-border bg-app-bg/95 backdrop-blur-xl shadow-2xl"
            >
              <div className="mx-auto w-full max-w-[1536px] px-4 py-5 sm:px-6 md:px-8 lg:px-10">
                <div className="mb-4 flex items-center justify-between border-b border-app-border/60 pb-3">
                  <div>
                    <h3 className="font-heavy text-lg font-bold tracking-tight text-app-text">
                      Operational Context Details
                    </h3>
                    <p className="mt-0.5 text-xs text-app-muted">
                      Authoritative context confirmed by backend API; environment is not inferred from page address.
                    </p>
                  </div>
                  <div className="flex items-center gap-3">
                    <div className="hidden sm:flex items-center gap-2 text-xs text-app-muted">
                      <span>Updated at:</span>
                      {observation?.observed_at ? (
                        <span className="font-mono text-app-text">{observation.observed_at}</span>
                      ) : (
                        <span className="text-app-muted">No timestamp</span>
                      )}
                    </div>
                    <button
                      type="button"
                      onClick={handleToggleOpen}
                      aria-label="Close context details"
                      className="rounded-lg p-1.5 text-app-muted transition-colors hover:bg-app-surface-muted hover:text-app-text focus:outline-none focus-visible:ring-2 focus-visible:ring-app-action"
                    >
                      <X size={16} />
                    </button>
                  </div>
                </div>

                <div className="grid gap-4 md:grid-cols-2">
                  <Surface className="relative flex min-h-[340px] flex-col justify-between overflow-hidden p-5">
                    <CardRefreshingOverlay
                      active={Boolean(status.refreshing || balances.refreshing)}
                      label={
                        status.refreshing && balances.refreshing
                          ? 'Syncing bot & balances…'
                          : status.refreshing
                            ? 'Updating bot…'
                            : 'Updating balances…'
                      }
                    />
                    <div>
                      <h3 className="font-semibold text-app-text">Connection and bot</h3>
                      <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
                        <dt className="text-app-muted">API</dt>
                        <dd className="break-all font-mono text-xs text-app-text">
                          <CopyableId id={apiBaseUrl} copyLabel="Copy API Endpoint" />
                        </dd>

                        <dt className="text-app-muted">Bot</dt>
                        <dd className="text-app-text">
                          {botId ?? (
                            status.phase === 'error' ? (
                              'Failed to read status'
                            ) : status.phase === 'loading' ? (
                              <span className="inline-flex items-center gap-2">
                                <span className="h-4 w-20 rounded bg-app-surface-muted animate-pulse inline-block" />
                                <span className="text-xs text-app-muted">Loading</span>
                              </span>
                            ) : (
                              'Loading'
                            )
                          )}
                        </dd>

                        <dt className="text-app-muted">Bot environment</dt>
                        <dd className="text-app-text">{environmentLabel(processEnvironment)}</dd>

                        <dt className="text-app-muted">Exchange account</dt>
                        <dd className="text-app-text">
                          {accounts.length === 0 ? (
                            balances.phase === 'error' ? (
                              'Failed to read account status'
                            ) : balances.phase === 'ready' ? (
                              'No observed accounts'
                            ) : (
                              <span className="inline-flex items-center gap-2">
                                <span className="h-4 w-28 rounded bg-app-surface-muted animate-pulse inline-block" />
                                <span className="text-xs text-app-muted">Loading</span>
                              </span>
                            )
                          ) : accounts.length === 1 ? (
                            <div className="flex flex-wrap items-center gap-2">
                              <span>{accounts[0].data.account_id ?? 'No account ID'}</span>
                              <Badge tone={accounts[0].verification === 'verified' ? 'success' : 'neutral'}>
                                {accounts[0].verification === 'verified' ? 'Verified' : 'Unverified account identity'}
                              </Badge>
                              <Badge tone={accounts[0].observation === 'fresh' ? 'success' : accounts[0].observation === 'stale' ? 'warning' : 'neutral'}>
                                {observationLabel(accounts[0].observation)}
                              </Badge>
                            </div>
                          ) : (
                            `${accounts.length} accounts returned by backend`
                          )}
                        </dd>

                        <dt className="text-app-muted">Account environment</dt>
                        <dd className="text-app-text">
                          {accounts.length === 1
                            ? environmentLabel(accounts[0].data.environment)
                            : accounts.length > 1
                              ? 'See accounts below'
                              : 'Unknown'}
                        </dd>
                      </dl>

                      {accounts.length > 1 && (
                        <ul aria-label="Exchange accounts" className="mt-3 space-y-2">
                          {accounts.map(({ data, verification, observation: obs }, index) => (
                            <li key={`${data.account_id ?? 'unknown'}:${index}`} className="flex flex-wrap items-center gap-2 text-sm">
                              <span className="text-app-text">
                                {data.account_id ?? 'No account ID'} · {environmentLabel(data.environment)}
                              </span>
                              <Badge tone={verification === 'verified' ? 'success' : 'neutral'}>
                                {verification === 'verified' ? 'Verified' : 'Unverified account identity'}
                              </Badge>
                              <Badge tone={obs === 'fresh' ? 'success' : obs === 'stale' ? 'warning' : 'neutral'}>
                                {observationLabel(obs)}
                              </Badge>
                            </li>
                          ))}
                        </ul>
                      )}

                      {environmentMismatch && (
                        <p role="alert" className="mt-3 rounded-card border border-app-danger/30 bg-app-danger/5 p-3 text-sm text-app-danger">
                          Mismatch: backend environment identifiers do not agree. Cannot verify trading context.
                        </p>
                      )}

                      {status.phase === 'error' && (
                        <p role="status" className="mt-3 text-sm text-app-danger">
                          Cannot verify bot information from backend.{botId ? ' Retaining last-read data.' : ''}
                        </p>
                      )}
                      {balances.phase === 'error' && (
                        <p role="status" className="mt-2 text-sm text-app-danger">
                          Cannot verify account information from backend.{accounts.length > 0 ? ' Retaining last-read data.' : ''}
                        </p>
                      )}
                    </div>

                    <div className="mt-4 border-t border-app-border/60 pt-3">
                      <h4 className="text-sm font-semibold text-app-text">Configured markets</h4>
                      {status.phase === 'loading' ? (
                        <div className="mt-2 flex gap-2" aria-label="Loading markets">
                          <span className="h-6 w-28 rounded-pill bg-app-surface-muted animate-pulse inline-block" />
                          <span className="h-6 w-32 rounded-pill bg-app-surface-muted animate-pulse inline-block" />
                        </div>
                      ) : markets.length ? (
                        <ul className="mt-2 flex flex-wrap gap-2">
                          {markets.map((market) => (
                            <li key={`${market.connector}:${market.market}:${market.pair}`}>
                              <Badge tone="neutral">{market.connector} · {market.market} · {market.pair}</Badge>
                            </li>
                          ))}
                        </ul>
                      ) : (
                        <p className="mt-1 text-sm text-app-muted">Backend has not provided markets.</p>
                      )}
                    </div>
                  </Surface>

                  <Surface className="relative flex min-h-[340px] flex-col justify-between overflow-hidden p-5">
                    <CardRefreshingOverlay active={Boolean(status.refreshing)} label="Updating permissions…" />
                    <div>
                      <h3 className="font-semibold text-app-text">Permissions and operational capabilities</h3>
                      <p className="mt-2 text-sm text-app-muted">
                        {roleLabel} · {identity?.email ?? identity?.id ?? 'Unknown identity'}
                      </p>

                      <div className="mt-4">
                        <h4 className="text-sm font-semibold text-app-text">Current capabilities from backend</h4>
                        {status.phase === 'loading' ? (
                          <ul className="mt-2 space-y-2" aria-label="Loading capabilities">
                            <li className="h-6 w-44 rounded-pill bg-app-surface-muted animate-pulse" />
                            <li className="h-6 w-40 rounded-pill bg-app-surface-muted animate-pulse" />
                            <li className="h-6 w-36 rounded-pill bg-app-surface-muted animate-pulse" />
                          </ul>
                        ) : status.phase !== 'ready' ? (
                          <p className="mt-1 text-sm text-app-danger">
                            Cannot read operational capabilities; please reload status.
                          </p>
                        ) : (
                          <ul className="mt-2 space-y-2">
                            {Object.entries(actions).map(([name, availability]) => (
                              <li key={name} className="flex flex-wrap items-center gap-2 text-sm">
                                <Badge tone={availability.allowed ? 'success' : 'neutral'}>
                                  {name}: {availability.allowed ? 'allowed' : 'blocked'}
                                </Badge>
                                {!availability.allowed && (
                                  <span className="text-app-muted">
                                    {availability.detail || availability.reasonCode || 'No reason provided by backend'}
                                  </span>
                                )}
                              </li>
                            ))}
                          </ul>
                        )}
                      </div>
                      <p className="mt-3 text-xs text-app-muted">
                        Account permissions do not guarantee action is permitted: backend verifies permissions and state on each command.
                      </p>
                    </div>

                    {trackedReceipts.length > 0 && (
                      <div className="mt-4 border-t border-app-border/80 pt-3">
                        <h4 className="text-xs font-semibold uppercase tracking-wider text-app-muted">
                          Tracked prior operations ({trackedReceipts.length})
                        </h4>
                        <p className="mt-1 text-xs text-app-muted">
                          Select an operation to inspect actual status from backend.
                        </p>
                        <ul className="mt-2 flex flex-wrap gap-1.5" aria-label="Tracked operations">
                          {trackedReceipts.map((receipt) => {
                            const isSelected = activeSelected?.kind === receipt.kind && activeSelected?.id === receipt.id
                            const acceptedAt = findReceiptAcceptedAt(receipt.id)
                            const timeLabel = acceptedAt
                              ? new Date(acceptedAt).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })
                              : null

                            return (
                              <li key={`${receipt.kind}:${receipt.id}`}>
                                <button
                                  type="button"
                                  onClick={() => handleToggleReceipt(receipt)}
                                  title={`${receipt.kind}: ${receipt.id}`}
                                  aria-pressed={isSelected}
                                  className={cn(
                                    'inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 font-mono text-[11px] font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-app-action',
                                    isSelected
                                      ? 'bg-app-action text-white ring-1 ring-app-action'
                                      : 'bg-app-accent/10 text-app-text hover:bg-app-accent/20'
                                  )}
                                >
                                  <span>{receipt.kind}: {receipt.id.length > 12 ? `${receipt.id.slice(0, 12)}…` : receipt.id}</span>
                                  {timeLabel && (
                                    <span className={cn('text-[10px] font-sans font-normal', isSelected ? 'text-white/80' : 'text-app-muted')}>
                                      ({timeLabel})
                                    </span>
                                  )}
                                </button>
                              </li>
                            )
                          })}
                        </ul>

                        {activeSelected && (
                          <div
                            className="mt-3 rounded-card border border-app-border bg-app-surface/80 p-3 text-xs"
                            aria-label="Tracked operation details"
                          >
                            <div className="flex items-start justify-between gap-2">
                              <div className="flex items-center gap-1.5 min-w-0">
                                <span className="font-semibold uppercase text-app-text">{activeSelected.kind}: </span>
                                <CopyableId id={activeSelected.id} copyLabel="Copy operation ID" />
                              </div>
                              <button
                                type="button"
                                onClick={() => handleToggleReceipt(activeSelected)}
                                className="shrink-0 text-xs text-app-muted hover:text-app-text"
                              >
                                Close
                              </button>
                            </div>

                            {(() => {
                              const selectedAcceptedAt = findReceiptAcceptedAt(activeSelected.id)
                              if (!selectedAcceptedAt) return null
                              return (
                                <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-app-muted border-t border-app-border/40 pt-1.5">
                                  <span className="inline-flex items-center gap-1 font-mono">
                                    <Clock size={11} className="text-app-action shrink-0" />
                                    <span>Submitted at:</span>
                                    <strong className="text-app-text font-semibold">
                                      {new Date(selectedAcceptedAt).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit' })} ({new Date(selectedAcceptedAt).toLocaleDateString('en-US')})
                                    </strong>
                                  </span>
                                </div>
                              )
                            })()}

                            <div className="mt-2 flex flex-wrap items-center gap-2">
                              <span className="text-app-muted">Status:</span>
                              {propReceiptInspection?.phase === 'loading' && (
                                <span className="text-app-muted">Reading outcome...</span>
                              )}
                              {propReceiptInspection?.phase === 'error' && (
                                <Badge tone="danger">
                                  {propReceiptInspection.error?.message || 'Error reading status'}
                                </Badge>
                              )}
                              {propReceiptInspection?.phase === 'ready' && propReceiptInspection.status && (
                                <Badge
                                  tone={
                                    propReceiptInspection.status === 'failed'
                                      ? 'danger'
                                      : ['accepted', 'applying'].includes(propReceiptInspection.status) ? 'warning' : 'neutral'
                                  }
                                >
                                  Receipt progress: {propReceiptInspection.status}
                                </Badge>
                              )}
                              {(!propReceiptInspection || propReceiptInspection.phase === 'idle') && (
                                <span className="text-app-muted">Connecting to read status...</span>
                              )}
                            </div>
                            {propReceiptInspection?.phase === 'ready' && propReceiptInspection.outcome && (
                              <ReceiptOutcomeView outcome={propReceiptInspection.outcome} status={propReceiptInspection.status} />
                            )}
                          </div>
                        )}
                      </div>
                    )}
                  </Surface>
                </div>

                <details className="group mt-4 rounded-card border border-app-border/80 bg-app-surface/60 p-4 transition-colors">
                  <summary className="flex cursor-pointer items-center justify-between text-xs font-semibold uppercase tracking-wider text-app-muted hover:text-app-text focus:outline-none focus-visible:ring-1 focus-visible:ring-app-action">
                    <span>Technical & diagnostic details</span>
                    <span className="text-[11px] transition-transform group-open:rotate-180">▼</span>
                  </summary>
                  <div className="mt-3 grid gap-3 border-t border-app-border/60 pt-3 text-xs text-app-muted sm:grid-cols-2">
                    <div className="flex items-center gap-1.5 flex-wrap">
                      <span className="font-medium text-app-text">API Endpoint: </span>
                      <CopyableId id={apiBaseUrl} copyLabel="Copy API Endpoint" />
                    </div>
                    {controlVersion !== null && (
                      <div>
                        <span className="font-medium text-app-text">Control Version: </span>
                        <code className="font-mono text-app-text">{controlVersion}</code>
                      </div>
                    )}
                    {activePermissions.length > 0 && (
                      <div className="sm:col-span-2">
                        <span className="font-medium text-app-text">Granted permissions: </span>
                        <span>{activePermissions.join(', ')}</span>
                      </div>
                    )}
                  </div>
                </details>
              </div>
            </motion.div>
          </>
        )}
      </AnimatePresence>

      {/* Persistent Bottom Status Bar (Footer) */}
      <section
        className="fixed inset-x-0 bottom-0 z-30 h-9 border-t border-app-border/80 bg-app-surface/95 shadow-md backdrop-blur-md"
        aria-labelledby="operational-context-title"
      >
        {/* Top subtle indeterminate progress bar when refreshing */}
        {(status.refreshing || balances.refreshing) && (
          <div className="absolute inset-x-0 top-0 h-[2px] overflow-hidden bg-transparent z-10" aria-hidden="true">
            <div className="h-full w-1/3 rounded-pill bg-gradient-to-r from-transparent via-app-action to-transparent animate-shimmer" />
          </div>
        )}

        <div className="mx-auto flex h-full w-full max-w-[1536px] items-center justify-between gap-3 px-4 sm:px-6 md:px-8 lg:px-10 text-xs">
          {/* Left summary items */}
          <div className="flex items-center gap-2 sm:gap-2.5 min-w-0 overflow-x-auto no-scrollbar py-1">
            <span className="relative flex h-2 w-2 shrink-0" aria-hidden="true">
              {indicator.ping && (
                <span className={cn('absolute inline-flex h-full w-full rounded-full opacity-75 animate-ping', indicator.pingColor)} />
              )}
              <span className={cn('relative inline-flex h-2 w-2 rounded-full', indicator.dotColor)} />
            </span>

            <h2 id="operational-context-title" className="text-[11px] font-bold uppercase tracking-wider text-app-muted shrink-0">
              Operational context
            </h2>

            {!isOpen && (
              <>
                <div className="h-3 w-px bg-app-border/80 hidden sm:block shrink-0" aria-hidden="true" />

                <div className="flex items-center gap-1 text-xs shrink-0">
                  <span className="text-app-muted text-[11px]">Bot:</span>
                  <span className="font-mono font-medium text-app-text text-[11px]">
                    {botId ?? (
                      status.phase === 'error' ? (
                        'Failed to read'
                      ) : (
                        'Loading'
                      )
                    )}
                  </span>
                </div>

                {accounts.length === 1 && accounts[0].data.account_id && (
                  <span className="hidden text-[11px] text-app-muted md:inline shrink-0">
                    · <span className="font-mono">{accounts[0].data.account_id}</span>
                  </span>
                )}
              </>
            )}

            {(processEnvironment === 'live' || processEnvironment === 'demo') && (
              <Badge tone={processEnvironment === 'live' ? 'danger' : 'neutral'} className="px-2 py-0.5 text-[10px]">
                {environmentLabel(processEnvironment)}
              </Badge>
            )}

            {/* Freshness & Refreshing Badge: When refreshing, it directly overwrites the freshness label */}
            <Badge
              tone={
                status.refreshing || balances.refreshing
                  ? 'neutral'
                  : observationState === 'fresh'
                    ? 'success'
                    : observationState === 'stale'
                      ? 'warning'
                      : observationState === 'session_mismatch'
                        ? 'danger'
                        : 'neutral'
              }
              className="inline-flex items-center gap-1 px-2 py-0.5 text-[10px] shrink-0"
            >
              {status.refreshing || balances.refreshing ? (
                <>
                  <Loader2 size={10} className="animate-spin text-app-action shrink-0" />
                  <span>Syncing…</span>
                </>
              ) : observationState === 'fresh' ? (
                'Recently updated'
              ) : observationState === 'stale' ? (
                'Stale data'
              ) : observationState === 'session_mismatch' ? (
                'Session mismatch'
              ) : (
                'Unknown'
              )}
            </Badge>

            {status.phase === 'error' && (
              <span className="inline-flex items-center gap-1 rounded-pill border border-app-danger/30 bg-app-danger/10 px-2 py-0.5 text-[10px] font-medium text-app-danger shrink-0">
                <AlertCircle size={11} className="shrink-0" />
                <span>Cannot verify bot</span>
              </span>
            )}

            {environmentMismatch && (
              <span className="inline-flex items-center gap-1 rounded-pill border border-app-danger/30 bg-app-danger/10 px-2 py-0.5 text-[10px] font-medium text-app-danger shrink-0">
                <AlertTriangle size={11} className="shrink-0" />
                <span>Environment mismatch</span>
              </span>
            )}
          </div>

          {/* Right controls: Toggle trigger */}
          <div className="flex items-center gap-2 shrink-0">
            <button
              type="button"
              id="operational-context-trigger"
              aria-expanded={isOpen}
              aria-controls="operational-context-panel"
              onClick={handleToggleOpen}
              className="inline-flex items-center gap-1 rounded-md border border-app-border/80 bg-app-surface px-2.5 py-1 text-[11px] font-medium text-app-text transition-colors hover:bg-app-surface-muted focus:outline-none focus-visible:ring-2 focus-visible:ring-app-action"
            >
              <span>{isOpen ? 'Collapse' : 'Details'}</span>
              <ChevronUp
                size={13}
                className={cn(
                  'text-app-muted transition-transform duration-200',
                  isOpen && 'rotate-180'
                )}
              />
            </button>
          </div>
        </div>
      </section>
    </>
  )
}
