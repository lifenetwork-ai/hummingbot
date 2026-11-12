/**
 * Presentation policy for the own-spread tone on liquidity cards.
 * Tunable apart from rendering so thresholds stay reviewable and testable.
 */
export const SPREAD_TONE_THRESHOLDS_BPS = { goodBelow: 10, warnBelow: 30 } as const

export type SpreadTone = 'success' | 'warning' | 'danger' | 'neutral'

/** null (no observation) stays neutral; strict bounds keep boundary values in the stricter bucket. */
export function classifySpreadTone(spreadBpsNum: number | null): SpreadTone {
  if (spreadBpsNum === null) return 'neutral'
  if (spreadBpsNum < SPREAD_TONE_THRESHOLDS_BPS.goodBelow) return 'success'
  if (spreadBpsNum < SPREAD_TONE_THRESHOLDS_BPS.warnBelow) return 'warning'
  return 'danger'
}
