import { describe, expect, it } from 'vitest'
import { deriveRecoveryViewModel } from './view-model'
import type { RecoveryOperation } from './recovery'

const allowed = { allowed: true }
const check = (expiresAt: string | null): RecoveryOperation => ({
  id: 'check-1', action: 'recovery_check', status: 'succeeded', stage: 'review_required', can_recover: true,
  confirmation_token: null, expires_at: expiresAt, trading_state: 'stopped', created_at: '', updated_at: '', detail: '',
  label: 'Recovery check', proposed_changes: [], verification: { scope: 'verified_checkpoint', orders_checked: 0, fills_checked: 0 }, blockers: [],
}) as unknown as RecoveryOperation

function model(expiresAt: string | null, now: number) {
  return deriveRecoveryViewModel({ permission: true, operation: check(expiresAt), checkDecision: allowed, reviewDecision: allowed,
    canRetryCheck: false, canRetryReset: false, isChecking: false, isResetting: false, fresh: true, now })
}

describe('Recovery view model preview expiry', () => {
  it('derives a positive remaining time for a valid preview', () => {
    expect(model(new Date(5_000).toISOString(), 1_001)).toMatchObject({ previewValid: true, previewSecondsRemaining: 4 })
  })

  it('marks expired, missing, and invalid expiries unusable without a NaN countdown', () => {
    for (const expiry of [new Date(1_000).toISOString(), null, 'invalid']) {
      expect(model(expiry, 1_000)).toMatchObject({ previewValid: false, previewSecondsRemaining: 0 })
    }
  })
})
