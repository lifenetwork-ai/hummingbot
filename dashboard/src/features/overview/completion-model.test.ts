import { describe, expect, it } from 'vitest'
import { completionOverview, completionStaleOverview, completionStatus } from '../../test/mocks/completionFixtures'
import { deriveOverview } from './model'
describe('completion overview contract', () => {
  it('keeps random period and policy inventory from source observations', () => {
    expect(completionOverview.task.randomCycle).toMatchObject({ stepIndex: 2, stepDurationSeconds: 120, remainingAtObservationSeconds: 120 })
    expect(completionOverview.task.runtime.ownerAvailable).toBe(true)
    expect(completionOverview.risk.marketPolicies[0].inventory?.base_cash).toBe('0.000000001')
    expect(completionOverview.markets[0].sides[0]).toMatchObject({ reasonCodes: ['inventory_restricted'] })
    expect(completionOverview.markets[0].warningThresholdStatus).toBe('deployment_threshold_differs')
  })
  it('does not fabricate a random clock or fresh policy when source evidence is missing/stale', () => {
    expect(completionStaleOverview.task.randomCycle?.observation).toBe('unknown')
    expect(completionStaleOverview.risk.marketPolicies[0].observation).toBe('unknown')
    const model = deriveOverview({ status: { ...completionOverview.status, data: { ...completionStatus, server_time: null, session: { ...completionStatus.session!, step_expires_at: null } } }, balances: completionOverview.balances, positions: completionOverview.positions, liquidity: completionOverview.liquidity })
    expect(model.task.randomCycle?.remainingAtObservationSeconds).toBeNull()
  })
})
