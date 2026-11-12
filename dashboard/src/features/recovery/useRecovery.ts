import { useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useAuth } from '../../core/auth/auth-context'
import { ApiError } from '../../core/api/client'
import { useApiSessionScope } from '../../core/api/ApiSessionContext'
import { useAdminMutation, useAdminQuery } from '../../core/api/hooks'
import { adminKeys } from '../../core/api/queries'
import { buildRecoveryComparison } from './comparison'
import { deriveFormFeedback } from '../../core/api/operation-feedback'
import { useTrackedReceipts } from '../../core/api/useTrackedReceipts'
import { useOperationalContext } from '../operational-context/useOperationalContext'
import { recoveryComplete, recoveryEntry, recoveryOperationPending, reviewRecovery } from './recovery'

function newKey() { return crypto.randomUUID() }
function hideConfirmationToken<T extends { confirmation_token: string | null }>(operation: T): T {
  return { ...operation, confirmation_token: null }
}
function outcomeMayBeUnknown(error: unknown): boolean {
  if (!(error instanceof ApiError)) return true
  return error.status === 408 || error.status >= 500 || error.retry === 'same_request' || error.retry === 'wait'
}

/** Recovery controller. Confirmation tokens remain in a private ref and are never journaled. */
export function useRecovery() {
  const { identity } = useAuth()
  const operational = useOperationalContext()
  const permission = Boolean(identity?.permissions.manage_recovery)
  const overview = useAdminQuery('getRecovery', {}, { enabled: permission })
  const status = operational.status
  const refreshStatus = operational.refreshStatus
  const checkMutation = useAdminMutation('checkRecovery')
  const resetMutation = useAdminMutation('resetRecovery')
  const client = useQueryClient()
  const apiSession = useApiSessionScope()
  const scope = `${apiSession?.apiBaseUrl ?? ''}\0${apiSession?.principalId ?? ''}\0${operational.botId ?? ''}`
  const scopeRef = useRef(scope)
  const previousScope = useRef(scope)
  const scopeGeneration = useRef(0)
  const mounted = useRef(true)
  const permissionRef = useRef(Boolean(identity?.permissions.manage_recovery))
  scopeRef.current = scope
  permissionRef.current = Boolean(identity?.permissions.manage_recovery)
  const trackedReceipts = useTrackedReceipts()
  const latestTrackedRecovery = trackedReceipts.find((receipt) => receipt.kind === 'recovery')
  const latestTrackedRecoveryId = latestTrackedRecovery?.id
  const confirmationToken = useRef<string>()
  const [checkId, setCheckId] = useState<string>()
  const [resetId, setResetId] = useState<string>()
  const [checkedVersion, setCheckedVersion] = useState<number>()
  const pendingCheck = useRef<Parameters<typeof checkMutation.mutateAsync>[0]>()
  const pendingReset = useRef<Parameters<typeof resetMutation.mutateAsync>[0]>()
  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      pendingCheck.current = undefined
      pendingReset.current = undefined
      confirmationToken.current = undefined
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
    if (previousScope.current === scope) return
    previousScope.current = scope
    scopeGeneration.current += 1
    pendingCheck.current = undefined
    pendingReset.current = undefined
    confirmationToken.current = undefined
    setCheckId(undefined)
    setResetId(undefined)
    setCheckedVersion(undefined)
  }, [scope])
  const checkReceipt = useAdminQuery('getRecoveryOperation', checkId ? { path: { operation_id: checkId } } : undefined, { enabled: permission })
  const resetReceipt = useAdminQuery('getRecoveryOperation', resetId ? { path: { operation_id: resetId } } : undefined, { enabled: permission })
  useEffect(() => {
    if (!checkId && !resetId && latestTrackedRecoveryId) setCheckId(latestTrackedRecoveryId)
  }, [checkId, resetId, latestTrackedRecoveryId])
  // The confirmation token is needed only inside this controller. Clear it from the
  // in-memory query cache after reading each receipt; persisted receipt references
  // contain only kind and operation ID.
  useEffect(() => {
    if (checkId && checkReceipt.data?.confirmation_token) {
      confirmationToken.current = checkReceipt.data.confirmation_token
      client.setQueryData(adminKeys.query('getRecoveryOperation', { path: { operation_id: checkId } }),
        { ...checkReceipt.data, confirmation_token: null })
    }
  }, [checkId, checkReceipt.data, client])
  useEffect(() => {
    if (resetId && resetReceipt.data?.confirmation_token) {
      client.setQueryData(adminKeys.query('getRecoveryOperation', { path: { operation_id: resetId } }),
        { ...resetReceipt.data, confirmation_token: null })
    }
  }, [resetId, resetReceipt.data, client])
  useEffect(() => {
    if (overview.data?.operation?.confirmation_token) {
      client.setQueryData(adminKeys.query('getRecovery', {}), { ...overview.data,
        operation: { ...overview.data.operation, confirmation_token: null } })
    }
  }, [overview.data, client])
  const operation = resetReceipt.data ?? checkReceipt.data ?? overview.data?.operation ?? undefined
  const publicOperation = operation ? { ...operation, confirmation_token: null } : undefined
  const publicOverview = overview.data ? { ...overview.data,
    operation: overview.data.operation ? { ...overview.data.operation, confirmation_token: null } : null,
  } : undefined
  const publicCheck = checkReceipt.data ? { ...checkReceipt.data, confirmation_token: null } : undefined
  const publicReset = resetReceipt.data ? { ...resetReceipt.data, confirmation_token: null } : undefined
  const pending = checkMutation.isPending || resetMutation.isPending || Boolean(pendingCheck.current || pendingReset.current) || recoveryOperationPending(operation)

  const checkDecision = recoveryEntry({ permission, overview: overview.data, status: status.data,
    statusFresh: status.phase === 'ready', operationPending: pending })
  const reviewOperation = checkReceipt.data ? { ...checkReceipt.data,
    confirmation_token: confirmationToken.current ?? checkReceipt.data.confirmation_token } : undefined
  const reviewDecision = reviewRecovery({ permission, overview: overview.data, status: status.data,
    statusFresh: status.phase === 'ready', operation: reviewOperation })

  const check = async () => {
    assertPermission()
    const startedIn = scopeRef.current
    const generation = scopeGeneration.current
    if (pendingCheck.current || pendingReset.current) throw new Error('A recovery request has an unknown outcome; retry the same request first')
    // Read status immediately before review/submission so the request pins the current control version.
    const fresh = await refreshStatus()
    assertPermission()
    assertScope(startedIn, generation)
    const currentOverview = await overview.refetch()
    assertPermission()
    assertScope(startedIn, generation)
    const decision = recoveryEntry({ permission: permissionRef.current, overview: currentOverview.data, status: fresh.data,
      statusFresh: fresh.isSuccess, operationPending: pending })
    if (!decision.allowed || !fresh.data) throw new Error(decision.reason ?? 'recovery_check_unavailable')
    const input = { body: { expected_version: fresh.data.control_version }, idempotencyKey: newKey() }
    pendingCheck.current = input
    let receipt
    try { receipt = await checkMutation.mutateAsync(input) }
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
    return hideConfirmationToken(receipt)
  }

  const retryCheck = async () => {
    assertPermission()
    const startedIn = scopeRef.current
    const generation = scopeGeneration.current
    if (!pendingCheck.current) throw new Error('No recovery check with an unknown outcome is available to retry')
    const input = pendingCheck.current
    let receipt
    try { receipt = await checkMutation.mutateAsync(input) }
    catch (error) {
      if (scopeRef.current === startedIn && scopeGeneration.current === generation && pendingCheck.current?.idempotencyKey === input.idempotencyKey && !outcomeMayBeUnknown(error)) pendingCheck.current = undefined
      throw error
    }
    assertScope(startedIn, generation)
    if (pendingCheck.current?.idempotencyKey === input.idempotencyKey) pendingCheck.current = undefined
    assertPermission()
    setCheckId(receipt.id)
    setCheckedVersion(input.body.expected_version)
    return hideConfirmationToken(receipt)
  }

  const confirmReset = async () => {
    assertPermission()
    const startedIn = scopeRef.current
    const generation = scopeGeneration.current
    if (pendingCheck.current || pendingReset.current) throw new Error('A recovery request has an unknown outcome; retry the same request first')
    const [freshStatus, freshOverview, freshCheck] = await Promise.all([
      refreshStatus(), overview.refetch(), checkReceipt.refetch(),
    ])
    assertPermission()
    assertScope(startedIn, generation)
    const reviewedCheck = freshCheck.data
    const token = reviewedCheck?.confirmation_token ?? confirmationToken.current
    const decision = reviewRecovery({ permission: permissionRef.current, overview: freshOverview.data, status: freshStatus.data,
      statusFresh: freshStatus.isSuccess, operation: reviewedCheck ? { ...reviewedCheck,
        confirmation_token: token ?? null } : undefined })
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
    const input = { body: {
      expected_version: checkedVersion,
      check_id: reviewedCheck.id,
      confirmation_token: token,
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
    return hideConfirmationToken(receipt)
  }

  const retryReset = async () => {
    assertPermission()
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
    return hideConfirmationToken(receipt)
  }

  return {
    overview: { data: publicOverview, error: overview.error, isLoading: overview.isLoading, isFetching: overview.isFetching, refetch: async () => {
      const { data, ...result } = await overview.refetch()
      return { ...result, data: data ? { ...data, operation: data.operation ? hideConfirmationToken(data.operation) : null } : undefined }
    } },
    status, operation: publicOperation, checkReceipt: { data: publicCheck, error: checkReceipt.error, isLoading: checkReceipt.isLoading, isFetching: checkReceipt.isFetching },
    resetReceipt: { data: publicReset, error: resetReceipt.error, isLoading: resetReceipt.isLoading, isFetching: resetReceipt.isFetching },
    check, retryCheck, confirmReset, retryReset, checkDecision, reviewDecision,
    canRetryCheck: Boolean(pendingCheck.current), canRetryReset: Boolean(pendingReset.current),
    access: { allowed: permission, detail: permission ? 'manage_recovery permission granted.' : 'Contact an administrator with manage_recovery permission to inspect and recover.' },
    comparison: buildRecoveryComparison(operation?.proposed_changes),
    feedback: { check: deriveFormFeedback(checkMutation.error, 'recovery'), reset: deriveFormFeedback(resetMutation.error, 'recovery') },
    checkAvailable: checkDecision.allowed,
    reviewAvailable: reviewDecision.allowed,
    operationPending: pending,
    lockSessionAndConnections: pending,
    completed: recoveryComplete(resetReceipt.data ?? checkReceipt.data),
    trackedReceipts: trackedReceipts.filter((receipt) => receipt.kind === 'recovery'),
    refreshingStatus: status.refreshing,
    refresh: async () => {
      const [recovery, runtime] = await Promise.all([overview.refetch(), refreshStatus()])
      return [{ ...recovery, data: recovery.data ? { ...recovery.data,
        operation: recovery.data.operation ? hideConfirmationToken(recovery.data.operation) : null } : undefined }, runtime] as const
    },
    refreshStatus,
  }
}
