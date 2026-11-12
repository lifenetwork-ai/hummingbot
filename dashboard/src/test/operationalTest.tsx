import { transferableAbortController } from 'node:util'
import type { PropsWithChildren } from 'react'
import { afterAll, beforeEach, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import { ApiProvider } from '../core/api/ApiProvider'
import { AuthContext, type AuthContextValue } from '../core/auth/auth-context'
import type { ReadResponse } from '../core/api/admin'
import { OperationalProvider } from '../features/operational-context/OperationalProvider'
import { contextPrincipal, contextStatus } from './mocks/contextFixtures'
import { staleBalances } from './mocks/adminFixtures'
import { server } from './mocks/server'

export const pageApiUrl = 'https://pages.test/api/v1/admin'
export const pageProfiles: ReadResponse<'listProfiles'> = {
  catalogue_hash: 'catalogue-reviewed', process_identity: null, reporting_timezone: 'Asia/Ho_Chi_Minh',
  market_modes: ['spot', 'perpetual', 'both'], duration_modes: ['timed', 'until_stopped'],
  catalogue: { default_duration_mode: 'until_stopped', allow_until_stopped: true, default_duration_seconds: 3600, min_duration_seconds: 60, max_duration_seconds: 86400,
    markets: {}, max_controller_drawdown_quote: null, max_global_drawdown_quote: null, max_total_amount_quote: '1000', reference_connector: 'okx', spot_outflow: null, profiles: { follow_market: { anchor: 'session_start_mid', scenario: 'follow_market', sideways_range: null, references: [] } }, random: { profile_ids: ['follow_market'], step_duration_seconds: 120 },
  },
}
export function setupPageApi() {
  const abort = transferableAbortController()
  vi.stubGlobal('AbortController', abort.constructor)
  vi.stubGlobal('AbortSignal', abort.signal.constructor)
  afterAll(() => vi.unstubAllGlobals())
  beforeEach(() => server.use(
    http.get(`${pageApiUrl}/status`, () => HttpResponse.json(contextStatus)),
    http.get(`${pageApiUrl}/balances`, () => HttpResponse.json(staleBalances)),
    http.get(`${pageApiUrl}/performance`, () => HttpResponse.json({ markets: [], owner_available: false, scope: 'controller_lifetime_and_active_exposure', server_time: '2026-11-11T00:00:00Z' })),
    http.get(`${pageApiUrl}/profiles`, () => HttpResponse.json(pageProfiles)),
    http.get(`${pageApiUrl}/commands`, () => HttpResponse.json({ items: [], next_cursor: null })),
    http.get(`${pageApiUrl}/commands/:id`, ({ params }) => HttpResponse.json({ id: params.id, action: 'start', status: 'accepted', effective_version: null, error: null })),
    http.get(`${pageApiUrl}/credentials`, () => HttpResponse.json({ source: 'environment', exchanges: [] })),
    http.get(`${pageApiUrl}/recovery`, () => HttpResponse.json({ halted: false, check_available: false, reset_available: false, blockers: [], operation: null, control_version: 2 })),
    http.get(`${pageApiUrl}/access/accounts`, () => HttpResponse.json({ items: [], next_cursor: null })),
    http.get(`${pageApiUrl}/access/bootstrap`, () => HttpResponse.json({ google_auth_enabled: true, active_google_admins: 1 })),
    http.get(`${pageApiUrl}/audit`, () => HttpResponse.json({ items: [], next_cursor: null })),
    http.get(`${pageApiUrl}/sessions`, () => HttpResponse.json({ items: [], next_cursor: null })),
    http.get(`${pageApiUrl}/sessions/:id/events`, () => HttpResponse.json({ items: [], next_cursor: null })),
    http.get(`${pageApiUrl}/reconciliation`, () => HttpResponse.json({ check: null, report: { state: 'not_checked' } })),
    http.get(`${pageApiUrl}/orders`, () => HttpResponse.json({ items: [], next_cursor: null })),
    http.get(`${pageApiUrl}/fills`, () => HttpResponse.json({ items: [], next_cursor: null })),
  ))
}
export function pageWrapper(permissions = contextPrincipal.permissions) {
  return function Wrapper({ children }: PropsWithChildren) {
    const apiSession = { baseUrl: pageApiUrl, principalId: contextPrincipal.id, cacheIdentity: 'page-1', getToken: () => 'test-token' }
    const auth: AuthContextValue = { status: 'authenticated', identity: { ...contextPrincipal, permissions }, error: null, login: async () => {}, logout: () => {}, apiSession }
    return <AuthContext.Provider value={auth}><ApiProvider session={apiSession}><OperationalProvider>{children}</OperationalProvider></ApiProvider></AuthContext.Provider>
  }
}
