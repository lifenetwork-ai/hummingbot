import type { ReadResponse } from '../../core/api/admin'
import type { components } from '../../core/api/schema'

type Catalogue = components['schemas']['MMProfilesConfigOutput']
export type ConfigurationSnapshot = {
  source: 'current_catalogue' | 'pinned_session'
  profileContext?: 'current' | 'at_creation' | null
  catalogueHash: string | null
  configHash: string | null
  sessionId: string | null
  activeProfileId: string | null
  request: components['schemas']['MMSessionRequestOutput'] | null
  profiles: { id: string; value: components['schemas']['MMProfileOutput'] }[]
  markets: { market: string; value: components['schemas']['LifeMMConfigOutput'] }[]
  random: Catalogue['random']
  riskLimits: Pick<Catalogue, 'max_controller_drawdown_quote' | 'max_global_drawdown_quote' | 'max_total_amount_quote' | 'spot_outflow'>
}
function snapshot(catalogue: Catalogue, metadata: Pick<ConfigurationSnapshot, 'source' | 'catalogueHash' | 'configHash' | 'sessionId' | 'activeProfileId' | 'request'> & { profileContext?: ConfigurationSnapshot['profileContext'] }): ConfigurationSnapshot {
  return { ...metadata, profileContext: metadata.profileContext ?? null, profiles: Object.entries(catalogue.profiles).map(([id, value]) => ({ id, value })),
    markets: Object.entries(catalogue.markets).map(([market, value]) => ({ market, value })), random: catalogue.random,
    riskLimits: { max_controller_drawdown_quote: catalogue.max_controller_drawdown_quote,
      max_global_drawdown_quote: catalogue.max_global_drawdown_quote,
      max_total_amount_quote: catalogue.max_total_amount_quote, spot_outflow: catalogue.spot_outflow } }
}
/** Two independently sourced read-only snapshots, never merged into an editable request body. */
export function buildConfigurationReview(current: ReadResponse<'listProfiles'> | undefined, pinned: ReadResponse<'getSessionConfiguration'> | undefined) {
  return {
    current: current ? snapshot(current.catalogue, { source: 'current_catalogue', profileContext: 'current', catalogueHash: current.catalogue_hash,
      configHash: null, sessionId: null, activeProfileId: null, request: null }) : null,
    pinned: pinned ? snapshot(pinned.catalogue, { source: 'pinned_session', profileContext: pinned.profile_context, catalogueHash: pinned.catalogue_hash,
      configHash: pinned.config_hash, sessionId: pinned.session_id, activeProfileId: pinned.profile_id, request: pinned.request }) : null,
  }
}
export type ConfigurationReviewModel = ReturnType<typeof buildConfigurationReview>
