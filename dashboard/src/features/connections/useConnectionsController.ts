import { useEffect, useRef } from 'react'
import { useQueries, useQueryClient } from '@tanstack/react-query'
import { useAdminMutation, useAdminQuery } from '../../core/api/hooks'
import { adminQueryOptions } from '../../core/api/queries'
import { invalidateAfterReceipt } from '../../core/api/invalidation'
import { useAdminApi } from '../../core/api/ApiContext'
import { useTrackedReceipts } from '../../core/api/useTrackedReceipts'
import { useApiSessionScope } from '../../core/api/ApiSessionContext'
import { deriveFormFeedback } from '../../core/api/operation-feedback'
import { ApiError } from '../../core/api/client'
import { useOperationalContext } from '../operational-context/useOperationalContext'
import { cleanupComplete, credentialSetOutcome, deriveCleanupProgress, deriveCredentialSlot, validateCredentialBundle, type CredentialFormValues } from './connections'
import { type DecisionLock, type LockedAction } from './operation-state-context'
import { useConnectionsOperationState } from './useConnectionsOperationState'

function outcomeMayBeUnknown(error: unknown): boolean {
  return !(error instanceof ApiError) || error.status === 408 || error.status >= 500 ||
    error.retry === 'same_request' || error.retry === 'wait'
}

/** Connections workflow logic. Unknown request candidates remain private to its workspace owner until resolved. */
export function useConnectionsController(options: { active?: boolean; recoveryPending?: boolean } = {}) {
  const api = useAdminApi()
  const client = useQueryClient()
  const apiScope = useApiSessionScope()
  const operational = useOperationalContext()
  const scopeToken = JSON.stringify([apiScope?.apiBaseUrl?.replace(/\/+$/, '') ?? null, apiScope?.principalId ?? null, operational.botId])
  const scopeRef = useRef(scopeToken)
  scopeRef.current = scopeToken
  const active = options.active ?? true
  const canManage = operational.identity?.permissions.manage_credentials === true
  const canRead = operational.identity?.permissions.read === true
  const recoveryPending = options.recoveryPending ?? operational.recoveryLock?.active ?? false
  const permissionRef = useRef(canManage)
  permissionRef.current = canManage
  const catalogue = useAdminQuery('listCredentialInputs', {}, { enabled: active && canRead })
  const exchanges = catalogue.data?.exchanges ?? []
  const statuses = useQueries({ queries: exchanges.map(({ exchange }) => ({ ...adminQueryOptions(api, 'getExchangeCredentialStatus', { path: { exchange } }), enabled: active && canRead })) })
  const setMutation = useAdminMutation('setExchangeCredentials')
  const disconnectMutation = useAdminMutation('disconnectExchangeCredentials')
  const retryMutation = useAdminMutation('retryCredentialCleanup')
  const repairMutation = useAdminMutation('repairCleanupCredentials')
  const { locksRef, candidatesRef, locks, setLocks } = useConnectionsOperationState()
  // Only announce Set receipts initiated by this workspace session.
  const submittedSetIdsRef = useRef(new Map<string, string>())
  const generationRef = useRef(0)
  const ownerIdentityRef = useRef(JSON.stringify([scopeToken, canManage]))
  const ownerIdentity = JSON.stringify([scopeToken, canManage])
  if (ownerIdentityRef.current !== ownerIdentity) {
    ownerIdentityRef.current = ownerIdentity
    generationRef.current += 1
    submittedSetIdsRef.current.clear()
    locksRef.current.clear()
    candidatesRef.current.clear()
  }
  useEffect(() => {
    for (const [key, lock] of locksRef.current) if (lock.scopeToken !== scopeToken || !canManage) {
      locksRef.current.delete(key)
      candidatesRef.current.delete(key)
    }
    setLocks([...locksRef.current.values()])
  }, [scopeToken, canManage, locksRef, candidatesRef, setLocks])
  const tracked = useTrackedReceipts().filter((receipt) => receipt.kind === 'credential')
  const activeLocks = locks.filter((lock) => lock.scopeToken === scopeToken)
  const slotRootIds = active ? statuses.flatMap((query) => query.data?.slots.flatMap((slot) => [slot.cleanup_operation_id, slot.operation_id].filter((id): id is string => Boolean(id))) ?? []) : []
  const rootIds = [...new Set([...tracked.map(({ id }) => id), ...activeLocks.flatMap((lock) => lock.receiptId ? [lock.receiptId] : []), ...slotRootIds])]
  const pendingReceiptIds = new Set([...tracked.map(({ id }) => id), ...activeLocks.filter((lock) => lock.state === 'accepted').flatMap((lock) => lock.receiptId ? [lock.receiptId] : [])])
  const receiptQueries = useQueries({ queries: rootIds.map((id) => ({ ...adminQueryOptions(api, 'getCredentialOperation', { path: { operation_id: id } }), enabled: canRead && (active || pendingReceiptIds.has(id)) })) })
  const terminalReceipts = receiptQueries.flatMap((query) => query.data &&
    (query.data.status === 'succeeded' || query.data.status === 'failed')
      ? [`${query.data.id}:${query.data.status}:${query.data.updated_at}`] : []).join('|')
  const lockReceiptRevision = activeLocks.filter((lock) => lock.state === 'accepted').map((lock) => lock.receiptId).join('|')

  useEffect(() => {
    if (!terminalReceipts) return
    void invalidateAfterReceipt(client, 'getCredentialOperation')
    const terminalIds = new Set(terminalReceipts.split('|').map((value) => value.split(':')[0]))
    let changed = false
    for (const [key, lock] of locksRef.current) {
      if (lock.receiptId && terminalIds.has(lock.receiptId)) {
        locksRef.current.delete(key)
        candidatesRef.current.delete(key)
        changed = true
      }
    }
    if (changed) setLocks([...locksRef.current.values()])
  }, [client, terminalReceipts, lockReceiptRevision, locksRef, candidatesRef, setLocks])

  const runtime = { ...operational.status, updatedAt: operational.statusUpdatedAt }
  const slots = exchanges.flatMap((exchange, index) => (statuses[index]?.data?.slots ?? []).map((slot) => {
    const model = deriveCredentialSlot(operational.identity, source(catalogue), source(statuses[index]), runtime, exchange.exchange, slot.connector)
    const rootId = model.cleanup?.rootReceiptId ?? slot.operation_id
    const rootReceipt = rootIds.includes(rootId ?? '') ? receiptQueries[rootIds.indexOf(rootId ?? '')]?.data : undefined
    const setReceipt = slot.operation_id ? receiptQueries[rootIds.indexOf(slot.operation_id)]?.data : undefined
    const decisions = recoveryPending ? { set: { allowed: false, reasonCode: 'recovery_in_progress', detail: operational.recoveryLock?.detail ?? 'Recovery operation is pending; wait for its outcome.' }, disconnect: { allowed: false, reasonCode: 'recovery_in_progress', detail: operational.recoveryLock?.detail ?? 'Recovery operation is pending; wait for its outcome.' }, retry: { allowed: false, reasonCode: 'recovery_in_progress', detail: operational.recoveryLock?.detail ?? 'Recovery operation is pending; wait for its outcome.' }, repair: { allowed: false, reasonCode: 'recovery_in_progress', detail: operational.recoveryLock?.detail ?? 'Recovery operation is pending; wait for its outcome.' } } : model.decisions
    return { ...model, decisions, setOutcome: submittedSetIdsRef.current.get(JSON.stringify([exchange.exchange, slot.connector])) === slot.operation_id
      ? credentialSetOutcome(setReceipt, slot.operation_id) : null, disconnectedProof: model.connectionState === 'disconnected' && cleanupComplete(rootReceipt), cleanup: model.cleanup && rootReceipt ? {
      ...model.cleanup,
      ...deriveCleanupProgress(rootReceipt),
    } : model.cleanup }
  }))

  function currentSlot(exchange: string, connector: string) {
    const result = slots.find((item) => item.exchange === exchange && item.connector === connector)
    if (!result?.slot) throw new Error('Refresh credential status and review this connection before submitting')
    return result
  }
  function requireAllowed(exchange: string, connector: string, action: 'set' | 'disconnect' | 'retry' | 'repair') {
    if (!permissionRef.current) throw new Error('This action requires manage_credentials permission.')
    const model = currentSlot(exchange, connector)
    const decision = model.decisions[action]
    if (!decision.allowed) throw new Error(decision.detail || decision.reasonCode || 'This action is unavailable')
    return model
  }
  function lockKey(action: LockedAction, model: (typeof slots)[number], operationVersion: number | null) {
    const botId = operational.botId
    if (!apiScope?.apiBaseUrl || !apiScope.principalId || !botId) throw new Error('Verified API, principal and bot scope are required for connection changes')
    return JSON.stringify([apiScope.apiBaseUrl.replace(/\/+$/, ''), apiScope.principalId, botId, action,
      model.exchange, model.connector, model.slot?.version, runtime.data?.control_version, operationVersion])
  }
  async function runLocked<T extends { id?: string }>(action: LockedAction, model: (typeof slots)[number], operationVersion: number | null, idempotencyKey: string, request: () => Promise<T>) {
    const key = lockKey(action, model, operationVersion)
    const existing = locksRef.current.get(key)
    const retryCandidate = existing ? candidatesRef.current.get(key) : undefined
    const competing = [...locksRef.current.values()].find((lock) => lock.scopeToken === scopeToken && lock.exchange === model.exchange && lock.connector === model.connector && lock.state === 'unknown' && lock.key !== key)
    if (competing) throw new Error('An unknown operation must be reviewed before making a different connection change')
    if (existing?.state === 'unknown' && !retryCandidate) throw new Error('The original request is unavailable; review current credential state and start a new operation')
    if (existing?.retryAt && Date.now() < existing.retryAt) throw new Error('operation_cooldown')
    if (existing && !(existing.state === 'unknown' && existing.retryReviewed && existing.idempotencyKey === idempotencyKey)) {
      throw new Error('This reviewed action already has an open or unknown operation; follow its receipt or explicitly review the latest state')
    }
    if (existing && existing.idempotencyKey !== idempotencyKey) throw new Error('Unknown outcomes can only retry with the same idempotency key and unchanged request')
    const requestScope = scopeToken
    const requestGeneration = generationRef.current
    const lock: DecisionLock = existing ? { ...existing, retryReviewed: false, state: 'pending' } : {
      key, scopeToken: requestScope, action, exchange: model.exchange, connector: model.connector,
      credentialVersion: model.slot!.version, controlVersion: runtime.data!.control_version,
      operationVersion, idempotencyKey, retryReviewed: false, state: 'pending', receiptId: null,
    }
    if (action === 'set') submittedSetIdsRef.current.delete(JSON.stringify([model.exchange, model.connector]))
    locksRef.current.set(key, lock)
    if (!existing) candidatesRef.current.set(key, { run: request })
    setLocks([...locksRef.current.values()])
    try {
      const response = await (retryCandidate?.run() ?? request())
      if (scopeRef.current !== requestScope || generationRef.current !== requestGeneration || !permissionRef.current) return response
      const accepted: DecisionLock = { ...lock, state: response.id ? 'accepted' : 'unknown', receiptId: response.id ?? null }
      if (action === 'set' && response.id) submittedSetIdsRef.current.set(JSON.stringify([model.exchange, model.connector]), response.id)
      locksRef.current.set(key, accepted)
      if (response.id) candidatesRef.current.delete(key)
      setLocks([...locksRef.current.values()])
      return response
    } catch (error) {
      if (scopeRef.current !== requestScope || generationRef.current !== requestGeneration || !permissionRef.current) throw error
      const retryAt = error instanceof ApiError && error.retry === 'wait' && error.retryAfterSeconds !== undefined && Number.isFinite(error.retryAfterSeconds) ? error.observedAt + Math.max(0, error.retryAfterSeconds) * 1000 : lock.retryAt
      if (outcomeMayBeUnknown(error)) locksRef.current.set(key, { ...lock, state: 'unknown', retryAt })
      else { locksRef.current.delete(key); candidatesRef.current.delete(key) }
      setLocks([...locksRef.current.values()])
      throw error
    }
  }

  return {
    catalogue: source(catalogue), runtime, slots,
    access: { allowed: canManage, reasonCode: canManage ? null : 'permission_required' as const },
    operationPending: recoveryPending || Object.values({ set: setMutation, disconnect: disconnectMutation, retry: retryMutation, repair: repairMutation }).some((mutation) => mutation.isPending),
    refresh: () => Promise.all([catalogue.refetch(), operational.refreshStatus(), operational.refreshBalances(), ...statuses.map((query) => query.refetch())]),
    decisionLocks: activeLocks,
    reviewUnknown: async (input: { exchange: string; connector: string; action: LockedAction }) => {
      if (!canManage) throw new Error('This action requires manage_credentials permission.')
      const pending = [...locksRef.current.values()].filter((lock) => lock.scopeToken === scopeToken && lock.state === 'unknown' && lock.exchange === input.exchange && lock.connector === input.connector && lock.action === input.action)
      if (!pending.length) throw new Error('There is no unknown operation awaiting review')
      const reviewScope = scopeToken
      const reviewGeneration = generationRef.current
      const exchangeIndex = exchanges.findIndex((item) => item.exchange === input.exchange)
      if (exchangeIndex < 0) throw new Error('Refresh credential catalogue before reviewing the unknown operation')
      const [freshStatus, freshRuntime] = await Promise.all([statuses[exchangeIndex]!.refetch(), operational.refreshStatus()])
      if (scopeRef.current !== reviewScope || scopeToken !== reviewScope || generationRef.current !== reviewGeneration || !permissionRef.current ||
          pending.some((lock) => locksRef.current.get(lock.key) !== lock)) {
        throw new Error('Connection permission or scope changed during review; review the current operation again')
      }
      const current = freshRuntime.data
      const fresh = freshStatus.data && current?.owner_available && current.observation?.observed_at && !current.observation.stale && current.observation.session_matches
      if (!freshStatus.isSuccess || !fresh) throw new Error('Latest credential and bot status could not be verified; operation remains unknown')
      const slot = freshStatus.data.slots.find((item) => item.connector === input.connector)
      const unchanged = pending.filter((lock) => slot && slot.version === lock.credentialVersion && current.control_version === lock.controlVersion &&
        (input.action === 'retry' || input.action === 'repair' ? slot.operation_version === lock.operationVersion : lock.operationVersion === null))
      if (!unchanged.length) return { canRetrySameRequest: false, reason: 'Versions changed; review the new state and make a new decision.' }
      for (const lock of unchanged) locksRef.current.set(lock.key, { ...lock, retryReviewed: true })
      setLocks([...locksRef.current.values()])
      return { canRetrySameRequest: true, idempotencyKey: unchanged[0]!.idempotencyKey }
    },
    set: async (input: { exchange: string; connector: string; values: CredentialFormValues; idempotencyKey: string }) => {
      const model = requireAllowed(input.exchange, input.connector, 'set')
      const missing = validateCredentialBundle(model.descriptor?.fields ?? [], input.values)
      if (!model.descriptor || missing.length) throw new Error(`Required credential fields are missing: ${missing.join(', ')}`)
      const controlVersion = runtime.data?.control_version
      if (controlVersion === undefined) throw new Error('Current control version is unavailable')
      const requestInput = { path: { exchange: input.exchange, connector: input.connector }, idempotencyKey: input.idempotencyKey,
        body: { credentials: { ...input.values }, expected_version: model.slot!.version, expected_control_version: controlVersion } }
      return runLocked('set', model, null, input.idempotencyKey, () => setMutation.mutateAsync(requestInput))
    },
    disconnect: (input: { exchange: string; connector: string; idempotencyKey: string }) => {
      const model = requireAllowed(input.exchange, input.connector, 'disconnect')
      const controlVersion = runtime.data?.control_version
      if (controlVersion === undefined) throw new Error('Current control version is unavailable')
      const requestInput = { path: { exchange: input.exchange, connector: input.connector }, idempotencyKey: input.idempotencyKey,
        body: { expected_version: model.slot!.version, expected_control_version: controlVersion } }
      return runLocked('disconnect', model, null, input.idempotencyKey, () => disconnectMutation.mutateAsync(requestInput))
    },
    retryCleanup: (input: { exchange: string; connector: string; idempotencyKey: string }) => {
      const model = requireAllowed(input.exchange, input.connector, 'retry')
      const operationId = model.slot!.cleanup_operation_id
      const controlVersion = runtime.data?.control_version
      const operationVersion = model.slot!.operation_version
      if (!operationId || controlVersion === undefined || operationVersion === null) throw new Error('Refresh root cleanup operation versions before retrying')
      const requestInput = { path: { operation_id: operationId }, idempotencyKey: input.idempotencyKey,
        body: { expected_version: model.slot!.version, expected_control_version: controlVersion, expected_operation_version: operationVersion } }
      return runLocked('retry', model, operationVersion, input.idempotencyKey, () => retryMutation.mutateAsync(requestInput))
    },
    repairCleanupKey: (input: { exchange: string; connector: string; values: CredentialFormValues; idempotencyKey: string }) => {
      const model = requireAllowed(input.exchange, input.connector, 'repair')
      const operationId = model.slot!.cleanup_operation_id
      const controlVersion = runtime.data?.control_version
      const operationVersion = model.slot!.operation_version
      const credentials = { ...input.values }
      const idempotencyKey = input.idempotencyKey
      const missing = validateCredentialBundle(model.descriptor?.fields ?? [], input.values)
      if (!model.descriptor || missing.length) throw new Error(`Required credential fields are missing: ${missing.join(', ')}`)
      if (!operationId || controlVersion === undefined || operationVersion === null) throw new Error('Refresh root cleanup operation versions before repairing')
      const requestInput = { path: { exchange: input.exchange, connector: input.connector }, idempotencyKey,
        body: { operation_id: operationId, credentials, expected_version: model.slot!.version,
          expected_control_version: controlVersion, expected_operation_version: operationVersion } }
      return runLocked('repair', model, operationVersion, idempotencyKey, () => repairMutation.mutateAsync(requestInput))
    },
    feedback: {
      set: deriveFormFeedback(setMutation.error, 'credentials', [...new Set(slots.flatMap((slot) => slot.fields.map((field) => field.name)))]),
      repair: deriveFormFeedback(repairMutation.error, 'credentials', [...new Set(slots.flatMap((slot) => slot.fields.map((field) => field.name)))]),
      disconnect: deriveFormFeedback(disconnectMutation.error, 'credentials'), retry: deriveFormFeedback(retryMutation.error, 'credentials'),
    },
    mutations: { set: setMutation, disconnect: disconnectMutation, retry: retryMutation, repair: repairMutation },
  }
}

function source<T extends { data?: unknown; error?: unknown; isPending: boolean; isFetching: boolean; isError: boolean }>(query: T) {
  return { phase: query.isError ? 'error' as const : query.isPending ? 'loading' as const : 'ready' as const,
    data: query.data as T['data'], error: query.error as Error | null, refreshing: query.isFetching,
    updatedAt: 'dataUpdatedAt' in query && typeof query.dataUpdatedAt === 'number' ? query.dataUpdatedAt : undefined }
}
