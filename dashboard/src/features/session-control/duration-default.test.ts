import { describe, expect, it } from 'vitest'
import { pageProfiles } from '../../test/operationalTest'
import { defaultSessionDurationMode } from './session-control'

describe('catalogue duration default', () => {
  it('uses the advertised deployment default rather than array ordering', () => {
    expect(defaultSessionDurationMode(pageProfiles)).toBe('until_stopped')
  })
  it('falls back to timed for absent or unavailable defaults', () => {
    expect(defaultSessionDurationMode(undefined)).toBe('timed')
    expect(defaultSessionDurationMode({ ...pageProfiles, duration_modes: ['timed'] })).toBe('timed')
    expect(defaultSessionDurationMode({ ...pageProfiles, duration_modes: undefined })).toBe('timed')
    expect(defaultSessionDurationMode({ ...pageProfiles, catalogue: { ...pageProfiles.catalogue, allow_until_stopped: false } })).toBe('timed')
    expect(defaultSessionDurationMode({ ...pageProfiles, catalogue: { ...pageProfiles.catalogue, default_duration_mode: undefined } })).toBe('timed')
  })
})
