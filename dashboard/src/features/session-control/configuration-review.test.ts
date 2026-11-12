import { describe, expect, it } from 'vitest'
import { pageProfiles } from '../../test/operationalTest'
import { buildConfigurationReview } from './configuration-review'
import type { ReadResponse } from '../../core/api/admin'
const pinned: ReadResponse<'getSessionConfiguration'> = { catalogue: { ...pageProfiles.catalogue, max_total_amount_quote: '9007199254740993.000001' },
  catalogue_hash: 'pinned-catalogue', config_hash: 'pinned-config', session_id: 'previous-session', profile_id: 'follow_market',
  source: 'session_created_event', profile_context: 'at_creation', legacy_overrides: [],
  request: { market_mode: 'spot', selection: 'fixed', profile_id: 'follow_market', profile_ids: null, duration_mode: 'timed', duration_seconds: 600, references: null, step_duration_seconds: null } }
describe('read-only configuration review', () => {
  it('retains exact values and independently pinned/current hashes without rewriting a mutation', () => {
    const model = buildConfigurationReview(pageProfiles, pinned)
    expect(model.current?.catalogueHash).toBe('catalogue-reviewed')
    expect(model.pinned?.catalogueHash).toBe('pinned-catalogue')
    expect(model.pinned?.riskLimits.max_total_amount_quote).toBe('9007199254740993.000001')
    expect(model.pinned?.sessionId).toBe('previous-session')
    expect(model.current?.request).toBeNull()
    expect(model.pinned?.request?.duration_seconds).toBe(600)
    expect(model.current?.profiles[0].value).toEqual(pageProfiles.catalogue.profiles.follow_market)
    expect(pageProfiles.catalogue.max_total_amount_quote).toBe('1000')
  })
  it('keeps absent pinned/current sources absent rather than silently falling back', () => {
    expect(buildConfigurationReview(undefined, pinned).current).toBeNull()
    expect(buildConfigurationReview(pageProfiles, undefined).pinned).toBeNull()
  })
})
