import { useAdminQuery } from '../../core/api/hooks'
import type { useOperationalController } from '../operational-context/useOperationalController'
import { deriveOverview } from './model'

type SharedOperationalSources = Pick<ReturnType<typeof useOperationalController>, 'status' | 'balances'>

/** Reuses the protected workspace's status/balance queries; only feature-specific sources are queried here. */
export function useOverviewController(shared: SharedOperationalSources) {
  const liquidity = useAdminQuery('getLiquidity', { query: { market: 'all' } })
  const positions = useAdminQuery('getPositions', {})
  const model = deriveOverview({
    status: shared.status,
    balances: shared.balances,
    liquidity: { phase: liquidity.isError ? 'error' : liquidity.isPending ? 'loading' : 'ready', data: liquidity.data, error: liquidity.error, refreshing: liquidity.isFetching },
    positions: { phase: positions.isError ? 'error' : positions.isPending ? 'loading' : 'ready', data: positions.data, error: positions.error, refreshing: positions.isFetching },
  })
  return { ...model, refreshLiquidity: liquidity.refetch, refreshPositions: positions.refetch }
}
