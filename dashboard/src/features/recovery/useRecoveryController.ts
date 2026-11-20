import { useEffect, useMemo, useRef, useState } from 'react'
import { skipToken, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useAuth } from '../../core/auth/auth-context'
import { ApiError } from '../../core/api/client'
import { useApiSessionScope } from '../../core/api/ApiSessionContext'
import { useAdminMutation } from '../../core/api/hooks'
import { adminKeys, adminQueryOptions, receiptFinished } from '../../core/api/queries'
import { useAdminApi } from '../../core/api/ApiContext'
import { invalidateAfterMutation, invalidateAfterReceipt } from '../../core/api/invalidation'
import { acceptedReceiptReference, rememberAcceptedReceipt } from '../../core/api/receipt-tracking'
import { trustedBotId } from '../../core/api/trustedBotId'
import type { WriteInput } from '../../core/api/admin'
import { buildRecoveryComparison } from './comparison'
import { deriveFormFeedback } from '../../core/api/operation-feedback'
import { useTrackedReceipts } from '../../core/api/useTrackedReceipts'
import { useOperationalContext } from '../operational-context/useOperationalContext'
import { recoveryComplete, recoveryEntry, recoveryOperationPending, reviewRecovery } from './recovery'
import { deriveRecoveryPresentation, deriveRecoveryVerification } from './presentation'
import { useObservationTime } from '../../shared/utils/useObservationTime'
import { deriveRecoveryViewModel } from './view-model'
import { exactUncertainOrderRepairs, normalizeUncertainOrderRepairs, uncertainOrderQuery, uncertainOrderRequest, validateUncertainOrderIds } from './uncertain-orders'

function newKey() { return crypto.randomUUID() }
function hideConfirmationToken<T extends { confirmation_token: string | null }>(operation: T): T {
  return { ...operation, confirmation_token: null }
}
function outcomeMayBeUnknown(error: unknown): boolean {
  if (!(error instanceof ApiError)) return true
  return error.status === 408 || error.status >= 500 || error.retry === 'same_request' || error.retry === 'wait'
}

function useSafeRecoveryReceipt(api: ReturnType<typeof useAdminApi>, id: string | undefined, enabled: boolean,
  captureToken?: (token: string, operationId: string) => void) {
  const input = id ? { path: { operation_id: id } } : undefined
  const options = adminQueryOptions(api, 'getRecoveryOperation', input)
  return useQuery({
    ...options,
    queryFn: input === undefined ? skipToken : async ({ signal }) => {
      const response = await api.read('getRecoveryOperation', input, signal)
      if (response.confirmation_token) captureToken?.(response.confirmation_token, response.id)
      return hideConfirmationToken(response)
    },
    enabled,
  })
}

/** Recovery controller. Confirmation tokens remain in private refs and are never journaled. */
export function useRecoveryController(active: boolean, activate: () => () => void) {
  const { identity } = useAuth()
  const operational = useOperationalContext()
  const api = useAdminApi()
  const permission = Boolean(identity?.permissions.manage_recovery)
  const [uncertainOrderIds, setUncertainOrderIdsState] = useState<string[]>([])
  const [uncertainOrderValidationError, setUncertainOrderValidationError] = useState<string | null>(null)
  const uncertainOrderIdsRef = useRef<string[]>([])
  const overviewInput = useMemo(() => uncertainOrderQuery(uncertainOrderIds), [uncertainOrderIds])
  const overviewOptions = adminQueryOptions(api, 'getRecovery', overviewInput)
  const overview = useQuery({
    ...overviewOptions,
    queryFn: async ({ signal }) => {
      const response = await api.read('getRecovery', overviewInput, signal)
      return { ...response, operation: response.operation ? hideConfirmationToken(response.operation) : null }
    },
    enabled: permission && active,
  })
  const status = operational.status
  const now = useObservationTime(active)
  const refreshStatus = operational.refreshStatus
  const resetMutation = useAdminMutation('resetRecovery')
  const client = useQueryClient()
  const apiSession = useApiSessionScope()
  const scope = `${apiSession?.apiBaseUrl ?? ''}\0${apiSession?.principalId ?? ''}\0${operational.botId ?? ''}`
  const operationScope = `${scope}\0${permission ? 'manage_recovery' : 'permission_revoked'}`
  const scopeRef = useRef(operationScope)
  const previousScope = useRef(operationScope)
  const scopeGeneration = useRef(0)
  const mounted = useRef(true)
  const permissionRef = useRef(Boolean(identity?.permissions.manage_recovery))
  scopeRef.current = operationScope
  permissionRef.current = Boolean(identity?.permissions.manage_recovery)
  const trackedReceipts = useTrackedReceipts()
  const latestTrackedRecovery = trackedReceipts.find((receipt) => receipt.kind === 'recovery')
  const latestTrackedRecoveryId = latestTrackedRecovery?.id
  const confirmationToken = useRef<{ id: string; token: string }>()
  const dismissedTrackedRecoveryId = useRef<string>()
  const checkBusy = useRef(false)
  const checkMutation = useMutation({
    mutationKey: ['admin', 'checkRecovery'],
    mutationFn: async (input: WriteInput<'checkRecovery'>) => {
      const submittedScope = scopeRef.current
      const submittedGeneration = scopeGeneration.current
      const response = await api.mutate('checkRecovery', input)
      const stillAuthorized = permissionRef.current && scopeRef.current === submittedScope &&
        scopeGeneration.current === submittedGeneration
      if (stillAuthorized && response.confirmation_token) confirmationToken.current = { id: response.id, token: response.confirmation_token }
      const botId = trustedBotId(client)
      const receiptScope = apiSession?.apiBaseUrl && apiSession.principalId && botId
        ? { apiBaseUrl: apiSession.apiBaseUrl, principalId: apiSession.principalId, botId } : undefined
      const receipt = acceptedReceiptReference('checkRecovery', response)
      if (stillAuthorized && receipt) rememberAcceptedReceipt(receiptScope, receipt)
      return hideConfirmationToken(response)
    },
    retry: false, gcTime: 0, networkMode: 'always',
    onSuccess: () => { void invalidateAfterMutation(client, 'checkRecovery') },
  })
  const submitCheck = async (input: WriteInput<'checkRecovery'>) => {
    if (checkBusy.current) throw new Error('A request is already pending; follow its receipt before submitting again')
    checkBusy.current = true
    try { return await checkMutation.mutateAsync(input) }
    finally { checkBusy.current = false }
  }
  const [checkId, setCheckId] = useState<string>()
  const [resetId, setResetId] = useState<string>()
  const checkIdRef = useRef(checkId)
  checkIdRef.current = checkId
  const [checkedVersion, setCheckedVersion] = useState<number>()
  const actionBusy = useRef(false)
  const [preflightAction, setPreflightAction] = useState<'check' | 'reset' | null>(null)
  const pendingCheck = useRef<WriteInput<'checkRecovery'>>()
  const pendingReset = useRef<Parameters<typeof resetMutation.mutateAsync>[0]>()
  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])
  const assertPermission = () => {
    if (!permissionRef.current) throw new Error('permission_required')
  }
  const assertScope = (startedIn: string, generation: number) => {
    if (!mounted.current || scopeRef.current !== startedIn || scopeGeneration.current !== generation) {
      throw new Error('recovery_session_changed')
    }
  }
  useEffect(() => {
    if (previousScope.current === operationScope) return
    previousScope.current = operationScope
    scopeGeneration.current += 1
    pendingCheck.current = undefined
    pendingReset.current = undefined
    confirmationToken.current = undefined
    setCheckId(undefined)
    setResetId(undefined)
    setCheckedVersion(undefined)
    confirmationToken.current = undefined
    dismissedTrackedRecoveryId.current = undefined
    uncertainOrderIdsRef.current = []
    setUncertainOrderIdsState([])
    setUncertainOrderValidationError(null)
  }, [operationScope])
  const checkReceipt = useSafeRecoveryReceipt(api, checkId, permission, (token, operationId) => {
    if (checkIdRef.current === operationId) confirmationToken.current = { id: operationId, token }
  })
  const resetReceipt = useSafeRecoveryReceipt(api, resetId, permission)
  const observedCheckReceipt = useRef<unknown>()
  const observedResetReceipt = useRef<unknown>()
  useEffect(() => {
    const checkFinished = checkReceipt.data && observedCheckReceipt.current !== checkReceipt.data && receiptFinished('getRecoveryOperation', checkReceipt.data)
    const resetFinished = resetReceipt.data && observedResetReceipt.current !== resetReceipt.data && receiptFinished('getRecoveryOperation', resetReceipt.data)
    if (checkFinished) {
      observedCheckReceipt.current = checkReceipt.data
    }
    if (resetFinished) {
      observedResetReceipt.current = resetReceipt.data
    }
    if (checkFinished || resetFinished) {
      void invalidateAfterReceipt(client, 'getRecoveryOperation')
    }
  }, [checkReceipt.data, client, resetReceipt.data])
  useEffect(() => {
    if (permission && !checkId && !resetId && latestTrackedRecoveryId && latestTrackedRecoveryId !== dismissedTrackedRecoveryId.current) setCheckId(latestTrackedRecoveryId)
  }, [checkId, permission, resetId, latestTrackedRecoveryId])
  // The confirmation token is needed only inside this controller. Clear it from the
  // in-memory query cache after reading each receipt; persisted receipt references
  // contain only kind and operation ID.
  useEffect(() => {
    if (resetId && resetReceipt.data?.confirmation_token) {
      client.setQueryData(adminKeys.query('getRecoveryOperation', { path: { operation_id: resetId } }),
        { ...resetReceipt.data, confirmation_token: null })
    }
  }, [resetId, resetReceipt.data, client])
  useEffect(() => {
    const expiresAt = Date.parse(checkReceipt.data?.expires_at ?? '')
    if (!confirmationToken.current || confirmationToken.current.id !== checkId || !Number.isFinite(expiresAt)) return
    const timeout = window.setTimeout(() => {
      if (Date.now() < expiresAt) return
      confirmationToken.current = undefined
      if (!pendingReset.current) {
        setCheckId(undefined)
        setCheckedVersion(undefined)
      }
    }, Math.max(0, expiresAt - Date.now()))
    return () => window.clearTimeout(timeout)
  }, [checkId, checkReceipt.data?.expires_at])
  const operation = resetReceipt.data ?? checkReceipt.data ?? overview.data?.operation ?? undefined
  const publicOperation = operation ? { ...operation, confirmation_token: null } : undefined
  const publicOverview = overview.data ? { ...overview.data,
    operation: overview.data.operation ? { ...overview.data.operation, confirmation_token: null } : null,
  } : undefined
  const publicCheck = checkReceipt.data ? { ...checkReceipt.data, confirmation_token: null } : undefined
  const publicReset = resetReceipt.data ? { ...resetReceipt.data, confirmation_token: null } : undefined
  const pending = checkMutation.isPending || resetMutation.isPending || Boolean(pendingCheck.current || pendingReset.current || actionBusy.current) || recoveryOperationPending(operation)
  const pendingOperationLock = Boolean(pendingCheck.current || pendingReset.current || checkMutation.isPending || resetMutation.isPending || actionBusy.current)
  const recoveryPending = pendingOperationLock || recoveryOperationPending(operation)

  const scopedProposed = normalizeUncertainOrderRepairs(operation)
  const scopedRepaired = normalizeUncertainOrderRepairs(operation && {
    proposed_uncertain_order: operation.repaired_uncertain_order,
    proposed_uncertain_orders: operation.repaired_uncertain_orders,
  })
  const uncertainOrderCompletionVerified = uncertainOrderIds.length > 0 &&
    operation?.action === 'recovery_reset' && operation.status === 'succeeded' && operation.stage === 'recovered' &&
    exactUncertainOrderRepairs(uncertainOrderIds, scopedRepaired)
  const uncertainOrderSelectionLocked = recoveryPending || Boolean(checkId || resetId)

  const setUncertainOrderIds = (ids: string[]) => {
    const validation = validateUncertainOrderIds(ids)
    if (validation) {
      setUncertainOrderValidationError(validation)
      return
    }
    if (recoveryPending || actionBusy.current || pendingCheck.current || pendingReset.current) {
      setUncertainOrderValidationError('Wait for the current recovery request to finish before changing order IDs.')
      return
    }
    if (uncertainOrderSelectionLocked && ids.length > 0) {
      setUncertainOrderValidationError('Clear the current recovery review before selecting different order IDs.')
      return
    }
    setUncertainOrderValidationError(null)
    uncertainOrderIdsRef.current = [...ids]
    setUncertainOrderIdsState([...ids])
    if (ids.length === 0 || ids.join('\0') !== uncertainOrderIds.join('\0')) {
      if (ids.length === 0) dismissedTrackedRecoveryId.current = latestTrackedRecoveryId
      confirmationToken.current = undefined
      setCheckId(undefined)
      setResetId(undefined)
      setCheckedVersion(undefined)
    }
  }

  const baseCheckDecision = recoveryEntry({ permission, overview: overview.data, status: status.data,
    statusFresh: status.phase === 'ready', operationPending: pending, uncertainOrderScope: uncertainOrderIds.length > 0 })
  const reviewOperation = checkReceipt.data ? { ...checkReceipt.data,
    confirmation_token: (confirmationToken.current?.id === checkReceipt.data.id ? confirmationToken.current.token : undefined) ?? checkReceipt.data.confirmation_token } : undefined
  const baseReviewDecision = reviewRecovery({ permission, overview: overview.data, status: status.data,
    statusFresh: status.phase === 'ready', operation: reviewOperation, uncertainOrderScope: uncertainOrderIds.length > 0 })
  const checkDecision = uncertainOrderValidationError
    ? { allowed: false, reason: 'uncertain_order_ids_invalid' }
    : baseCheckDecision
  const reviewDecision = !uncertainOrderIds.length && scopedProposed.length
    ? { allowed: false, reason: 'uncertain_order_scope_required' }
    : uncertainOrderIds.length && !exactUncertainOrderRepairs(uncertainOrderIds, scopedProposed)
      ? { allowed: false, reason: 'uncertain_order_preview_mismatch' }
      : baseReviewDecision
  const uncertainOrderReviewAvailable = uncertainOrderIds.length > 0 && !uncertainOrderValidationError &&
    baseReviewDecision.allowed && exactUncertainOrderRepairs(uncertainOrderIds, scopedProposed)

  const check = async () => {
    assertPermission()
    if (uncertainOrderValidationError) throw new Error('uncertain_order_ids_invalid')
    if (actionBusy.current) throw new Error('A recovery action is already in progress')
    actionBusy.current = true
    setPreflightAction('check')
    try {
      const startedIn = scopeRef.current
      const generation = scopeGeneration.current
      if (pendingCheck.current || pendingReset.current) throw new Error('A recovery request has an unknown outcome; retry the same request first')
      // Read status immediately before review/submission so the request pins the current control version.
      const fresh = await refreshStatus()
      assertPermission()
      assertScope(startedIn, generation)
      const ids = [...uncertainOrderIdsRef.current]
      const currentOverview = await api.read('getRecovery', uncertainOrderQuery(ids))
      assertPermission()
      assertScope(startedIn, generation)
      client.setQueryData(adminKeys.query('getRecovery', uncertainOrderQuery(ids)), {
        ...currentOverview,
        operation: currentOverview.operation ? hideConfirmationToken(currentOverview.operation) : null,
      })
      const decision = recoveryEntry({ permission: permissionRef.current, overview: currentOverview, status: fresh.data,
        statusFresh: fresh.isSuccess, operationPending: pending, uncertainOrderScope: ids.length > 0 })
      if (!decision.allowed || !fresh.data) throw new Error(decision.reason ?? 'recovery_check_unavailable')
      const input = { body: { expected_version: fresh.data.control_version, ...uncertainOrderRequest(ids) }, idempotencyKey: newKey() }
      pendingCheck.current = input
      let receipt
      try { receipt = await submitCheck(input) }
      catch (error) {
        if (scopeRef.current === startedIn && scopeGeneration.current === generation && pendingCheck.current?.idempotencyKey === input.idempotencyKey && !outcomeMayBeUnknown(error)) pendingCheck.current = undefined
        throw error
      }
      assertScope(startedIn, generation)
      if (pendingCheck.current?.idempotencyKey === input.idempotencyKey) pendingCheck.current = undefined
      assertPermission()
      setResetId(undefined)
      setCheckId(receipt.id)
      setCheckedVersion(fresh.data.control_version)
      setUncertainOrderValidationError(null)
      return hideConfirmationToken(receipt)
    } finally {
      actionBusy.current = false
      setPreflightAction(null)
    }
  }

  const retryCheck = async () => {
    assertPermission()
    if (status.data?.recovery?.next_action === 'wait') throw new Error('shutdown_verification_pending')
    if (actionBusy.current) throw new Error('A recovery action is already in progress')
    actionBusy.current = true
    setPreflightAction('check')
    try {
      const startedIn = scopeRef.current
      const generation = scopeGeneration.current
      if (!pendingCheck.current) throw new Error('No recovery check with an unknown outcome is available to retry')
      const input = pendingCheck.current
      let receipt
      try { receipt = await submitCheck(input) }
      catch (error) {
        if (scopeRef.current === startedIn && scopeGeneration.current === generation && pendingCheck.current?.idempotencyKey === input.idempotencyKey && !outcomeMayBeUnknown(error)) pendingCheck.current = undefined
        throw error
      }
      assertScope(startedIn, generation)
      if (pendingCheck.current?.idempotencyKey === input.idempotencyKey) pendingCheck.current = undefined
      assertPermission()
      setCheckId(receipt.id)
      setCheckedVersion(input.body.expected_version)
      setUncertainOrderValidationError(null)
      return hideConfirmationToken(receipt)
    } finally {
      actionBusy.current = false
      setPreflightAction(null)
    }
  }

  const confirmReset = async (options?: { confirmOrderChanges?: boolean }) => {
    assertPermission()
    if (actionBusy.current) throw new Error('A recovery action is already in progress')
    actionBusy.current = true
    setPreflightAction('reset')
    try {
      const startedIn = scopeRef.current
      const generation = scopeGeneration.current
      if (pendingCheck.current || pendingReset.current) throw new Error('A recovery request has an unknown outcome; retry the same request first')
      if (!checkId) throw new Error('recovery_check_not_reviewable')
      const [freshStatus, freshOverview, freshCheck] = await Promise.all([
        refreshStatus(), overview.refetch(), checkReceipt.refetch(),
      ])
      assertPermission()
      assertScope(startedIn, generation)
      const reviewedCheck = freshCheck.data
      const token = reviewedCheck?.confirmation_token ?? (confirmationToken.current && reviewedCheck &&
        confirmationToken.current.id === reviewedCheck.id ? confirmationToken.current.token : undefined)
      const ids = uncertainOrderIdsRef.current
      const decision = reviewRecovery({ permission: permissionRef.current, overview: freshOverview.data, status: freshStatus.data,
        statusFresh: freshStatus.isSuccess, operation: reviewedCheck ? { ...reviewedCheck,
          confirmation_token: token ?? null } : undefined, uncertainOrderScope: ids.length > 0 })
      if (!decision.allowed || !freshStatus.data || !token || !reviewedCheck) {
        confirmationToken.current = undefined
        setCheckId(undefined)
        throw new Error(decision.reason ?? 'recovery_preview_invalid')
      }
      if (freshOverview.data?.operation?.id && freshOverview.data.operation.id !== reviewedCheck.id) {
        confirmationToken.current = undefined
        setCheckId(undefined)
        throw new Error('recovery_preview_superseded')
      }
      if (checkedVersion === undefined || freshStatus.data.control_version !== checkedVersion) {
        confirmationToken.current = undefined
        setCheckId(undefined)
        throw new Error('control_version_changed')
      }
      const proposed = normalizeUncertainOrderRepairs(reviewedCheck)
      if (ids.length && !exactUncertainOrderRepairs(ids, proposed)) {
        confirmationToken.current = undefined
        setCheckId(undefined)
        throw new Error('uncertain_order_preview_mismatch')
      }
      if (!ids.length && proposed.length) throw new Error('uncertain_order_scope_required')
      if (ids.length && options?.confirmOrderChanges !== true) throw new Error('uncertain_order_confirmation_required')
      const input = { body: {
        expected_version: checkedVersion,
        check_id: reviewedCheck.id,
        confirmation_token: token,
        confirm_order_changes: ids.length > 0,
        ...uncertainOrderRequest(ids),
      }, idempotencyKey: newKey() }
      pendingReset.current = input
      let receipt
      try { receipt = await resetMutation.mutateAsync(input) }
      catch (error) {
        if (scopeRef.current === startedIn && scopeGeneration.current === generation && pendingReset.current?.idempotencyKey === input.idempotencyKey && !outcomeMayBeUnknown(error)) {
          pendingReset.current = undefined
          confirmationToken.current = undefined
          setCheckId(undefined)
        }
        throw error
      }
      assertScope(startedIn, generation)
      if (pendingReset.current?.idempotencyKey === input.idempotencyKey) pendingReset.current = undefined
      assertPermission()
      confirmationToken.current = undefined
      setCheckId(undefined)
      setResetId(receipt.id)
      setUncertainOrderValidationError(null)
      return hideConfirmationToken(receipt)
    } finally {
      actionBusy.current = false
      setPreflightAction(null)
    }
  }

  const retryReset = async () => {
    assertPermission()
    if (status.data?.recovery?.next_action === 'wait') throw new Error('shutdown_verification_pending')
    if (actionBusy.current) throw new Error('A recovery action is already in progress')
    actionBusy.current = true
    setPreflightAction('reset')
    try {
      const startedIn = scopeRef.current
      const generation = scopeGeneration.current
      if (!pendingReset.current) throw new Error('No recovery reset with an unknown outcome is available to retry')
      let receipt
      const input = pendingReset.current
      try { receipt = await resetMutation.mutateAsync(input) }
      catch (error) {
        if (scopeRef.current === startedIn && scopeGeneration.current === generation && pendingReset.current?.idempotencyKey === input.idempotencyKey && !outcomeMayBeUnknown(error)) {
          pendingReset.current = undefined
          confirmationToken.current = undefined
          setCheckId(undefined)
        }
        throw error
      }
      assertScope(startedIn, generation)
      if (pendingReset.current?.idempotencyKey === input.idempotencyKey) pendingReset.current = undefined
      assertPermission()
      setResetId(receipt.id)
      setUncertainOrderValidationError(null)
      return hideConfirmationToken(receipt)
    } finally {
      actionBusy.current = false
      setPreflightAction(null)
    }
  }

  const checkFeedback = deriveFormFeedback(checkMutation.error, 'recovery')
  const resetFeedback = deriveFormFeedback(resetMutation.error, 'recovery')
  const viewModel = deriveRecoveryViewModel({
    permission,
    uncertainOrders: {
      ids: uncertainOrderIds,
      validationError: uncertainOrderValidationError,
      proposed: scopedProposed,
      repaired: scopedRepaired,
      completionVerified: uncertainOrderCompletionVerified,
      reviewAvailable: uncertainOrderReviewAvailable,
      selectionLocked: uncertainOrderSelectionLocked,
    },
    summary: publicOverview,
    status: status.data,
    operation: publicOperation,
    checkDecision,
    reviewDecision,
    canRetryCheck: Boolean(pendingCheck.current),
    canRetryReset: Boolean(pendingReset.current),
    isChecking: checkMutation.isPending || preflightAction === 'check',
    isResetting: resetMutation.isPending || preflightAction === 'reset',
    checkError: checkMutation.error instanceof Error ? checkMutation.error.message : undefined,
    resetError: resetMutation.error instanceof Error ? resetMutation.error.message : undefined,
    fresh: status.phase === 'ready',
    now,
  })
  return {
    activate,
    viewModel,
    presentation: deriveRecoveryPresentation(status.data, status.phase === 'ready', now),
    verification: deriveRecoveryVerification(publicOperation),
    overview: { data: publicOverview, error: overview.error, isLoading: overview.isLoading, isFetching: overview.isFetching, refetch: async () => {
      const { data, ...result } = await overview.refetch()
      return { ...result, data: data ? { ...data, operation: data.operation ? hideConfirmationToken(data.operation) : null } : undefined }
    } },
    status, operation: publicOperation, checkReceipt: { data: publicCheck, error: checkReceipt.error, isLoading: checkReceipt.isLoading, isFetching: checkReceipt.isFetching },
    resetReceipt: { data: publicReset, error: resetReceipt.error, isLoading: resetReceipt.isLoading, isFetching: resetReceipt.isFetching },
    check, retryCheck, confirmReset, retryReset, checkDecision, reviewDecision,
    uncertainOrders: {
      ids: uncertainOrderIds,
      setIds: setUncertainOrderIds,
      validationError: uncertainOrderValidationError,
      proposed: scopedProposed,
      repaired: scopedRepaired,
      completionVerified: uncertainOrderCompletionVerified,
      reviewAvailable: uncertainOrderReviewAvailable,
      selectionLocked: uncertainOrderSelectionLocked,
    },
    canRetryCheck: Boolean(pendingCheck.current), canRetryReset: Boolean(pendingReset.current),
    access: { allowed: permission, detail: permission ? 'manage_recovery permission granted.' : 'Contact an administrator with manage_recovery permission to inspect and recover.' },
    comparison: buildRecoveryComparison(operation?.proposed_changes),
    feedback: { check: checkFeedback, reset: resetFeedback },
    checkAvailable: checkDecision.allowed,
    reviewAvailable: reviewDecision.allowed,
    operationPending: pending,
    lockSessionAndConnections: pending || pendingOperationLock,
    completed: uncertainOrderIds.length ? uncertainOrderCompletionVerified : recoveryComplete(resetReceipt.data ?? checkReceipt.data),
    trackedReceipts: trackedReceipts.filter((receipt) => receipt.kind === 'recovery'),
    refreshingStatus: status.refreshing,
    refresh: async () => {
      const [recovery, runtime] = await Promise.all([
        overview.refetch(),
        refreshStatus(),
        checkId ? checkReceipt.refetch() : Promise.resolve(undefined),
        resetId ? resetReceipt.refetch() : Promise.resolve(undefined),
      ])
      return [{ ...recovery, data: recovery.data ? { ...recovery.data,
        operation: recovery.data.operation ? hideConfirmationToken(recovery.data.operation) : null } : undefined }, runtime] as const
    },
    refreshStatus,
  }
}
