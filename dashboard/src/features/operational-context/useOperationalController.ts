import { useAuth } from '../../core/auth/auth-context'
import { useAdminQuery } from '../../core/api/hooks'
import { deriveOperationalContext } from './context'

/** Called once by OperationalProvider; server state stays in the session QueryClient. */
export function useOperationalController() {
  const { identity } = useAuth()
  const status = useAdminQuery('getRuntimeStatus', {})
  const balances = useAdminQuery('getBalances', {})
  const model = deriveOperationalContext({
    identity,
    status: { phase: status.isError ? 'error' : status.isPending ? 'loading' : 'ready',
      data: status.data, error: status.error, refreshing: status.isFetching },
    balances: { phase: balances.isError ? 'error' : balances.isPending ? 'loading' : 'ready',
      data: balances.data, error: balances.error, refreshing: balances.isFetching },
  })
  return { ...model, refreshStatus: status.refetch, refreshBalances: balances.refetch }
}
