import { useAdminQuery } from '../../core/api/hooks'
import { useOperationalContext } from './useOperationalContext'

/** Shared account snapshots remain separate; missing observations never imply zero or Flat. */
export function useAssetRibbonController() {
  const context = useOperationalContext()
  const positions = useAdminQuery('getPositions', {}, { enabled: context.identity?.permissions.read === true })
  const currencies = context.environmentMismatch || context.observationState === 'unknown' || context.observationState === 'session_mismatch' ? [] :
    [...new Set(context.markets.flatMap(market =>
      market.base_asset && market.quote_asset && market.pair === `${market.base_asset}-${market.quote_asset}`
        ? [market.quote_asset, market.base_asset] : []))]
  const accounts = context.accounts.map((account) => ({
    id: account.data.account_id,
    observation: account.observation === 'unknown' || context.observationState === 'unknown' || context.observationState === 'session_mismatch'
      ? 'unknown' as const : context.observationState === 'fresh' ? account.observation : 'stale' as const,
    balances: currencies.map(currency => {
      const balance = account.data.balances.find(row => row.currency === currency)
      return { currency, available: balance && balance.present_in_latest_snapshot !== false ? balance.available_cash : null }
    }),
  }))
  const markets = positions.data?.markets ?? []
  const observed = positions.isSuccess && positions.data.owner_available && markets.length > 0 &&
    markets.every((market) => market.coverage === 'observed' && !market.stale && Boolean(market.observed_at))
  const items = markets.flatMap((market) => market.items ?? [])
  const drawdown = context.status.data?.drawdown
  const outflow = context.status.data?.spot_outflow
  const policyCurrent = context.observationState === 'fresh'
  const known = policyCurrent && Boolean(drawdown?.ready && !drawdown.stale && outflow?.ready && !outflow.stale)
  const blocked = policyCurrent && Boolean(drawdown?.breached_controller_id || (outflow?.ready && !outflow.stale && !outflow.buy_allowed))
  return { accounts, assetContextAvailable: currencies.length > 0, positions: items, positionsObserved: observed, positionError: positions.error,
    riskState: blocked ? 'blocked' as const : known ? 'no_reported_blocker' as const : 'unknown' as const,
    profileId: context.status.data?.observed_session?.profile_id ?? context.status.data?.session?.profile_id,
    selection: context.status.data?.session?.request.selection }
}
