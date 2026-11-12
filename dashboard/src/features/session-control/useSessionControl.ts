import { useRef } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import type { WriteInput } from '../../core/api/admin'
import { useApiSessionScope } from '../../core/api/ApiSessionContext'
import { useAdminMutation, useAdminQuery, useReceiptInspection } from '../../core/api/hooks'
import { clearPendingOperation, readPendingOperation, savePendingOperation } from '../../core/api/operation-journal'
import { trustedBotId } from '../../core/api/trustedBotId'
import { useTrackedReceipts } from '../../core/api/useTrackedReceipts'
import { deriveFormFeedback, operationFeedback } from '../../core/api/operation-feedback'
import { useAuth } from '../../core/auth/auth-context'
import { useOperationalContext } from '../operational-context/useOperationalContext'
import { makeStopCommand, reviewSession, type SessionDraft, type SessionReview } from './session-control'

const commandChannel = 'session'
const stopChannel = 'stop'
function newKey() { return crypto.randomUUID() }

export function useSessionControl(options: { includeProfiles?: boolean; trackCommands?: boolean } = {}) {
  const { identity } = useAuth()
  const { status: sharedStatus, refreshStatus, recoveryLock } = useOperationalContext()
  const status = sharedStatus
  const profiles = useAdminQuery('listProfiles', {}, { enabled: options.includeProfiles !== false })
  const startOrSwitch = useAdminMutation('submitCommand')
  const stopMutation = useAdminMutation('submitCommand')
  const client = useQueryClient()
  const session = useApiSessionScope()
  const botId = trustedBotId(client)
  const scope = session?.apiBaseUrl && session.principalId && botId
    ? { apiBaseUrl: session.apiBaseUrl, principalId: session.principalId, botId } : undefined
  const scopeIdentity = JSON.stringify([scope?.apiBaseUrl, scope?.principalId, scope?.botId])
  const scopeMarker = useRef(scopeIdentity)
  const inMemorySessionRetry = useRef<WriteInput<'submitCommand'> | undefined>(undefined)
  const inMemoryStopRetry = useRef<WriteInput<'submitCommand'> | undefined>(undefined)
  if (scopeMarker.current !== scopeIdentity) {
    inMemorySessionRetry.current = undefined
    inMemoryStopRetry.current = undefined
    scopeMarker.current = scopeIdentity
  }
  const storedSessionRetry = readPendingOperation(scope, commandChannel)
  const pendingRetry = storedSessionRetry ?? (inMemorySessionRetry.current ? { operation: 'submitCommand' as const, input: inMemorySessionRetry.current } : undefined)
  const storedStopRetry = readPendingOperation(scope, stopChannel)
  const stopPendingRetry = storedStopRetry ?? (inMemoryStopRetry.current ? { operation: 'submitCommand' as const, input: inMemoryStopRetry.current } : undefined)
  const trackedReceipts = useTrackedReceipts()
  const trackedCommand = trackedReceipts.find((reference) => reference.kind === 'command')
  const latestCommandId = options.trackCommands === false ? undefined : trackedCommand?.id ?? startOrSwitch.data?.id
  const latestStopId = stopMutation.data?.id
  const commandReceipt = useReceiptInspection(latestCommandId ? { kind: 'command', id: latestCommandId } : null)
  const stopReceipt = useReceiptInspection(latestStopId ? { kind: 'command', id: latestStopId } : null)
  const stopProcessing = stopReceipt?.status === 'accepted' || stopReceipt?.status === 'applying'
  const stopBlocksNew = Boolean(stopMutation.data && !['succeeded', 'failed', 'superseded'].includes(stopReceipt?.status ?? '')) || Boolean(stopPendingRetry)
  const commandProcessing = commandReceipt?.status === 'accepted' || commandReceipt?.status === 'applying'
  const commandTerminal = ['succeeded', 'failed', 'superseded'].includes(commandReceipt?.status ?? '')
  const commandBlocksNew = Boolean(pendingRetry) || Boolean(latestCommandId && !commandTerminal)

  const review = (action: 'start' | 'switch', draft: SessionDraft) => recoveryLock?.active
    ? { review: undefined, errors: { action: 'recovery_in_progress' } } : reviewSession({
    action, draft, status: status.data, profiles: profiles.data,
    permission: Boolean(identity?.permissions.operate), statusAvailable: status.phase === 'ready',
  })
  const send = async (input: WriteInput<'submitCommand'>) => {
    const key = input.idempotencyKey
    const submittedScopeId = scopeIdentity
    if (!savePendingOperation(scope, 'submitCommand', input)) {
      const existing = readPendingOperation(scope, 'session')
      if (existing && existing.input.idempotencyKey !== key) throw new Error('A session command is unresolved')
      inMemorySessionRetry.current = input
    }
    try {
      const receipt = await startOrSwitch.mutateAsync(input)
      clearPendingOperation(scope, 'session', key)
      if (scopeMarker.current === submittedScopeId && inMemorySessionRetry.current?.idempotencyKey === key) inMemorySessionRetry.current = undefined
      return receipt
    } catch (error) {
      const guidance = operationFeedback(error)?.retry
      if (guidance === 'fix_request' || guidance === 'refresh_state') {
        clearPendingOperation(scope, 'session', key)
        if (scopeMarker.current === submittedScopeId && inMemorySessionRetry.current?.idempotencyKey === key) inMemorySessionRetry.current = undefined
      }
      throw error
    }
  }
  const submitReviewed = (reviewed: SessionReview) => {
    if (recoveryLock?.active) throw new Error('recovery_in_progress')
    if (!identity?.permissions.operate || status.phase !== 'ready' || !status.data || !profiles.isSuccess ||
        status.data.control_version !== reviewed.reviewedAtVersion || profiles.data.catalogue_hash !== reviewed.catalogueHash) {
      throw new Error('Reviewed state changed; refresh and review the operation again')
    }
    const actionAvailability = status.data.actions?.[reviewed.action]
    if (!actionAvailability) throw new Error('availability_unknown')
    if (!actionAvailability.allowed) throw new Error(actionAvailability.reason_code ?? 'action_unavailable')
    if (commandBlocksNew) throw new Error('A prior session command is unresolved; follow its receipt or retry the same request first')
    return send({ body: reviewed.body, idempotencyKey: newKey() })
  }
  const stop = async () => {
    if (!identity?.permissions.operate) throw new Error('permission_required')
    if (stopBlocksNew) throw new Error('A Stop request is unresolved; follow its receipt or retry the same request')
    const refreshed = await refreshStatus()
    if (refreshed.isError || !refreshed.data) throw new Error('status_unavailable')
    const command = makeStopCommand(refreshed.data, true, true)
    if (!command.body) throw new Error(command.error)
    const input = { body: command.body, idempotencyKey: newKey() }
    const key = input.idempotencyKey
    const submittedScopeId = scopeIdentity
    if (!savePendingOperation(scope, 'submitCommand', input)) {
      const existing = readPendingOperation(scope, 'stop')
      if (existing && existing.input.idempotencyKey !== key) throw new Error('A Stop operation is unresolved')
      inMemoryStopRetry.current = input
    }
    try {
      const receipt = await stopMutation.mutateAsync(input)
      clearPendingOperation(scope, 'stop', key)
      if (scopeMarker.current === submittedScopeId && inMemoryStopRetry.current?.idempotencyKey === key) inMemoryStopRetry.current = undefined
      return receipt
    } catch (error) {
      const guidance = operationFeedback(error)?.retry
      if (guidance === 'fix_request' || guidance === 'refresh_state') {
        clearPendingOperation(scope, 'stop', key)
        if (scopeMarker.current === submittedScopeId && inMemoryStopRetry.current?.idempotencyKey === key) inMemoryStopRetry.current = undefined
      }
      throw error
    }
  }
  const retryPending = () => {
    const retryAt = operationFeedback(startOrSwitch.error)?.retryAt
    if (retryAt && Date.now() < retryAt) throw new Error('operation_cooldown')
    if (recoveryLock?.active) throw new Error('recovery_in_progress')
    if (!identity?.permissions.operate) throw new Error('permission_required')
    const candidate = readPendingOperation(scope, commandChannel) ?? (inMemorySessionRetry.current ? { operation: 'submitCommand' as const, input: inMemorySessionRetry.current } : undefined)
    if (!candidate || candidate.operation !== 'submitCommand' || candidate.input.body.action === 'stop') throw new Error('No session command is available to retry')
    return startOrSwitch.mutateAsync(candidate.input).then((receipt) => {
      clearPendingOperation(scope, 'session', candidate.input.idempotencyKey)
      inMemorySessionRetry.current = undefined
      return receipt
    })
  }
  const retryStop = () => {
    const retryAt = operationFeedback(stopMutation.error)?.retryAt
    if (retryAt && Date.now() < retryAt) throw new Error('operation_cooldown')
    if (!identity?.permissions.operate) throw new Error('permission_required')
    const candidate = readPendingOperation(scope, stopChannel) ?? (inMemoryStopRetry.current ? { operation: 'submitCommand' as const, input: inMemoryStopRetry.current } : undefined)
    if (!candidate || candidate.operation !== 'submitCommand') throw new Error('No Stop operation is available to retry')
    return stopMutation.mutateAsync(candidate.input).then((receipt) => {
      clearPendingOperation(scope, 'stop', candidate.input.idempotencyKey)
      inMemoryStopRetry.current = undefined
      return receipt
    })
  }
  return {
    status, profiles, review, submitReviewed, stop, retryPending, retryStop,
    clearReviewedFeedback: () => {
      if (!startOrSwitch.isPending && ['fix_request', 'refresh_state'].includes(operationFeedback(startOrSwitch.error)?.retry ?? '')) startOrSwitch.reset()
    },
    pendingRetry, stopPendingRetry, trackedReceipts, commandReceipt, commandProcessing, commandBlocksNew, stopReceipt, stopProcessing, stopBlocksNew,
    formFeedback: deriveFormFeedback(startOrSwitch.error, 'session'), stopFormFeedback: deriveFormFeedback(stopMutation.error, 'session'),
    feedback: operationFeedback(startOrSwitch.error), stopFeedback: operationFeedback(stopMutation.error),
    submitting: startOrSwitch.isPending, stopping: stopMutation.isPending,
    refreshStatus, refreshProfiles: profiles.refetch,
  }
}
