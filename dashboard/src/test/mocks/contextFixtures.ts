import type { ResponseBody } from '../../core/api/types'

// Synthetic fixtures only; never import from application runtime.
export const contextPrincipal: ResponseBody<'getAdminIdentity'> = {
  id: 'google:operator', email: null, kind: 'google', role: 'write', role_label: 'Operator',
  permissions: { read: true, operate: true, manage_accounts: false, manage_recovery: false },
}
export const contextStatus: ResponseBody<'getRuntimeStatus'> = {
  automatic_recovery: null,
  acknowledged_version: 1, bot_id: 'bot-a', control_version: 2, desired_state: 'running',
  drawdown: null, error: null, funding_sync: null, heartbeat_age_seconds: 0,
  observation: { observed_at: '2026-11-10T00:00:00Z', reason_code: null, stale: false, session_matches: true },
  observed_session: null, operator: null, owner_available: true,
  owner_identity: { basis: 'process_configuration', revision: null, trading_environment: 'demo' },
  process_identity: { basis: 'process_configuration', revision: null, trading_environment: 'demo' },
  phase: 'running', recovery: null, running: true, server_time: '2026-11-10T00:00:00Z', service: null,
  session: null, session_configuration_url: null, spot_outflow: null, time_remaining_seconds: null,
  actions: {
    start: { allowed: false, reason_code: 'session_active', detail: 'Session is active.' },
    switch: { allowed: true, reason_code: null, detail: 'Replace session.' },
    stop: { allowed: true, reason_code: null, detail: 'Positions remain open.' },
  },
}
