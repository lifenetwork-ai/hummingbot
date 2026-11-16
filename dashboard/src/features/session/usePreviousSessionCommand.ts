import { useAdminQuery } from '../../core/api/hooks'
import type { components } from '../../core/api/schema'

export type CommandReceipt = components['schemas']['CommandResponseOutput']
export type SessionConfigPayload = components['schemas']['RecordedStartCommandOutput'] | components['schemas']['RecordedSwitchCommandOutput']

/** Narrow a receipt payload to a start/switch configuration payload. */
export function configPayloadOf(receipt: CommandReceipt | null | undefined): SessionConfigPayload | null {
  const payload = receipt?.payload
  if (payload && (payload.action === 'start' || payload.action === 'switch')) return payload
  return null
}

/**
 * The most recent earlier start/switch command recorded for the same session —
 * the configuration a switch replaces. Null while loading or when no earlier
 * configuration receipt exists (first recorded command or unavailable history).
 */
export function usePreviousSessionCommand(receipt: CommandReceipt | null | undefined) {
  const sessionId = receipt?.action === 'switch' ? receipt.session_id : undefined
  const query = useAdminQuery(
    'listCommands',
    sessionId ? { query: { session_id: sessionId, limit: 20 } } : undefined,
    { enabled: Boolean(sessionId) },
  )
  const submittedAt = receipt ? new Date(receipt.created_at).getTime() : Number.NaN
  const previous = receipt
    ? (query.data?.items ?? []).find(item => item.id !== receipt.id
        && (item.action === 'start' || item.action === 'switch')
        && new Date(item.created_at).getTime() <= submittedAt)
    : undefined
  return {
    previous: previous ?? null,
    isLoading: Boolean(sessionId) && query.isLoading,
  }
}
