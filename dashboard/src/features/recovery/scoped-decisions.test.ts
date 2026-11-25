import { describe, expect, it } from 'vitest'
import type { ReadResponse } from '../../core/api/admin'
import { recoveryEntry, reviewRecovery, type RecoveryOperation, type RecoveryOverview } from './recovery'

const overview = { halted: false, check_available: true, reset_available: true } as RecoveryOverview
const status = { desired_state: 'stopped', control_version: 4, acknowledged_version: 4,
  observed_session: null } as ReadResponse<'getRuntimeStatus'>
const operation = { action: 'recovery_check', status: 'succeeded', stage: 'review_required', can_recover: true,
  confirmation_token: 'private', expires_at: new Date(10_000).toISOString(), trading_state: 'stopped' } as RecoveryOperation
const input = { permission: true, overview, status, statusFresh: true, uncertainOrderScope: true }

describe('scoped recovery decisions', () => {
  it('allows stopped non-HALT review without weakening generic HALT recovery', () => {
    expect(recoveryEntry({ ...input, operationPending: false })).toEqual({ allowed: true })
    expect(reviewRecovery({ ...input, operation, now: 1_000 })).toEqual({ allowed: true })
    expect(recoveryEntry({ ...input, uncertainOrderScope: false, operationPending: false }).allowed).toBe(false)
  })

  it('allows Check and Reset when the backend authorizes the selected order during HALT', () => {
    const halted = { ...input, overview: { ...overview, halted: true } }
    expect(recoveryEntry({ ...halted, operationPending: false })).toEqual({ allowed: true })
    expect(reviewRecovery({ ...halted, operation, now: 1_000 })).toEqual({ allowed: true })
  })

  it.each([
    { permission: false },
    { statusFresh: false },
    { status: { ...status, acknowledged_version: 3 } },
    { status: { ...status, desired_state: 'running' as const } },
  ])('retains common safety fences for %j', (override) => {
    expect(recoveryEntry({ ...input, ...override, operationPending: false }).allowed).toBe(false)
    expect(reviewRecovery({ ...input, ...override, operation, now: 1_000 }).allowed).toBe(false)
  })

  it('requires independent backend availability for Check and Reset', () => {
    expect(recoveryEntry({ ...input, overview: { ...overview, halted: true, check_available: false }, operationPending: false }).allowed).toBe(false)
    expect(reviewRecovery({ ...input, overview: { ...overview, halted: true, reset_available: false }, operation, now: 1_000 }).allowed).toBe(false)
    expect(reviewRecovery({ ...input, operation, now: 10_000 }).reason).toBe('preview_expired')
    expect(reviewRecovery({ ...input, operation: { ...operation, confirmation_token: null }, now: 1_000 }).allowed).toBe(false)
  })
})
