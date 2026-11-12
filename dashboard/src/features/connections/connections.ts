import type { ResponseBody } from '../../core/api/types'

export type Catalogue = ResponseBody<'listCredentialInputs'>
export type CredentialStatus = ResponseBody<'getExchangeCredentialStatus'>
export type RuntimeStatus = ResponseBody<'getRuntimeStatus'>
export type Identity = ResponseBody<'getAdminIdentity'>
export type Slot = CredentialStatus['slots'][number]
export type CredentialAction = 'configure' | 'disconnect' | 'retry_cleanup' | 'repair_cleanup_key'
export type Decision = { allowed: boolean; reasonCode: string | null; detail: string }
export type Source<T> = { phase: 'loading' | 'ready' | 'error'; data?: T; error: Error | null; refreshing: boolean; updatedAt?: number }

function denied(reasonCode: string, detail: string): Decision {
  return { allowed: false, reasonCode, detail }
}

function actionDecision(identity: Identity | null, actions: Slot['actions'] | undefined, action: CredentialAction): Decision {
  if (!identity?.permissions.manage_credentials) return denied('permission_required', 'This action requires manage_credentials permission.')
  const decision = actions?.[action]
  if (!decision) return denied('availability_unknown', 'The server has not provided availability for this action.')
  return { allowed: decision.allowed, reasonCode: decision.reason_code, detail: decision.detail }
}

/** Server action capabilities and permission are both required; role labels are informational only. */
export function deriveCredentialSlot(identity: Identity | null, catalogue: Source<Catalogue>, status: Source<CredentialStatus>, runtime: Source<RuntimeStatus>, exchange: string, connector: string) {
  const descriptor = catalogue.data?.exchanges.find((item) => item.exchange === exchange)
  const slot = status.data?.slots.find((item) => item.connector === connector)
  const currentRuntime = runtime.phase === 'ready' ? runtime.data : undefined
  const observation = currentRuntime?.observation
  const runtimeFresh = Boolean(currentRuntime?.owner_available && observation?.observed_at && !observation.stale && observation.session_matches)
  const slotObservedAt = Date.parse(slot?.observed_at ?? '')
  const runtimeServerTime = Date.parse(currentRuntime?.server_time ?? '')
  const now = Date.now()
  const cacheFresh = (updatedAt: number | undefined) => updatedAt === undefined ||
    (Number.isFinite(updatedAt) && now >= updatedAt && now - updatedAt <= 5000)
  const slotEvidenceFresh = Number.isFinite(slotObservedAt) && Number.isFinite(runtimeServerTime) &&
    runtimeServerTime >= slotObservedAt && runtimeServerTime - slotObservedAt <= 5000 &&
    cacheFresh(status.updatedAt) && cacheFresh(runtime.updatedAt)
  const stopAcknowledged = Boolean(runtimeFresh && currentRuntime && currentRuntime.desired_state === 'stopped' &&
    currentRuntime.acknowledged_version >= currentRuntime.control_version && currentRuntime.operator?.state === 'stopped')
  const managed = catalogue.data?.source === 'postgres_kms' && status.data?.source === 'postgres_kms'
  const action = (name: CredentialAction) => actionDecision(identity, slot?.actions, name)
  const configure = action('configure')
  const disconnect = action('disconnect')
  const retry = action('retry_cleanup')
  const repair = action('repair_cleanup_key')
  const setDecision = !managed ? denied('managed_source_required', 'Credentials are managed by deployment.')
    : catalogue.phase !== 'ready' || status.phase !== 'ready' ? denied('credential_status_unavailable', 'Refresh credential catalogue and status before changing this connection.')
    : !descriptor ? denied('availability_unknown', 'The exchange is not present in the deployment credential catalogue.')
    : runtime.phase !== 'ready' ? denied('status_unavailable', 'Refresh bot status before changing this connection.')
    : !runtimeFresh ? denied('status_stale', 'Refresh fresh bot status before changing this connection.')
    : !configure.allowed ? configure
    : !stopAcknowledged ? denied('stop_not_acknowledged', 'Wait for the bot to acknowledge Stop before saving a connection.')
    : { allowed: true, reasonCode: null, detail: configure.detail }
  const disconnectDecision = !managed ? denied('managed_source_required', 'Credentials are managed by deployment.')
    : status.phase !== 'ready' || runtime.phase !== 'ready' ? denied('credential_status_unavailable', 'Refresh credential and bot status before disconnecting.')
    : !runtimeFresh ? denied('status_stale', 'Refresh fresh bot status before disconnecting.')
    : !disconnect.allowed ? disconnect
    : { allowed: true, reasonCode: null, detail: disconnect.detail }
  const cleanup = slot?.cleanup_operation_id ? { id: slot.cleanup_operation_id, version: slot.operation_version } : null
  const disconnected = Boolean(slot && !slot.configured && slot.status === 'disconnected')
  const connected = Boolean(slot?.configured && slot.status === 'connected' && slotEvidenceFresh &&
    status.phase === 'ready' && currentRuntime?.owner_available && observation?.observed_at && !observation.stale && observation.session_matches)
  return {
    exchange, connector, descriptor: descriptor ?? null, slot: slot ?? null,
    source: catalogue.data?.source ?? status.data?.source ?? null,
    fields: descriptor?.fields ?? [],
    decisions: { set: setDecision, disconnect: disconnectDecision,
      retry: status.phase !== 'ready' ? denied('credential_status_unavailable', 'Refresh credential status before retrying cleanup.')
        : !runtimeFresh ? denied('status_stale', 'Refresh fresh bot status before retrying cleanup.') : retry,
      repair: status.phase !== 'ready' ? denied('credential_status_unavailable', 'Refresh credential status before repairing cleanup.')
        : !runtimeFresh ? denied('status_stale', 'Refresh fresh bot status before repairing cleanup.') : repair },
    stopAcknowledged,
    connectionState: catalogue.phase === 'error' || status.phase === 'error' ? 'unavailable' as const
      : !slot ? 'unknown' as const
      : slot.cleanup_operation_id ? (slot.actions.retry_cleanup?.allowed ? 'action_required' as const : 'disconnecting' as const)
      : disconnected ? 'disconnected' as const
      : connected ? 'connected' as const
      : slot.configured ? 'checking' as const : 'not_configured' as const,
    cleanup: cleanup && { ...cleanup, rootReceiptId: cleanup.id, blockedStage: null as string | null, reasonCode: null as string | null, nextRetryAt: null as string | null,
      finalProofs: { ordersTerminalVerified: false, positionsFlatVerified: false, ledgerConfirmed: false, consumersDetached: false } },
    // A zero/missing row count or child receipt is never enough to conclude root cleanup.
    // Disconnection success also requires all four verified cleanup proofs from the root receipt.
    disconnectedProof: false,
  }
}

export function cleanupProofs(receipt: ResponseBody<'getCredentialOperation'> | undefined) {
  const result = receipt?.result
  return {
    ordersTerminalVerified: result?.orders_terminal_verified === true,
    positionsFlatVerified: result?.positions_flat_verified === true,
    ledgerConfirmed: result?.ledger_confirmed === true,
    consumersDetached: result?.consumers_detached === true,
  }
}

export function cleanupComplete(receipt: ResponseBody<'getCredentialOperation'> | undefined): boolean {
  return Boolean(receipt?.status === 'succeeded' && receipt.result.stage === 'removed' &&
    Object.values(cleanupProofs(receipt)).every(Boolean))
}

export type CredentialFormValues = Record<string, string>

/** Validate complete input by server-declared field names; caller must clear its secret state after submit. */
export function validateCredentialBundle(fields: Catalogue['exchanges'][number]['fields'], values: CredentialFormValues): string[] {
  return fields.filter((field) => field.required && !values[field.name]?.trim()).map((field) => field.name)
}
