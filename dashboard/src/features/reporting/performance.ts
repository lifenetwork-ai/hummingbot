import type { ReadResponse } from '../../core/api/admin'
import type { Source } from './model'

/**
 * Backend serializes CloseType enum dict keys as numeric values ("10" for
 * POSITION_HOLD, per strategy_v2.models.executors.CloseType). Normalize them to
 * canonical names so display code never matches on numeric keys.
 */
const CLOSE_TYPE_BY_VALUE: Record<string, string> = {
  '1': 'TIME_LIMIT',
  '2': 'STOP_LOSS',
  '3': 'TAKE_PROFIT',
  '4': 'EXPIRED',
  '5': 'EARLY_STOP',
  '6': 'TRAILING_STOP',
  '7': 'INSUFFICIENT_BALANCE',
  '8': 'FAILED',
  '9': 'COMPLETED',
  '10': 'POSITION_HOLD',
}

function normalizeCloseTypeCounts(counts: Record<string, number> | undefined): Record<string, number> {
  const output: Record<string, number> = {}
  for (const [key, count] of Object.entries(counts ?? {})) {
    const name = CLOSE_TYPE_BY_VALUE[key] ?? key
    output[name] = (output[name] ?? 0) + count
  }
  return output
}

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
      report: { ...observation.report, close_type_counts: normalizeCloseTypeCounts(observation.report.close_type_counts) }, fundingStatus: market.funding_status,
    }))),
    explanation: 'Controller lifetime and active exposure, not total account return or investment benchmark. Do not sum across controller/position reports.',
  }
}
export type PerformancePanelModel = ReturnType<typeof derivePerformancePanel>
