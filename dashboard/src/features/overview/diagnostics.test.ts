import { describe, expect, it } from 'vitest'
import type { ReadResponse } from '../../core/api/admin'
import { contextStatus } from '../../test/mocks/contextFixtures'
import { deriveOperationalDiagnostics, deriveProcessDiagnostics } from './diagnostics'
import { ApiError } from '../../core/api/client'

type Status = ReadResponse<'getRuntimeStatus'>
const now = Date.parse('2026-11-16T00:00:05Z')
const base: Status = {
  ...contextStatus,
  server_time: new Date(now).toISOString(),
  observation: { observed_at: new Date(now).toISOString(), reason_code: null, stale: false, session_matches: true },
  markets: [{
    market: 'perpetual', connector: 'okx', pair: 'BTC-USDT-SWAP', admitted: true, ready: true, reason: null,
    inventory_skew: null, quote_guard: null, spread_policy: null, reference_price: null,
    inventory_reduction: {
      mode: 'close_first', price_mode: 'breakeven', observed_at: new Date(now - 5000).toISOString(), stale: false,
      fee_ready: true, sides: {
        BUY: { effect: 'CLOSE', target_leg: 'SHORT', owned_base: '1.25000001', venue_base: '1.25', reserved_base: '1.3', available_base: '0', reason_code: 'reduction_reserved' },
        SELL: { effect: 'OPEN', target_leg: 'LONG', owned_base: null, venue_base: '0', reserved_base: '0', available_base: '0.00000001', reason_code: null },
      },
    },
  }],
  spot_outflow: {
    binding: null, buy_allowed: true, coverage_start_ms: null, fill_count: 0, last_buy_denial: null,
    non_usdt_fees: {}, observed_at: new Date(now).toISOString(), pending_terminal_orders: 1,
    policy: { window_seconds: 3600, max_net_outflow_quote: '100', trading_pair: 'BTC-USDT', fee_allowance_rate: '0.001' }, ready: true, reason_code: null,
    recorded_net_outflow_quote: '0', remaining_quote: '100', reserved_quote: '0', stale: false,
    window_end_ms: null, window_start_ms: null,
  },
  service: {
    observed_at: new Date(now).toISOString(), observer_age_seconds: 0, persisted_state_available: true,
    phase: 'running', reason_code: null, stale: false, state_source: 'postgres',
    incidents: [{
      active_fallback: null, detail: null, first_seen_at: new Date(now - 1000).toISOString(), last_healthy_at: null,
      last_seen_at: new Date(now).toISOString(), next_retry_at: null, operator_action_required: false,
      reason_code: 'spot_outflow_terminal_fill_coverage', retry_count: 0, scope: 'spot', severity: 'warning',
    }, {
      active_fallback: null, detail: null, first_seen_at: new Date(now - 1000).toISOString(), last_healthy_at: null,
      last_seen_at: new Date(now).toISOString(), next_retry_at: null, operator_action_required: false,
      reason_code: 'other_incident', retry_count: 0, scope: 'spot', severity: 'error',
    }],
  },
}

describe('deriveOperationalDiagnostics', () => {
  it('keeps 503 process diagnostics separate from retained owner/account data', () => {
    const error = new ApiError(503, { request_id: 'req-1', service: { state_source: 'process', persisted_state_available: false, phase: 'unavailable', reason_code: 'database_unavailable', observed_at: '2026-11-16T00:00:00Z' } })
    expect(deriveProcessDiagnostics(error)).toMatchObject({ observation: 'process', persistedStateAvailable: false, phase: 'unavailable', requestId: 'req-1' })
    expect(deriveProcessDiagnostics(new ApiError(503))).toBeNull()
    expect(deriveProcessDiagnostics(new ApiError(503, { service: { state_source: 'postgres' } }))).toBeNull()
    expect(deriveProcessDiagnostics(new Error('offline'))).toBeNull()
  })
  it('keeps server freshness for outflow and marks failed service reads stale independently', () => {
    const result = deriveOperationalDiagnostics(base, true, now + 6000)
    expect(result.reductions[0]?.observation).toBe('stale')
    expect(result.outflow.waitingForTerminalVerification).toBe(true)
    expect(deriveOperationalDiagnostics(base, false, now).service.observation).toBe('stale')
  })
  it('keeps exact reduction evidence and accepts the five-second freshness boundary', () => {
    const result = deriveOperationalDiagnostics(base, true, now)
    expect(result.reductions[0]).toMatchObject({
      market: 'perpetual', observation: 'fresh', observedAt: new Date(now - 5000).toISOString(),
      evidence: { fee_ready: true, sides: { BUY: { owned_base: '1.25000001', reserved_base: '1.3', available_base: '0', effect: 'CLOSE' } } },
    })
  })

  it('rejects stale, future, expired and owner-unverified reduction observations', () => {
    const original = base.markets![0]!.inventory_reduction!
    const stale = { ...original, stale: true }
    const expired = { ...original, observed_at: new Date(now - 5001).toISOString() }
    const future = { ...original, observed_at: new Date(now + 1).toISOString() }
    expect(deriveOperationalDiagnostics({ ...base, markets: [{ ...base.markets![0]!, inventory_reduction: stale }] }, true, now).reductions[0]?.observation).toBe('stale')
    expect(deriveOperationalDiagnostics({ ...base, markets: [{ ...base.markets![0]!, inventory_reduction: expired }] }, true, now).reductions[0]?.observation).toBe('stale')
    expect(deriveOperationalDiagnostics({ ...base, markets: [{ ...base.markets![0]!, inventory_reduction: future }] }, true, now).reductions[0]?.observation).toBe('unknown')
    expect(deriveOperationalDiagnostics(base, false, now).reductions[0]?.observation).toBe('unknown')
  })

  it('distinguishes null reduction, zero pending count, and missing outflow data', () => {
    expect(deriveOperationalDiagnostics({ ...base, markets: [{ ...base.markets![0]!, inventory_reduction: null }] }, true, now).reductions).toEqual([])
    expect(deriveOperationalDiagnostics({ ...base, spot_outflow: { ...base.spot_outflow!, pending_terminal_orders: 0 } }, true, now).outflow).toMatchObject({ pendingTerminalOrders: 0, waitingForTerminalVerification: false })
    expect(deriveOperationalDiagnostics({ ...base, spot_outflow: null }, true, now).outflow).toMatchObject({ pendingTerminalOrders: null, waitingForTerminalVerification: false, observation: 'unknown' })
  })

  it('requires a fresh owner and fresh outflow observation before showing the wait', () => {
    expect(deriveOperationalDiagnostics(base, false, now).outflow).toMatchObject({ observation: 'unknown', waitingForTerminalVerification: false })
    const stale = { ...base, spot_outflow: { ...base.spot_outflow!, stale: true } }
    expect(deriveOperationalDiagnostics(stale, true, now).outflow).toMatchObject({ observation: 'stale', waitingForTerminalVerification: false })
    const future = { ...base, spot_outflow: { ...base.spot_outflow!, observed_at: new Date(now + 1).toISOString() } }
    expect(deriveOperationalDiagnostics(future, true, now).outflow).toMatchObject({ observation: 'unknown', waitingForTerminalVerification: false })
  })

  it('shows the pending terminal-order wait even when no scoped incident exists', () => {
    const status = { ...base, service: { ...base.service!, incidents: [] } }
    expect(deriveOperationalDiagnostics(status, true, now).outflow).toMatchObject({ pendingTerminalOrders: 1, waitingForTerminalVerification: true })
    expect(deriveOperationalDiagnostics(status, true, now).terminalIncidents).toEqual([])
  })

  it('only passes through known terminal-verification incident stages', () => {
    expect(deriveOperationalDiagnostics(base, true, now).terminalIncidents.map(({ terminalStage }) => terminalStage)).toEqual(['fill_coverage'])
  })
})
