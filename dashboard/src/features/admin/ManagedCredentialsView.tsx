import { KeyRound, Loader2, Lock, Unlink } from 'lucide-react'
import { useEffect, useState } from 'react'
import type { ResponseBody } from '../../core/api/types'
import type { FormFeedbackModel } from '../../core/api/operation-feedback'
import type { CredentialFormValues } from '../connections/connections'
import type { useConnectionsController } from '../connections/useConnectionsController'
import { Badge } from '../../shared/components/Badge'
import { Button } from '../../shared/components/Button'
import { CopyableId } from '../../shared/components/CopyableId'
import { Input } from '../../shared/components/Input'
import { Surface } from '../../shared/components/Surface'
import { FeedbackDetailsView } from '../session/FeedbackDetailsView'

type Controller = ReturnType<typeof useConnectionsController>
type CredentialSlot = Controller['slots'][number]
type CredentialLock = Controller['decisionLocks'][number]
type Catalogue = ResponseBody<'listCredentialInputs'>
type ValuesBySlot = Record<string, CredentialFormValues>
const slotKey = (slot: Pick<CredentialSlot, 'exchange' | 'connector'>) => `${slot.exchange}:${slot.connector}`

/** Operator-facing connection labels per docs/fe-integration.md "Disconnect UX"; raw enum stays out of the headline. */
const connectionStateLabels: Record<CredentialSlot['connectionState'], { label: string; tone: 'neutral' | 'brand' | 'success' | 'danger' | 'warning' }> = {
  connected: { label: 'Connected', tone: 'success' },
  checking: { label: 'Checking connection', tone: 'neutral' },
  unknown: { label: 'Checking connection', tone: 'neutral' },
  not_configured: { label: 'Not configured', tone: 'neutral' },
  disconnecting: { label: 'Disconnecting', tone: 'warning' },
  action_required: { label: 'Action required', tone: 'warning' },
  disconnected: { label: 'Disconnected', tone: 'neutral' },
  failed: { label: 'Connection failed', tone: 'danger' },
  unavailable: { label: 'Status unavailable', tone: 'danger' },
}

const formatUtc = (value: string | number) =>
  `${new Date(value).toLocaleString('en-US', { timeZone: 'UTC', dateStyle: 'medium', timeStyle: 'short' })} UTC`

/** CTA availability rule (docs/UX_MOTION.md): lock reasons come from backend decisions, displayed verbatim. */
const lockReasonText = (decision: CredentialSlot['decisions']['set']) =>
  decision.detail || decision.reasonCode || 'refresh status to see the reason'

export interface ManagedCredentialsViewProps {
  catalogue?: Catalogue
  slots: CredentialSlot[]
  locks: CredentialLock[]
  canManage: boolean
  isLoading: boolean
  isSubmitting: boolean
  statusError?: string | null
  onRefresh: () => Promise<unknown>
  onSetCredentials: (exchange: string, connector: string, values: CredentialFormValues, key: string) => Promise<unknown>
  onRetryCleanup: (exchange: string, connector: string, key: string) => Promise<unknown>
  onRepairCleanup: (exchange: string, connector: string, values: CredentialFormValues, key: string) => Promise<unknown>
  onReviewUnknown: (input: { exchange: string; connector: string; action: 'set' | 'disconnect' | 'retry' | 'repair' }) => Promise<{ canRetrySameRequest: boolean; idempotencyKey?: string; reason?: string }>
  onOpenDisconnect: (exchange: string, connector: string, retryKey?: string) => void
  feedbackModel?: FormFeedbackModel | null
  feedbackBySlot?: Record<string, FormFeedbackModel | null>
}

export function ManagedCredentialsView({ catalogue: _catalogue, slots, locks, canManage, isLoading, isSubmitting, statusError,
  onRefresh, onSetCredentials, onRetryCleanup, onRepairCleanup, onReviewUnknown, onOpenDisconnect, feedbackModel, feedbackBySlot }: ManagedCredentialsViewProps) {
  const [now, setNow] = useState(Date.now)
  useEffect(() => {
    if (!locks.some((lock) => lock.retryAt && lock.retryAt > Date.now())) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [locks])
  const [forms, setForms] = useState<ValuesBySlot>({})
  const [openForms, setOpenForms] = useState<Record<string, boolean>>({})
  const [formActions, setFormActions] = useState<Record<string, 'set' | 'repair'>>({})
  const [retryKeys, setRetryKeys] = useState<Record<string, string>>({})
  const [errors, setErrors] = useState<Record<string, string>>({})

  const updateValue = (key: string, field: string, value: string) => setForms((previous) => ({
    ...previous, [key]: { ...previous[key], [field]: value },
  }))
  const clear = (key: string) => setForms((previous) => {
    const next = { ...previous }
    delete next[key]
    return next
  })
  const submit = async (slot: CredentialSlot, action: 'set' | 'repair') => {
    const key = slotKey(slot)
    const values = forms[key] ?? {}
    const operationKey = retryKeys[key] ?? crypto.randomUUID()
    setErrors((previous) => ({ ...previous, [key]: '' }))
    try {
      if (action === 'set') await onSetCredentials(slot.exchange, slot.connector, values, operationKey)
      else await onRepairCleanup(slot.exchange, slot.connector, values, operationKey)
      clear(key)
      setRetryKeys((previous) => { const next = { ...previous }; delete next[key]; return next })
      setOpenForms((previous) => ({ ...previous, [key]: false }))
    } catch (error) {
      setErrors((previous) => ({ ...previous, [key]: error instanceof Error ? error.message : 'Credential operation failed.' }))
      throw error
    } finally { clear(key) }
  }
  const reviewUnknown = async (slot: CredentialSlot, action: CredentialLock['action']) => {
    const key = slotKey(slot)
    try {
      const result = await onReviewUnknown({ exchange: slot.exchange, connector: slot.connector, action })
      if (!result.canRetrySameRequest || !result.idempotencyKey) throw new Error(result.reason ?? 'Refresh and review the current operation first.')
      setRetryKeys((previous) => ({ ...previous, [key]: result.idempotencyKey! }))
      if (action === 'set' || action === 'repair') { setFormActions((previous) => ({ ...previous, [key]: action })); setOpenForms((previous) => ({ ...previous, [key]: true })) }
      if (action === 'disconnect') onOpenDisconnect(slot.exchange, slot.connector, result.idempotencyKey)
      return result.idempotencyKey
    } catch (error) {
      setErrors((previous) => ({ ...previous, [key]: error instanceof Error ? error.message : 'The operation outcome is still unknown.' }))
      return undefined
    }
  }

  return (
    <div className="space-y-4">
      <Surface className="p-5 md:p-6">
        <div className="flex flex-col justify-between gap-3 border-b border-app-border/80 pb-4 sm:flex-row sm:items-center">
          <div>
            <div className="flex items-center gap-2"><KeyRound size={20} className="text-app-action" />
              <h3 className="font-heavy text-lg font-bold text-app-text">Manage Exchange Credentials</h3></div>
            <p className="mt-1 text-xs text-app-muted">Inputs, environments, and operations defined by backend configuration.</p>
          </div>
          <Button variant="secondary" className="h-8 px-3 text-xs" onClick={() => { void onRefresh() }}>Refresh status</Button>
        </div>
        {statusError && <p role="alert" className="mt-3 text-xs text-app-danger">{statusError}</p>}
        {isLoading && <p role="status" className="mt-4 text-xs text-app-muted">Loading catalogue and connection status...</p>}
        {!isLoading && slots.length === 0 && <p role="status" className="mt-4 text-xs text-app-muted">No connection slots found in configuration catalogue.</p>}
      </Surface>

      {slots.map((slot) => {
        const key = slotKey(slot)
        const slotFeedback = feedbackBySlot?.[key] ?? feedbackModel
        const fields = slot.fields
        const values = forms[key] ?? {}
        const slotLocks = locks.filter((lock) => lock.exchange === slot.exchange && lock.connector === slot.connector)
        const blocked = slotLocks.find((lock) => lock.state === 'pending' || lock.state === 'accepted')
        const cooling = slotLocks.some((lock) => lock.retryAt && now < lock.retryAt)
        const retryWaitUntil = slotLocks.map((lock) => lock.retryAt ?? 0).filter((at) => at > now).sort((a, b) => a - b)[0]
        const source = slot.source ?? 'unknown'
        const connectionLabel = slot.connectionState === 'disconnected' && !slot.disconnectedProof
          ? { label: 'Waiting for removal verification', tone: 'neutral' as const }
          : connectionStateLabels[slot.connectionState]
        const isConfigured = slot.slot?.configured === true
        // The form must stay openable while a decision is locked, otherwise the lock reason
        // (decisions.*.detail) is unreachable: the disabled submit button carries the lock, not the toggle.
        const showSetForm = canManage && fields.length > 0
        const formAction = formActions[key] ?? 'set'
        const formDecision = formAction === 'repair' ? slot.decisions.repair : slot.decisions.set
        // Entry controls stay clickable while locked; each lock reason must be visible without any enabled click first.
        const lockedActionHints: { name: string; reason: string }[] = []
        if (fields.length > 0 && !slot.decisions.set.allowed) lockedActionHints.push({ name: 'Set credentials', reason: lockReasonText(slot.decisions.set) })
        if (isConfigured && !slot.decisions.disconnect.allowed) lockedActionHints.push({ name: 'Disconnect', reason: lockReasonText(slot.decisions.disconnect) })
        if (slot.cleanup && !slot.decisions.retry.allowed) lockedActionHints.push({ name: 'Retry cleanup', reason: lockReasonText(slot.decisions.retry) })
        if (slot.cleanup && fields.length > 0 && !slot.decisions.repair.allowed) lockedActionHints.push({ name: 'Repair cleanup key', reason: lockReasonText(slot.decisions.repair) })

        return (
          <Surface key={key} className="p-5 md:p-6">
            <div className="flex flex-wrap items-start justify-between gap-3 border-b border-app-border/80 pb-4">
              <div><div className="flex items-center gap-2"><Lock size={18} className="text-app-action" />
                <h3 className="font-heavy text-lg font-bold text-app-text">{slot.descriptor?.exchange ?? slot.exchange} · {slot.connector}</h3></div>
                <p className="mt-1 text-xs text-app-muted">{slot.slot?.label ?? slot.descriptor?.site ?? 'Credential slot'}</p>
              </div>
              <div className="flex flex-wrap gap-2">
                <Badge tone={connectionLabel.tone} className="text-xs">{connectionLabel.label}</Badge>
                <Badge tone={source === 'postgres_kms' ? 'brand' : 'neutral'} className="text-xs">{source === 'environment' ? 'Managed by deployment' : source}</Badge>
                <Badge tone="neutral" className="text-xs">{slot.descriptor?.environment ?? 'environment unknown'}</Badge>
              </div>
            </div>

            <dl className="mt-4 grid gap-3 text-xs sm:grid-cols-2 xl:grid-cols-4">
              <div><dt className="text-app-muted">Configured status</dt><dd className="font-medium text-app-text">{slot.slot?.status ?? 'unknown'}{isConfigured ? ' · saved' : ''}</dd></div>
              <div><dt className="text-app-muted">Last updated</dt><dd className="font-mono text-app-text">{slot.slot?.changed_at ? new Date(slot.slot.changed_at).toLocaleString('en-US') : '—'}</dd></div>
              <div><dt className="text-app-muted">Updated by</dt><dd className="font-mono text-app-text">{slot.slot?.changed_by ?? '—'}</dd></div>
              <div><dt className="text-app-muted">Observation</dt><dd className="font-mono text-app-text">{slot.slot?.observed_at ?? '—'}</dd></div>
            </dl>
            {slot.slot?.detail && <p className="mt-3 text-xs text-app-muted">{slot.slot.detail}</p>}

            {slot.cleanup && <div className="mt-4 rounded-card border border-app-border bg-app-surface-muted/40 p-3 text-xs">
              <p className="font-semibold text-app-text flex items-center gap-1.5 flex-wrap">
                <span>Cleanup</span>
                <CopyableId id={slot.cleanup.id} truncateLength={10} copyLabel="Copy Cleanup ID" />
                <span aria-live="polite">· {slot.cleanup.label}</span>
              </p>
              {slot.cleanup.label === 'Waiting for order verification' && (
                <p className="mt-1 text-app-muted">
                  An uncertain order is still being verified on the exchange. The key and intent remain until terminal proof
                  permits cleanup; neither retries nor an empty open-order list authorize key removal.
                </p>
              )}
              <dl className="mt-2 grid gap-1 text-app-muted sm:grid-cols-2">
                {Object.entries(slot.cleanup.finalProofs).map(([proof, complete]) => <div key={proof}>{proof}: {complete ? 'verified' : 'not verified'}</div>)}
              </dl>
              {slot.cleanup.nextRetryAt && <p className="mt-2 text-app-muted">Next retry: {formatUtc(slot.cleanup.nextRetryAt)}</p>}
              {slot.disconnectedProof && <p role="status" className="mt-2 font-semibold text-app-success">Removal verified by the root operation and all final proofs.</p>}
              <details className="mt-2">
                <summary className="cursor-pointer text-[11px] text-app-muted">Details</summary>
                <p className="mt-1 font-mono text-[11px] text-app-muted">
                  stage: {slot.cleanup.stage ?? '—'}
                  {slot.cleanup.blockedStage && <> · blocked_stage: {slot.cleanup.blockedStage}</>}
                  {slot.cleanup.reasonCode && <> · reason_code: {slot.cleanup.reasonCode}</>}
                  {slot.cleanup.nextRetryAt && <> · next_retry_at: {slot.cleanup.nextRetryAt}</>}
                </p>
              </details>
            </div>}

            {slotLocks.length > 0 && <div className="mt-4 space-y-2" aria-live="polite">
              {slotLocks.map((lock) => <div key={lock.key} className="flex flex-wrap items-center justify-between gap-2 rounded-card border border-app-warning/30 bg-app-warning/10 p-3 text-xs">
                <span className="text-app-text flex items-center gap-1.5 flex-wrap">
                  <span>{lock.action} · {lock.state}</span>
                  {lock.receiptId ? (
                    <>
                      <span>·</span>
                      <CopyableId id={lock.receiptId} truncateLength={10} copyLabel="Copy Receipt ID" />
                    </>
                  ) : (
                    <span>· outcome unknown</span>
                  )}
                </span>
                {lock.state === 'unknown' && <Button variant="secondary" className="h-7 px-2 text-xs" onClick={() => { void reviewUnknown(slot, lock.action) }}>Review latest state for same-key retry</Button>}
              </div>)}
              {blocked && <p className="text-xs text-app-warning">Follow the exact receipt before sending another change to this slot.</p>}
            </div>}

            {!canManage && <p className="mt-4 text-xs text-app-muted">manage_credentials permission is required.</p>}
            {canManage && retryWaitUntil !== undefined && (
              <p className="mt-2 text-xs text-app-muted" aria-live="polite">
                Retry cleanup is paused until {new Date(retryWaitUntil).toLocaleTimeString('en-US')} (scheduled backend retry).
              </p>
            )}
            {canManage && <div className="mt-4 flex flex-wrap gap-2">
              {fields.length > 0 && <Button variant="primary" className="h-8 px-3 text-xs" disabled={isSubmitting || !showSetForm || blocked !== undefined} onClick={() => { setFormActions((previous) => ({ ...previous, [key]: 'set' })); setOpenForms((previous) => ({ ...previous, [key]: !previous[key] })) }}>
                {openForms[key] ? 'Close input form' : `Set ${slot.exchange} credentials`}
              </Button>}
              {fields.length === 0 && (
                <p className="w-full text-xs text-app-muted">
                  {source === 'environment'
                    ? 'Credentials come from deployment configuration (environment). To enter credentials here, the backend must run in postgres_kms mode with encryption configured.'
                    : 'The backend advertised no input fields for this slot.'}
                </p>
              )}
              {isConfigured && <Button variant="danger" className="h-8 px-3 text-xs" disabled={isSubmitting || Boolean(blocked)} onClick={() => onOpenDisconnect(slot.exchange, slot.connector)}>
                <Unlink size={14} className="mr-1" /> Disconnect
              </Button>}
              {slot.cleanup && <>
                <Button variant="secondary" className="h-8 px-3 text-xs" disabled={isSubmitting || cooling || !slot.decisions.retry.allowed || Boolean(blocked)} onClick={() => { void onRetryCleanup(slot.exchange, slot.connector, retryKeys[key] ?? crypto.randomUUID()).catch((error) => setErrors((previous) => ({ ...previous, [key]: error instanceof Error ? error.message : 'Cleanup retry failed.' }))) }}>Retry cleanup</Button>
                {fields.length > 0 && <Button variant="secondary" className="h-8 px-3 text-xs" disabled={isSubmitting || Boolean(blocked)} onClick={() => { setFormActions((previous) => ({ ...previous, [key]: 'repair' })); setOpenForms((previous) => ({ ...previous, [key]: true })) }}>Repair cleanup key</Button>}
              </>}
            </div>}
            {canManage && blocked === undefined && !openForms[key] && lockedActionHints.length > 0 && (
              <div role="status" className="mt-2 space-y-1 text-xs text-app-warning">
                {lockedActionHints.map((hint) => <p key={hint.name}>{hint.name} is locked: {hint.reason}. You can still review the flow; the confirm action stays disabled until the backend allows it.</p>)}
              </div>
            )}

            {showSetForm && openForms[key] && <form className="mt-4 space-y-4 rounded-card border border-app-action/30 bg-app-action/5 p-4" onSubmit={(event) => { event.preventDefault(); void submit(slot, formAction).catch(() => undefined) }}>
              <div><h4 className="text-xs font-bold text-app-text">{formAction === 'repair' ? 'Repair cleanup credentials' : 'Set credential bundle'}</h4>
                {!formDecision.allowed ? (
                  <p role="status" className="mt-1 rounded-card border border-app-warning/30 bg-app-warning/10 p-2 text-xs text-app-text">
                    Submitting is locked: {formDecision.detail || formDecision.reasonCode || 'refresh status to see the reason'}. Resolve this condition, then refresh status.
                  </p>
                ) : formDecision.detail && <p className="mt-1 text-xs text-app-muted">{formDecision.detail}</p>}
              </div>
              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                {fields.map((field) => {
                  const inlineError = slotFeedback?.inlineErrors[field.name]?.[0]
                  return (
                    <Input
                      key={field.name}
                      label={field.label}
                      type={field.secret ? 'password' : 'text'}
                      autoComplete="off"
                      required={field.required}
                      value={values[field.name] ?? ''}
                      onChange={(event) => updateValue(key, field.name, event.target.value)}
                      error={inlineError}
                    />
                  )
                })}
              </div>
              {errors[key] && !Object.keys(slotFeedback?.inlineErrors ?? {}).length && <p role="alert" className="text-xs text-app-danger">{errors[key]}</p>}
              {(slotFeedback?.feedback || (slotFeedback?.globalFields && slotFeedback.globalFields.length > 0)) && (
                <FeedbackDetailsView
                  feedback={slotFeedback.feedback}
                  globalFields={slotFeedback.globalFields}
                />
              )}
              <div className="flex flex-wrap justify-end gap-2">
                <Button variant="secondary" type="button" className="h-8 px-3 text-xs" onClick={() => { clear(key); setOpenForms((previous) => ({ ...previous, [key]: false })); setRetryKeys((previous) => { const next = { ...previous }; delete next[key]; return next }) }}>Close</Button>
                <Button variant="primary" type="submit" disabled={isSubmitting || Boolean(blocked) || cooling || !(formAction === 'repair' ? slot.decisions.repair.allowed : slot.decisions.set.allowed)} className="h-8 px-3 text-xs">{isSubmitting ? <><Loader2 size={14} className="mr-1 animate-spin" />Submitting…</> : formAction === 'repair' ? 'Submit repair' : 'Submit set'}</Button>
              </div>
            </form>}
            {!openForms[key] && slotFeedback?.feedback && <FeedbackDetailsView feedback={slotFeedback.feedback} globalFields={slotFeedback.globalFields} />}
            {errors[key] && !openForms[key] && <p role="alert" className="mt-3 text-xs text-app-danger">{errors[key]}</p>}
          </Surface>
        )
      })}
      {/* <div className="rounded-card bg-app-surface-muted/60 p-3 text-xs text-app-muted"><strong>Secret handling: </strong>Credential values stay in the form/request only; they are not retained for automatic retry or receipt tracking.</div> */}
    </div>
  )
}
