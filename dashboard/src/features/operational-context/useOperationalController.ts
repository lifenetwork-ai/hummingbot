import { useTrackedReceipts } from '../../core/api/useTrackedReceipts'
import { deriveRecoveryFence } from './recovery-fence'
import { useAuth } from '../../core/auth/auth-context'
import { useAdminQuery } from '../../core/api/hooks'
import { deriveOperationalContext } from './context'

/** Called once by OperationalProvider; server state stays in the session QueryClient. */
export function useOperationalController() {
  const { identity } = useAuth()
  const status = useAdminQuery('getRuntimeStatus', {})
  const balances = useAdminQuery('getBalances', {})
  const recoveryReference = useTrackedReceipts().find((reference) => reference.kind === 'recovery')
  const inspectRecovery = identity?.permissions.manage_recovery === true ? recoveryReference : undefined
  const recoveryOperation = useAdminQuery('getRecoveryOperation', inspectRecovery ? { path: { operation_id: inspectRecovery.id } } : undefined)
  const recoveryLock = deriveRecoveryFence(inspectRecovery, { phase: recoveryOperation.isError ? 'error' : recoveryOperation.isPending ? 'loading' : 'ready', data: recoveryOperation.data })
  const model = deriveOperationalContext({
    identity,
    status: { phase: status.isError ? 'error' : status.isPending ? 'loading' : 'ready',
      data: status.data, error: status.error, refreshing: status.isFetching },
    balances: { phase: balances.isError ? 'error' : balances.isPending ? 'loading' : 'ready',
      data: balances.data, error: balances.error, refreshing: balances.isFetching },
  })
  const blocked = { allowed: false, reasonCode: 'recovery_in_progress', detail: recoveryLock.detail }
  return { ...model, ...(inspectRecovery ? { recoveryLock, refreshRecoveryOperation: async () => { await recoveryOperation.refetch() } } : {}),
    statusUpdatedAt: status.dataUpdatedAt,
    actions: { ...model.actions, start: recoveryLock.active ? blocked : model.actions.start, switch: recoveryLock.active ? blocked : model.actions.switch },
    refreshStatus: status.refetch, refreshBalances: balances.refetch }
}
