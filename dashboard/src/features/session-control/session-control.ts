import type { ReadResponse } from '../../core/api/admin'
import type { components } from '../../core/api/schema'

export type SessionDraft = {
  marketMode: 'spot' | 'perpetual' | 'both'
  selection: 'fixed' | 'random'
  profileId: string | null
  durationMode: 'timed' | 'until_stopped'
  durationSeconds: number | null
}
export type SessionReview = Readonly<{
  action: 'start' | 'switch'
  body: components['schemas']['StartCommand'] | components['schemas']['SwitchCommand']
  reviewedAtVersion: number
  catalogueHash: string | null
}>
export type ReviewResult = { review?: SessionReview; errors: Record<string, string> }

export function reviewSession(input: {
  action: 'start' | 'switch'
  draft: SessionDraft
  status: ReadResponse<'getRuntimeStatus'> | undefined
  profiles: ReadResponse<'listProfiles'> | undefined
  permission: boolean
  statusAvailable: boolean
}): ReviewResult {
  const { action, draft, status, profiles } = input
  const errors: Record<string, string> = {}
  if (!input.permission) errors.action = 'permission_required'
  if (!input.statusAvailable || !status) errors.status = 'status_unavailable'
  if (!profiles) errors.profiles = 'catalogue_unavailable'
  if (!status || !profiles) return { errors }
  const availability = status.actions?.[action]
  if (!availability) errors.action = 'availability_unknown'
  else if (!availability.allowed) errors.action = availability.reason_code ?? 'action_unavailable'
  if (!profiles.market_modes.includes(draft.marketMode)) errors.marketMode = 'market_mode_not_configured'
  if (draft.selection === 'fixed' && (!draft.profileId || !(draft.profileId in profiles.catalogue.profiles))) errors.profileId = 'profile_not_configured'
  if (draft.selection === 'random' && profiles.catalogue.random.profile_ids.length === 0) errors.selection = 'random_pool_empty'
  const supportedDurations = profiles.duration_modes ?? ['timed']
  if (!supportedDurations.includes(draft.durationMode)) errors.durationMode = 'duration_mode_not_configured'
  if (draft.durationMode === 'until_stopped' && !profiles.catalogue.allow_until_stopped) errors.durationMode = 'duration_mode_not_allowed'
  if (draft.durationMode === 'timed' && draft.durationSeconds !== null &&
      (!Number.isInteger(draft.durationSeconds) || draft.durationSeconds < profiles.catalogue.min_duration_seconds || draft.durationSeconds > profiles.catalogue.max_duration_seconds)) {
    errors.durationSeconds = 'duration_out_of_bounds'
  }
  if (action === 'switch' && !status.session?.id) errors.session = 'active_session_required'
  if (Object.keys(errors).length) return { errors }

  const common = {
    expected_version: status.control_version,
    expected_catalogue_hash: profiles.catalogue_hash,
    market_mode: draft.marketMode,
    selection: draft.selection,
    profile_id: draft.selection === 'fixed' ? draft.profileId : null,
    duration_mode: draft.durationMode,
    duration_seconds: draft.durationMode === 'timed' ? draft.durationSeconds : null,
  } as const
  const body: SessionReview['body'] = action === 'start'
    ? { ...common, action: 'start' }
    : { ...common, action: 'switch', session_id: status.session!.id }
  return { review: Object.freeze({ action, body: Object.freeze(body), reviewedAtVersion: status.control_version, catalogueHash: profiles.catalogue_hash }), errors }
}

export function makeStopCommand(status: ReadResponse<'getRuntimeStatus'> | undefined, permission: boolean, statusAvailable = true) {
  if (!permission) return { error: 'permission_required' as const }
  if (!statusAvailable || !status) return { error: 'status_unavailable' as const }
  const availability = status.actions?.stop
  if (!availability) return { error: 'availability_unknown' as const }
  if (!availability.allowed) return { error: availability.reason_code ?? 'action_unavailable' }
  if (!status.session?.id) return { error: 'active_session_unknown' as const }
  return { body: { action: 'stop' as const, expected_version: status.control_version, session_id: status.session.id } }
}

export function commandCompletion(status: string | undefined): 'pending' | 'succeeded' | 'failed' | 'superseded' | 'unknown' {
  if (status === 'accepted' || status === 'applying') return 'pending'
  if (status === 'succeeded' || status === 'failed' || status === 'superseded') return status
  return 'unknown'
}
