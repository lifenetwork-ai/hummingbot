import type { ReadResponse } from '../../core/api/admin'
import type { Source } from './model'

export function derivePerformancePanel(source: Source<ReadResponse<'getPerformance'>>) {
  const data = source.data
  return { source, percentageUnit: 'percentage_points' as const, percentageDenominator: 'volume_traded' as const, scope: data?.scope ?? null, observedAt: data?.server_time ?? null,
    ownerAvailable: data?.owner_available ?? null,
    rows: (data?.markets ?? []).flatMap((market) => market.observations.map((observation) => ({
      controllerId: observation.controller_id, market: observation.market, pair: observation.pair,
      connector: observation.connector, observedAt: observation.observed_at, source: observation.source,
      valuationBasis: observation.valuation_basis,
      observation: source.phase === 'error' || observation.stale ? 'stale' as const
        : source.phase !== 'ready' || !data?.owner_available || market.coverage !== 'observed' ? 'unknown' as const : 'fresh' as const,
      report: observation.report, fundingStatus: market.funding_status,
    }))),
    explanation: 'Controller lifetime and active exposure, not total account return or investment benchmark. Do not sum across controller/position reports.',
  }
}
export type PerformancePanelModel = ReturnType<typeof derivePerformancePanel>
