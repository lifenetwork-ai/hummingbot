import { useAdminQuery } from '../../core/api/hooks'
import type { useOperationalController } from '../operational-context/useOperationalController'
import { deriveOverview } from './model'
import { deriveOperationalDiagnostics, deriveProcessDiagnostics } from './diagnostics'
import { deriveRecoveryPresentation } from '../recovery/presentation'
import { useObservationTime } from '../../shared/utils/useObservationTime'

type SharedOperationalSources = Pick<ReturnType<typeof useOperationalController>, 'status' | 'balances'>

/** Reuses the protected workspace's status/balance queries; only feature-specific sources are queried here. */
export function useOverviewController(shared: SharedOperationalSources) {
  const now = useObservationTime(Boolean(shared.status.data?.automatic_recovery || shared.status.data?.markets?.some((market) => market.inventory_reduction) || shared.status.data?.spot_outflow || shared.status.data?.service))
  const liquidity = useAdminQuery('getLiquidity', { query: { market: 'all' } })
  const positions = useAdminQuery('getPositions', {})
  const model = deriveOverview({
    status: shared.status,
    balances: shared.balances,
    liquidity: { phase: liquidity.isError ? 'error' : liquidity.isPending ? 'loading' : 'ready', data: liquidity.data, error: liquidity.error, refreshing: liquidity.isFetching },
    positions: { phase: positions.isError ? 'error' : positions.isPending ? 'loading' : 'ready', data: positions.data, error: positions.error, refreshing: positions.isFetching },
  })
  const fresh = model.observation.status === 'fresh'
  return { ...model,
    diagnostics: deriveOperationalDiagnostics(shared.status.data, fresh, now),
    processDiagnostics: deriveProcessDiagnostics(shared.status.error),
    recoveryPresentation: deriveRecoveryPresentation(shared.status.data, fresh, now),
    refreshLiquidity: liquidity.refetch, refreshPositions: positions.refetch }
}
