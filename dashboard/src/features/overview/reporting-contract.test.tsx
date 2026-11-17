import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { components } from '../../core/api/schema'
import { completionOverview } from '../../test/mocks/completionFixtures'
import { CapitalRiskCard } from './CapitalRiskCard'
import { deriveCapitalRisk, deriveLiquidity } from './supplement'

const at = '2026-11-11T00:00:00Z'
const missing = { value: null, stale: true, observed_at: null, unavailable_reason: 'unavailable' }
function supplement(): components['schemas']['OverviewSupplementOutput'] {
  return {
    contract_version: 1, server_time: at, session_id: 'completion-session', session_started_at: at,
    account_scope: { account_ids: [], environment: 'demo', site: null, identity_verified: true },
    portfolio: missing, balances: missing, references: missing, inventory_limits: missing, risk_rules: missing,
    session: { capital_allocated: missing, delta: missing, benchmark_hold: missing, fees: missing },
    markets: [{ market: 'spot', connector: 'okx', pair: 'LIFE-USDT', depth: missing, slippage_estimate: missing,
      post_fill_recovery: missing, coverage: { observed_at: at, stale: false, unavailable_reason: null, value: {
        conservative_eligible_fraction: 0.98, conditional_eligible_fraction: 1, rolling_30m_fraction: 0.5,
        measurement: 'sampled_interval_lower_bound', measurement_ready: true, measurement_reason: null,
        eligible_seconds: 900, eligibility_unknown_seconds: 20, restricted_seconds: 880, restriction_seconds: { inventory_limit: 880 },
        elapsed_seconds: 1800, observed_seconds: 1780, two_sided_seconds: 900, two_sided_eligible_seconds: 900,
        unknown_seconds: 20, target_fraction: 0.99, full_window: true, window_start: at, window_end: at,
        window_seconds: 1800, per_minute: null,
      } } }],
  }
}

describe('published reporting mapping', () => {
  it('keeps eligible and total-time denominators separate and preserves restrictions', () => {
    const [view] = deriveLiquidity(completionOverview, supplement())
    expect(view).toMatchObject({ coverageFraction: 0.98, totalTimeCoverageFraction: 0.5,
      measurementReady: true, eligibleSeconds: 900, restrictedSeconds: 880, eligibilityUnknownSeconds: 20,
      why: { elapsedSeconds: 1800, eligibleSeconds: 900, notEligibleSeconds: 880, unknownSeconds: 20 } })
  })

  it.each(['stale', 'reason', 'missing_time', 'missing_value'] as const)('rejects %s rolling evidence', (state) => {
    const data = supplement()
    const coverage = data.markets[0].coverage
    if (state === 'stale') coverage.stale = true
    if (state === 'reason') coverage.unavailable_reason = 'history_incomplete'
    if (state === 'missing_time') coverage.observed_at = null
    if (state === 'missing_value') coverage.value = null
    expect(deriveLiquidity(completionOverview, data)[0]).toMatchObject({
      rollingCoverageAvailable: false, coverageFraction: null, measurementReady: false,
    })
  })

  it.each([false, true])('labels retained fee totals with independent catch-up and history state (stale=%s)', (stale) => {
    const data = supplement()
    data.session.fees = { observed_at: at, stale, unavailable_reason: stale ? 'session_fees_refreshing' : null, value: {
      total_usdt: '0.000000001', by_market: null, conversion_basis: 'at_fill', history_complete: false,
      processing_state: 'catching_up', through_at: at, caught_up_at: null, processed_fill_count: 1,
      backfill_processed_fill_count: 0, processing_projection_version: 2, reported_projection_version: 1,
    } }
    render(<CapitalRiskCard view={deriveCapitalRisk(completionOverview, data)} mode="operator" />)
    expect(screen.getByText('0.000000001 USDT')).toBeInTheDocument()
    const label = screen.getByText(/Session fees/)
    expect(label).toHaveTextContent('catching up')
    expect(label).toHaveTextContent('history incomplete')
    if (stale) expect(label).toHaveTextContent('stale')
  })
})
