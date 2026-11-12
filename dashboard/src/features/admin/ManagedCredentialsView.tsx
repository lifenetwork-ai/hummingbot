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
        const unknown = slotLocks.find((lock) => lock.state === 'unknown')
        const blocked = slotLocks.find((lock) => lock.state === 'pending' || lock.state === 'accepted')
        const cooling = slotLocks.some((lock) => lock.retryAt && now < lock.retryAt)
        const source = slot.source ?? 'unknown'
        const isConfigured = slot.slot?.configured === true
        const isConnected = slot.connectionState === 'connected'
        const showSetForm = canManage && fields.length > 0 && (openForms[key] || slot.decisions.set.allowed || slot.decisions.repair.allowed || unknown?.action === 'set' || unknown?.action === 'repair')
        const formAction = formActions[key] ?? 'set'

        return (
          <Surface key={key} className="p-5 md:p-6">
            <div className="flex flex-wrap items-start justify-between gap-3 border-b border-app-border/80 pb-4">
              <div><div className="flex items-center gap-2"><Lock size={18} className="text-app-action" />
                <h3 className="font-heavy text-lg font-bold text-app-text">{slot.descriptor?.exchange ?? slot.exchange} · {slot.connector}</h3></div>
                <p className="mt-1 text-xs text-app-muted">{slot.slot?.label ?? slot.descriptor?.site ?? 'Credential slot'}</p>
              </div>
              <div className="flex flex-wrap gap-2">
                <Badge tone={isConnected ? 'success' : slot.connectionState === 'unavailable' ? 'danger' : 'neutral'} className="text-xs">{slot.connectionState}</Badge>
                <Badge tone={source === 'postgres_kms' ? 'brand' : 'neutral'} className="text-xs">{source}</Badge>
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
                <span>· stage: {slot.cleanup.blockedStage ?? slot.cleanup.reasonCode ?? 'in progress'}</span>
              </p>
              <dl className="mt-2 grid gap-1 text-app-muted sm:grid-cols-2">
                {Object.entries(slot.cleanup.finalProofs).map(([proof, complete]) => <div key={proof}>{proof}: {complete ? 'verified' : 'not verified'}</div>)}
              </dl>
              {slot.cleanup.nextRetryAt && <p className="mt-2 text-app-muted">Next retry: {slot.cleanup.nextRetryAt}</p>}
              {slot.disconnectedProof && <p role="status" className="mt-2 font-semibold text-app-success">Removal verified by the root operation and all final proofs.</p>}
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
            {canManage && <div className="mt-4 flex flex-wrap gap-2">
              {fields.length > 0 && <Button variant="primary" className="h-8 px-3 text-xs" disabled={isSubmitting || !showSetForm || blocked !== undefined} onClick={() => { setFormActions((previous) => ({ ...previous, [key]: 'set' })); setOpenForms((previous) => ({ ...previous, [key]: !previous[key] })) }}>
                {openForms[key] ? 'Close input form' : `Set ${slot.exchange} credentials`}
              </Button>}
              {isConfigured && <Button variant="danger" className="h-8 px-3 text-xs" disabled={isSubmitting || !slot.decisions.disconnect.allowed || Boolean(blocked)} onClick={() => onOpenDisconnect(slot.exchange, slot.connector)}>
                <Unlink size={14} className="mr-1" /> Disconnect
              </Button>}
              {slot.cleanup && <>
                <Button variant="secondary" className="h-8 px-3 text-xs" disabled={isSubmitting || cooling || !slot.decisions.retry.allowed || Boolean(blocked)} onClick={() => { void onRetryCleanup(slot.exchange, slot.connector, retryKeys[key] ?? crypto.randomUUID()).catch((error) => setErrors((previous) => ({ ...previous, [key]: error instanceof Error ? error.message : 'Cleanup retry failed.' }))) }}>Retry cleanup</Button>
                {fields.length > 0 && <Button variant="secondary" className="h-8 px-3 text-xs" disabled={isSubmitting || !slot.decisions.repair.allowed || Boolean(blocked)} onClick={() => { setFormActions((previous) => ({ ...previous, [key]: 'repair' })); setOpenForms((previous) => ({ ...previous, [key]: true })) }}>Repair cleanup key</Button>}
              </>}
            </div>}

            {showSetForm && openForms[key] && <form className="mt-4 space-y-4 rounded-card border border-app-action/30 bg-app-action/5 p-4" onSubmit={(event) => { event.preventDefault(); void submit(slot, formAction).catch(() => undefined) }}>
              <div><h4 className="text-xs font-bold text-app-text">{formAction === 'repair' ? 'Repair cleanup credentials' : 'Set credential bundle'}</h4>
                {slot.decisions.set.detail && <p className="mt-1 text-xs text-app-muted">{slot.decisions.set.detail}</p>}
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
                <Button variant="secondary" type="button" className="h-8 px-3 text-xs" onClick={() => { clear(key); setOpenForms((previous) => ({ ...previous, [key]: false })); setRetryKeys((previous) => { const next = { ...previous }; delete next[key]; return next }) }}>Clear secret inputs</Button>
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
