import { describe, expect, it } from 'vitest'
import type { MMProfile } from './profile-reference-math'
import {
  profileReferences,
  referenceContributions,
  targetPrice,
  weightedReferenceMove,
} from './profile-reference-math'

const followMarket: MMProfile = {
  anchor: 'session_start_mid',
  scenario: 'follow_market',
  sideways_range: null,
  references: [
    { pair: 'BTC-USDT', weight: '0.6' },
    { pair: 'ETH-USDT', weight: '0.4' },
  ],
}

const sideways: MMProfile = { anchor: 'session_start_mid', scenario: 'sideways', sideways_range: null }

describe('weightedReferenceMove', () => {
  it('applies basket weights to per-pair moves (life_mm.py formula)', () => {
    expect(weightedReferenceMove(followMarket, { 'BTC-USDT': 0.1, 'ETH-USDT': -0.05 })).toBeCloseTo(0.6 * 0.1 + 0.4 * -0.05)
  })

  it('treats a pair with no slider input as unchanged', () => {
    expect(weightedReferenceMove(followMarket, { 'BTC-USDT': 0.1 })).toBeCloseTo(0.06)
  })

  it('sideways has no references so the move is always zero', () => {
    expect(profileReferences(sideways)).toEqual([])
    expect(weightedReferenceMove(sideways, { 'BTC-USDT': 0.3 })).toBe(0)
  })
})

describe('referenceContributions', () => {
  it('splits the weighted move per pair', () => {
    const rows = referenceContributions(followMarket, { 'BTC-USDT': 0.1, 'ETH-USDT': -0.05 })
    expect(rows.map((r) => r.pair)).toEqual(['BTC-USDT', 'ETH-USDT'])
    expect(rows[0].contribution).toBeCloseTo(0.06)
    expect(rows[1].contribution).toBeCloseTo(-0.02)
  })
})

describe('targetPrice', () => {
  it('multiplies the anchor by the weighted move', () => {
    expect(targetPrice(0.012, 0.04)).toBeCloseTo(0.012 * 1.04)
  })

  it('stays null without a usable anchor', () => {
    expect(targetPrice(null, 0.04)).toBeNull()
    expect(targetPrice(0, 0.04)).toBeNull()
  })
})
