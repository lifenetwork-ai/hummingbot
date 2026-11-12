import type { OverviewModel } from './model'

type Markets = OverviewModel['positionMarkets']
type Position = Markets[number]['positions'][number]

function finiteNumber(value: string): number | null {
  if (!value.trim()) return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

export type PositionSummary = {
  observed: boolean
  positions: Position[]
  sameInstrument: boolean
  longAmount: number | null
  shortAmount: number | null
  gross: number | null
  netExposure: number | null
  netPnl: number | null
  pair: string | null
}

/** Backend amount is signed, while side is authoritative. Normalize magnitude before side-based aggregation. */
export function summarizePositions(markets: Markets): PositionSummary {
  const applicable = markets.filter((market) => market.coverage !== 'not_applicable')
  const positions = applicable.flatMap((market) => market.positions)
  const validObservation = applicable.length > 0 && applicable.every((market) =>
    market.coverage === 'observed' && market.observation === 'fresh' && !market.stale &&
    market.positions.every((position) => position.observation === 'fresh' && !position.stale &&
      (position.side === 'LONG' || position.side === 'SHORT') &&
      finiteNumber(position.amount) !== null && finiteNumber(position.unrealized_pnl) !== null),
  )
  const pairs = new Set(positions.map((position) => position.pair))
  const sameInstrument = pairs.size <= 1
  const pair = pairs.size === 1 ? positions[0].pair : null
  if (!validObservation) {
    return { observed: false, positions, sameInstrument, longAmount: null, shortAmount: null, gross: null, netExposure: null, netPnl: null, pair }
  }

  const longAmount = positions.filter((position) => position.side === 'LONG')
    .reduce((sum, position) => sum + Math.abs(finiteNumber(position.amount)!), 0)
  const shortAmount = positions.filter((position) => position.side === 'SHORT')
    .reduce((sum, position) => sum + Math.abs(finiteNumber(position.amount)!), 0)
  const pnlUsesUsdt = positions.every((position) => position.pair.endsWith('-USDT'))
  const pnlTotal = positions.reduce((sum, position) => sum + finiteNumber(position.unrealized_pnl)!, 0)
  if (![longAmount, shortAmount, longAmount + shortAmount, longAmount - shortAmount].every(Number.isFinite)) {
    return { observed: false, positions, sameInstrument, longAmount: null, shortAmount: null, gross: null, netExposure: null, netPnl: null, pair }
  }
  const netPnl = pnlUsesUsdt && Number.isFinite(pnlTotal) ? pnlTotal : null
  return {
    observed: true, positions, sameInstrument, longAmount, shortAmount,
    gross: longAmount + shortAmount,
    netExposure: sameInstrument ? longAmount - shortAmount : null,
    netPnl, pair,
  }
}
