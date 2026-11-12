import { describe, expect, it } from 'vitest'
import type { ReadResponse } from '../../core/api/admin'
import { makeStopCommand, reviewSession } from './session-control'

const status = {
  control_version: 8,
  session: { id: 'session-1' },
  actions: {
    start: { allowed: true, detail: 'Ready', reason_code: null },
    switch: { allowed: true, detail: 'Ready', reason_code: null },
    stop: { allowed: true, detail: 'Ready', reason_code: null },
  },
} as unknown as ReadResponse<'getRuntimeStatus'>
const profiles = {
  market_modes: ['spot', 'both'], duration_modes: ['timed', 'until_stopped'], catalogue_hash: 'hash-1',
  catalogue: {
    allow_until_stopped: true, min_duration_seconds: 60, max_duration_seconds: 3600,
    profiles: { follow_market: {} }, random: { profile_ids: ['follow_market'] },
  },
} as unknown as ReadResponse<'listProfiles'>
const draft = { marketMode: 'spot' as const, selection: 'fixed' as const, profileId: 'follow_market', durationMode: 'until_stopped' as const, durationSeconds: 180 }

describe('session command review', () => {
  it('reviews a pinned catalogue and does not leak stale timed seconds into until-stopped', () => {
    const { review, errors } = reviewSession({ action: 'start', draft, status, profiles, permission: true, statusAvailable: true })
    expect(errors).toEqual({})
    expect(review?.body).toMatchObject({ action: 'start', expected_version: 8, expected_catalogue_hash: 'hash-1', duration_mode: 'until_stopped', duration_seconds: null })
    expect(Object.isFrozen(review?.body)).toBe(true)
  })
  it('rejects catalogue mismatch inputs and action availability before POST', () => {
    expect(reviewSession({ action: 'start', draft: { ...draft, marketMode: 'perpetual' }, status, profiles, permission: true, statusAvailable: true }).errors.marketMode).toBe('market_mode_not_configured')
    const blocked = { ...status, actions: { ...status.actions, start: { allowed: false, detail: 'HALT', reason_code: 'halt_active' } } } as ReadResponse<'getRuntimeStatus'>
    expect(reviewSession({ action: 'start', draft, status: blocked, profiles, permission: true, statusAvailable: true }).errors.action).toBe('halt_active')
  })
  it('switch requires the current session and uses it in the reviewed payload', () => {
    expect(reviewSession({ action: 'switch', draft, status, profiles, permission: true, statusAvailable: true }).review?.body).toMatchObject({ action: 'switch', session_id: 'session-1' })
    expect(reviewSession({ action: 'switch', draft, status: { ...status, session: null }, profiles, permission: true, statusAvailable: true }).errors.session).toBe('active_session_required')
  })
  it('keeps Stop independent while requiring fresh status and backend availability', () => {
    expect(makeStopCommand(status, true, true).body).toMatchObject({ action: 'stop', session_id: 'session-1', expected_version: 8 })
    expect(makeStopCommand(status, true, false).error).toBe('status_unavailable')
    expect(makeStopCommand({ ...status, actions: {} } as ReadResponse<'getRuntimeStatus'>, true).error).toBe('availability_unknown')
    expect(makeStopCommand(status, false).error).toBe('permission_required')
  })
})
