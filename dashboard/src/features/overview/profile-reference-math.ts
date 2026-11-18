import type { components } from '../../core/api/schema'

export type MMProfile = components['schemas']['MMProfileOutput']
export type ReferencePair = components['schemas']['ReferencePairOutput']

/** Percentage change of one reference pair since the session anchor, e.g. 0.021 = +2.1%. */
export type ReferenceDelta = number

export const SCENARIO_COPY: Record<MMProfile['scenario'], { title: string; body: string }> = {
  follow_market: {
    title: 'Tracks a weighted market basket',
    body: 'The LIFE target mid scales with the weighted percentage move of the reference basket since the baseline anchor. If the basket moves +2% on average, LIFE\'s pricing centre shifts +2%.',
  },
  sideways: {
    title: 'Holds the session anchor',
    body: 'No reference pairs: the weighted sum is always zero, so the target mid stays pinned to the baseline anchor for this profile step. Quotes can additionally be clamped to a manual sideways range.',
  },
  follow_token: {
    title: 'Mirrors exactly one token',
    body: 'With a single reference at weight 1, the LIFE target mid copies that token\'s percentage move since the baseline anchor, mirroring its price action.',
  },
}

export function profileReferences(profile: MMProfile): ReferencePair[] {
  return profile.references ?? []
}

/**
 * Weighted reference move since the session anchor — the sum inside
 * target = anchor × (1 + Σ wᵢ × (Pᵢ/Pᵢ₀ − 1)); see controllers/market_making/life_mm.py.
 * Sideways profiles carry no references, so the move is always 0.
 */
export function weightedReferenceMove(profile: MMProfile, deltas: Partial<Record<string, ReferenceDelta>>): number {
  return profileReferences(profile).reduce(
    (sum, ref) => sum + Number(ref.weight) * (deltas[ref.pair] ?? 0),
    0,
  )
}

export function targetPrice(anchor: number | null, weightedMove: number): number | null {
  if (anchor === null || !Number.isFinite(anchor) || anchor <= 0) return null
  return anchor * (1 + weightedMove)
}

/** Per-pair contribution wᵢ × Δᵢ, for the diverging contribution bar. */
export function referenceContributions(
  profile: MMProfile,
  deltas: Partial<Record<string, ReferenceDelta>>,
): Array<{ pair: string; weight: number; delta: number; contribution: number }> {
  return profileReferences(profile).map((ref) => {
    const weight = Number(ref.weight)
    const delta = deltas[ref.pair] ?? 0
    return { pair: ref.pair, weight, delta, contribution: weight * delta }
  })
}
