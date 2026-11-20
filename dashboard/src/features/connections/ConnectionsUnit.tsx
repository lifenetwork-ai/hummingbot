import { useState } from 'react'
import { notify } from '../../core/feedback/notify'
import { deriveFormFeedback, type FormFeedbackModel } from '../../core/api/operation-feedback'
import type { useConnectionsController } from './useConnectionsController'
import { useConnectionsUnitActivation, useConnectionsUnitContext } from './connections-unit-context'
import { ManagedCredentialsView } from '../admin/ManagedCredentialsView'
import { DisconnectConfirmationDialog } from '../admin/DisconnectConfirmationDialog'

type CredentialSlot = ReturnType<typeof useConnectionsController>['slots'][number]
type DisconnectSelection = Pick<CredentialSlot, 'exchange' | 'connector'> & { idempotencyKey: string }

const newKey = () => crypto.randomUUID()

/** Connections workflow container: owns its visible state and connects it to the persistent operation owner. */
export function ConnectionsUnit() {
  useConnectionsUnitActivation()
  const { controller: connections } = useConnectionsUnitContext()
  const [disconnectSlot, setDisconnectSlot] = useState<DisconnectSelection | null>(null)
  const [disconnectError, setDisconnectError] = useState<string | null>(null)
  const [credentialFeedback, setCredentialFeedback] = useState<Record<string, FormFeedbackModel | null>>({})

  const runCredential = async (exchange: string, connector: string, submit: () => Promise<unknown>) => {
    const key = `${exchange}:${connector}`
    setCredentialFeedback((previous) => ({ ...previous, [key]: null }))
    try { return await submit() }
    catch (error) {
      const names = connections.slots.find((slot) => slot.exchange === exchange && slot.connector === connector)?.fields.map((field) => field.name) ?? []
      setCredentialFeedback((previous) => ({ ...previous, [key]: deriveFormFeedback(error, 'credentials', names) }))
      throw error
    }
  }

  const confirmDisconnect = async () => {
    if (!disconnectSlot) return
    const selected = disconnectSlot
    setDisconnectError(null)
    notify.loading('Disconnecting API Key...', { id: 'conn-disconnect' })
    try {
      await runCredential(selected.exchange, selected.connector, () => connections.disconnect(selected))
      setDisconnectSlot(null)
      notify.info('Disconnect receipt received — cleanup not yet verified', { id: 'conn-disconnect' })
    } catch (error) {
      setDisconnectError(error instanceof Error ? error.message : 'Unable to disconnect this credential slot.')
      notify.dismiss('conn-disconnect')
    }
  }

  const disconnectLockReason = (() => {
    if (!disconnectSlot) return null
    const slot = connections.slots.find((item) => item.exchange === disconnectSlot.exchange && item.connector === disconnectSlot.connector)
    const decision = slot?.decisions.disconnect
    return decision && !decision.allowed ? decision.detail || decision.reasonCode || null : null
  })()

  const canManage = connections.access.allowed
  return <>
    <ManagedCredentialsView
      catalogue={connections.catalogue.data}
      feedbackBySlot={credentialFeedback}
      slots={connections.slots}
      locks={connections.decisionLocks}
      canManage={canManage}
      statusError={connections.catalogue.error?.message ?? connections.runtime.error?.message}
      isLoading={connections.catalogue.phase === 'loading' || (connections.catalogue.data?.exchanges.length ?? 0) > 0 && connections.slots.length === 0}
      isSubmitting={connections.operationPending}
      onRefresh={connections.refresh}
      onSetCredentials={(exchange, connector, values, idempotencyKey) =>
        runCredential(exchange, connector, () => connections.set({ exchange, connector, values, idempotencyKey })).then(() => undefined)}
      onRetryCleanup={(exchange, connector, idempotencyKey) => runCredential(exchange, connector, () => connections.retryCleanup({ exchange, connector, idempotencyKey }))}
      onRepairCleanup={(exchange, connector, values, idempotencyKey) => runCredential(exchange, connector, () => connections.repairCleanupKey({ exchange, connector, values, idempotencyKey }))}
      onReviewUnknown={connections.reviewUnknown}
      onOpenDisconnect={(exchange, connector, retryKey) => setDisconnectSlot({ exchange, connector, idempotencyKey: retryKey ?? newKey() })}
    />
    <DisconnectConfirmationDialog
      open={Boolean(disconnectSlot)}
      onOpenChange={(open) => { if (!open) { setDisconnectSlot(null); setDisconnectError(null) } }}
      connector={disconnectSlot?.connector}
      isSubmitting={connections.mutations.disconnect.isPending}
      error={disconnectError}
      lockReason={disconnectLockReason}
      onConfirmDisconnect={confirmDisconnect}
    />
  </>
}
