import { useMemo, useSyncExternalStore } from 'react'
import { useApiSessionScope } from './ApiSessionContext'
import { readTrackedReceipts, subscribeTrackedReceipts, type ReceiptReference, type ReceiptScope } from './receipt-tracking'
import { useAdminQuery } from './hooks'

/** Read-only refs keep the last known bot scope within the session-owned cache. */
export function useTrackedReceipts(): ReceiptReference[] {
  const session = useApiSessionScope()
  const status = useAdminQuery('getRuntimeStatus', {}, { poll: false })
  // Invalidation/error does not change the identity of previously fetched data.
  // Keeping this read-only scope preserves receipt inspection during status refresh.
  // ApiProvider replaces the cache on API/principal/session changes; writes still
  // require trustedBotId() at submission time in useAdminMutation.
  const botId = status.data?.bot_id?.trim() ? status.data.bot_id : undefined
  const scope = useMemo<ReceiptScope | undefined>(() => session?.apiBaseUrl && session.principalId && botId
    ? { apiBaseUrl: session.apiBaseUrl, principalId: session.principalId, botId }
    : undefined, [session?.apiBaseUrl, session?.principalId, botId])
  const subscribe = useMemo(() => (onChange: () => void) => subscribeTrackedReceipts(scope, onChange), [scope])
  const snapshot = useMemo(() => () => JSON.stringify(readTrackedReceipts(scope)), [scope])
  const serialized = useSyncExternalStore(subscribe, snapshot, () => '[]')
  return useMemo(() => JSON.parse(serialized) as ReceiptReference[], [serialized])
}
