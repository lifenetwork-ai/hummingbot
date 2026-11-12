import { useRef, useState } from 'react'
import { useApiSessionScope } from '../../core/api/ApiSessionContext'
import { useAdminMutation, useAdminQuery, useReceiptInspection } from '../../core/api/hooks'
import { clearPendingOperation, readPendingOperation, savePendingOperation } from '../../core/api/operation-journal'
import { ApiError } from '../../core/api/client'
import { operationFeedback } from '../../core/api/operation-feedback'
import type { WriteInput } from '../../core/api/admin'
import { useTrackedReceipts } from '../../core/api/useTrackedReceipts'
import { useOperationalContext } from '../operational-context/useOperationalContext'

type Input = WriteInput<'requestReconciliation'>
const definitive = (error: unknown) => error instanceof ApiError && ['fix_request', 'refresh_state', 'reconnect', 'contact_support'].includes(error.retry ?? '')

/** A bounded observational operation; completion is distinct from a matched report. */
export function useReconciliation() {
  const mutation = useAdminMutation('requestReconciliation')
  const operational = useOperationalContext()
  const canCheck = operational.identity?.permissions.operate === true
  const latest = useAdminQuery('getReconciliation', {}, { enabled: operational.identity?.permissions.read === true })
  const session = useApiSessionScope()
  const botId = operational.botId
  const scope = session?.apiBaseUrl && session.principalId && botId
    ? { apiBaseUrl: session.apiBaseUrl, principalId: session.principalId, botId } : undefined
  const scopeKey = JSON.stringify(scope ?? null)
  const current = useRef({ scopeKey, canCheck, pending: undefined as Input | undefined, busy: false, retryAt: 0 })
  if (current.current.scopeKey !== scopeKey) current.current = { scopeKey, canCheck, pending: undefined, busy: false, retryAt: 0 }
  current.current.canCheck = canCheck
  const [submitted, setSubmitted] = useState<{ scopeKey: string; id: string }>()
  const tracked = useTrackedReceipts().find((reference) => reference.kind === 'reconciliation')
  const referenceId = submitted?.scopeKey === scopeKey ? submitted.id : tracked?.id
  const receipt = useReceiptInspection(referenceId ? { kind: 'reconciliation', id: referenceId } : null)
  const terminal = ['succeeded', 'failed'].includes(receipt?.status ?? '')
  const receiptUnresolved = Boolean(referenceId && !terminal)
  const stored = readPendingOperation(scope, 'reconciliation')
  const pendingRetry = stored?.operation === 'requestReconciliation' ? stored.input : current.current.pending
  const feedback = operationFeedback(mutation.error)

  const submit = async (input: Input) => {
    const owner = current.current
    if (!scope || !owner.canCheck) throw new Error('permission_or_context_required')
    if (owner.busy) throw new Error('reconciliation_request_pending')
    if (Date.now() < owner.retryAt) throw new Error('reconciliation_cooldown')
    owner.busy = true
    // Keep a private immutable candidate even when browser storage is unavailable.
    owner.pending = input
    savePendingOperation(scope, 'requestReconciliation', input)
    try {
      const accepted = await mutation.mutateAsync(input)
      clearPendingOperation(scope, 'reconciliation', input.idempotencyKey)
      if (current.current === owner) {
        owner.pending = undefined
        owner.retryAt = Date.now() + 30_000
        setSubmitted({ scopeKey, id: accepted.id })
      }
      return accepted
    } catch (error) {
      if (definitive(error)) {
        clearPendingOperation(scope, 'reconciliation', input.idempotencyKey)
        owner.pending = undefined
      }
      if (error instanceof ApiError && error.retry === 'wait' && error.retryAfterSeconds !== undefined) {
        owner.retryAt = Date.now() + Math.max(0, error.retryAfterSeconds) * 1000
      }
      throw error
    } finally { owner.busy = false }
  }

  const check = async (body: Input['body']) => {
    if (!scope || !current.current.canCheck) throw new Error('permission_or_context_required')
    if (pendingRetry || current.current.pending || receiptUnresolved) throw new Error('reconciliation_outcome_unresolved')
    if (!latest.isSuccess) throw new Error('reconciliation_status_unavailable')
    if (latest.data.report?.state === 'checking' || ['accepted', 'applying'].includes(latest.data.check?.status ?? '')) throw new Error('reconciliation_already_checking')
    const createdAt = Date.parse(latest.data.check?.created_at ?? '')
    if (Number.isFinite(createdAt) && Date.now() < createdAt + 30_000) throw new Error('reconciliation_cooldown')
    if (body.lookback_seconds < 60 || body.lookback_seconds > 86400 || !Number.isInteger(body.lookback_seconds) ||
      body.max_orders < 1 || body.max_orders > 200 || !Number.isInteger(body.max_orders) ||
      body.max_pages < 1 || body.max_pages > 10 || !Number.isInteger(body.max_pages)) throw new Error('reconciliation_request_out_of_bounds')
    return submit({ body: Object.freeze({ ...body }), idempotencyKey: crypto.randomUUID() })
  }
  const retryPending = () => {
    if (!pendingRetry) throw new Error('No reconciliation request is available to retry')
    return submit(pendingRetry)
  }
  return { check, retryPending, pendingRetry, receipt, submitting: mutation.isPending, latest, feedback,
    access: { allowed: canCheck && Boolean(scope), reasonCode: canCheck && scope ? null : 'permission_or_context_required' },
    retryAt: Math.max(current.current.retryAt, feedback?.retryAt ?? 0) || null,
    refresh: latest.refetch }
}

export function useReconciliationReceipt(id: string | undefined) {
  return useReceiptInspection(id ? { kind: 'reconciliation', id } : null)
}
