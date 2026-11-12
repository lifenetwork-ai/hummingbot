import { describe, expect, it } from 'vitest'
import { recoveryComplete, recoveryEntry, recoveryOperationPending, reviewRecovery } from './recovery'
import type { RecoveryOperation, RecoveryOverview } from './recovery'
import type { ReadResponse } from '../../core/api/admin'

const overview = (overrides: Partial<RecoveryOverview> = {}) => ({
  halted: true, check_available: true, reset_available: true, control_version: 4, blockers: [], operation: null,
  ...overrides,
}) as RecoveryOverview
const status = (overrides: Partial<ReadResponse<'getRuntimeStatus'>> = {}): ReadResponse<'getRuntimeStatus'> => ({
  desired_state: 'stopped', observed_session: null, control_version: 4, acknowledged_version: 4,
  ...overrides,
}) as ReadResponse<'getRuntimeStatus'>
const check = (overrides: Partial<RecoveryOperation> = {}) => ({
  action: 'recovery_check', id: 'check-1', status: 'succeeded', stage: 'review_required', can_recover: true,
  confirmation_token: 'private-token', expires_at: new Date(10_000).toISOString(), trading_state: 'stopped',
  created_at: '', updated_at: '', detail: '', label: '',
  ...overrides,
}) as RecoveryOperation

describe('recovery decisions', () => {
  it('requires admin, HALT, fresh stopped acknowledgement and backend check availability', () => {
    const input = { permission: true, overview: overview(), status: status(), statusFresh: true, operationPending: false }
    expect(recoveryEntry(input)).toEqual({ allowed: true })
    expect(recoveryEntry({ ...input, permission: false }).reason).toBe('permission_required')
    expect(recoveryEntry({ ...input, overview: overview({ halted: false }) }).reason).toBe('recovery_not_halted')
    expect(recoveryEntry({ ...input, status: status({ desired_state: 'running' }) }).reason).toBe('stop_acknowledgement_required')
    expect(recoveryEntry({ ...input, status: status({ acknowledged_version: 3 }) }).reason).toBe('stop_acknowledgement_required')
    expect(recoveryEntry({ ...input, statusFresh: false }).reason).toBe('status_unavailable')
    expect(recoveryEntry({ ...input, overview: overview({ check_available: false }) }).reason).toBe('check_unavailable')
  })

  it('blocks check during pending recovery operations', () => {
    const input = { permission: true, overview: overview(), status: status(), statusFresh: true, operationPending: true }
    expect(recoveryEntry(input).reason).toBe('recovery_in_progress')
    expect(recoveryOperationPending(check({ status: 'accepted' }))).toBe(true)
    expect(recoveryOperationPending(check({ status: 'applying' }))).toBe(true)
    expect(recoveryOperationPending(check({ status: 'failed' }))).toBe(false)
  })

  it('allows review only for a succeeded, recoverable, unexpired exact check while stopped', () => {
    const input = { permission: true, overview: overview(), status: status(), statusFresh: true,
      operation: check(), now: 1_000 }
    expect(reviewRecovery(input)).toEqual({ allowed: true })
    expect(reviewRecovery({ ...input, operation: check({ stage: 'blocked', can_recover: false }) }).allowed).toBe(false)
    expect(reviewRecovery({ ...input, operation: check({ status: 'accepted' }) }).allowed).toBe(false)
    expect(reviewRecovery({ ...input, operation: check({ expires_at: new Date(500).toISOString() }) }).reason).toBe('preview_expired')
    expect(reviewRecovery({ ...input, operation: check({ expires_at: 'invalid-date' }) }).reason).toBe('preview_expired')
    expect(reviewRecovery({ ...input, status: status({ desired_state: 'running' }) }).reason).toBe('stop_acknowledgement_required')
    expect(reviewRecovery({ ...input, status: status({ acknowledged_version: 3 }) }).reason).toBe('stop_acknowledgement_required')
    expect(reviewRecovery({ ...input, overview: overview({ reset_available: false }) }).allowed).toBe(false)
    expect(reviewRecovery({ ...input, permission: false }).reason).toBe('permission_required')
  })

  it('reports completion only for succeeded recovered reset, never check success', () => {
    expect(recoveryComplete(check())).toBe(false)
    expect(recoveryComplete(check({ action: 'recovery_reset', stage: 'recovered' }))).toBe(true)
    expect(recoveryComplete(check({ action: 'recovery_reset', status: 'failed', stage: 'recovered' }))).toBe(false)
    expect(recoveryComplete(check({ action: 'recovery_reset', status: 'succeeded', stage: 'blocked' }))).toBe(false)
  })
})
