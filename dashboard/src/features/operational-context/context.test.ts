import { describe, expect, it } from 'vitest'
import { contextPrincipal as principal, contextStatus as status } from '../../test/mocks/contextFixtures'
import { staleBalances } from '../../test/mocks/adminFixtures'
import { deriveOperationalContext, type ContextSource } from './context'

function ready<T>(data: T): ContextSource<T> { return { phase: 'ready', data, error: null, refreshing: false } }
function derive(overrides: Partial<Parameters<typeof deriveOperationalContext>[0]> = {}) {
  return deriveOperationalContext({ identity: principal, status: ready(status), balances: ready(staleBalances), ...overrides })
}

describe('operational context mapping', () => {
  it('derives display freshness from query state while preserving the raw observation', () => {
    expect(derive().observationState).toBe('fresh')
    const failed = derive({ status: { ...ready(status), phase: 'error', error: new Error('offline') } })
    expect(failed.observationState).toBe('stale')
    expect(failed.observation?.stale).toBe(false)
    expect(failed.observation?.observed_at).toBe(status.observation?.observed_at)
  })

  it('does not label missing, unavailable-owner or mismatched-session observations fresh', () => {
    expect(derive({ status: { phase: 'loading', error: null, refreshing: true } }).observationState).toBe('unknown')
    expect(derive({ status: ready({ ...status, observation: null }) }).observationState).toBe('unknown')
    expect(derive({ status: ready({ ...status, owner_available: false }) }).observationState).toBe('unknown')
    expect(derive({ status: ready({ ...status, observation: { ...status.observation!, session_matches: false } }) }).observationState).toBe('session_mismatch')
    expect(derive({ status: ready({ ...status, observation: { ...status.observation!, stale: true } }) }).observationState).toBe('stale')
  })

  it('preserves backend identity, versions, accounts and exact balances without claiming freshness', () => {
    const result = derive()
    expect(result.identity).toEqual(principal)
    expect(result.botId).toBe('bot-a')
    expect(result.controlVersion).toBe(2)
    expect(result.accounts[0]).toMatchObject({ verification: 'verified', observation: 'stale' })
    expect(result.accounts[0].data.balances[0].equity).toBe('123.000000000000000001')
    expect(result.actions.stop.allowed).toBe(true)
  })

  it('combines operate permission and backend action availability, preserving blocker detail', () => {
    const result = derive()
    expect(result.actions.start).toEqual({ allowed: false, reasonCode: 'session_active', detail: 'Session is active.' })
    expect(result.actions.switch.allowed).toBe(true)
  })

  it.each(['read', 'write', 'admin'] as const)('uses permissions rather than role %s to authorize', (role) => {
    expect(derive({ identity: { ...principal, role, permissions: { operate: false } } }).actions.stop.reasonCode).toBe('permission_required')
  })

  it('does not authorize an anonymous principal or an unknown action', () => {
    expect(derive({ identity: null }).actions.stop.allowed).toBe(false)
    expect(derive({ status: ready({ ...status, actions: {} }) }).actions.stop.reasonCode).toBe('availability_unknown')
  })

  it('disables commands when status refresh failed but retains last-good context', () => {
    const result = derive({ status: { ...ready(status), phase: 'error', error: new Error('offline') } })
    expect(result.botId).toBe('bot-a')
    expect(result.actions.stop.reasonCode).toBe('status_unavailable')
    expect(result.status.error?.message).toBe('offline')
  })

  it('does not block Stop because observation is stale or session mismatches', () => {
    const result = derive({ status: ready({ ...status, owner_available: false,
      observation: { ...status.observation!, stale: true, session_matches: false } }) })
    expect(result.observation).toMatchObject({ stale: true, session_matches: false })
    expect(result.actions.stop.allowed).toBe(true)
  })

  it('keeps status actions available when balances fail independently', () => {
    const result = derive({ balances: { phase: 'error', error: new Error('balances offline'), refreshing: false } })
    expect(result.actions.stop.allowed).toBe(true)
    expect(result.accounts).toEqual([])
    expect(result.balances.phase).toBe('error')
  })

  it('distinguishes missing and unknown deployment identity from account verification', () => {
    const result = derive({ status: ready({ ...status, process_identity: null, owner_identity: null }) })
    expect(result.processEnvironment).toBe('unknown')
    expect(result.environmentMismatch).toBe(false)
    expect(result.accounts[0].verification).toBe('verified')
  })

  it('reports process/owner mismatch even when balances are empty', () => {
    const result = derive({ status: ready({ ...status, owner_identity: { ...status.owner_identity!, trading_environment: 'live' } }),
      balances: ready({ ...staleBalances, accounts: [] }) })
    expect(result.environmentMismatch).toBe(true)
    expect(result.accounts).toEqual([])
  })

  it('retains all accounts and identifies a current verified environment mismatch', () => {
    const account = staleBalances.accounts[0]
    const result = derive({ balances: ready({ ...staleBalances, owner_available: true, accounts: [account,
      { ...account, account_id: 'acct-live', identity_verified: true, environment: 'live', balances: [{ ...account.balances[0], stale: false }] }] }) })
    expect(result.accounts).toHaveLength(2)
    expect(result.accounts[1]).toMatchObject({ verification: 'verified', observation: 'fresh', environmentMismatch: true })
    expect(result.environmentMismatch).toBe(true)
  })

  it('does not use saved account flags to conclude freshness or mismatch when owner is unavailable', () => {
    const account = staleBalances.accounts[0]
    const result = derive({ balances: ready({ ...staleBalances, owner_available: false,
      accounts: [{ ...account, environment: 'live', balances: [{ ...account.balances[0], stale: false }] }] }) })
    expect(result.accounts[0].observation).toBe('stale')
    expect(result.accounts[0].environmentMismatch).toBe(false)
    expect(result.environmentMismatch).toBe(false)
    expect(result.accounts[0].data.environment).toBe('live')
    expect(result.actions.stop.allowed).toBe(true)
  })

  it('does not infer a current deployment mismatch from an unavailable owner or errored status', () => {
    const mismatched = { ...status, owner_available: false, owner_identity: { ...status.owner_identity!, trading_environment: 'live' as const } }
    expect(derive({ status: ready(mismatched) }).environmentMismatch).toBe(false)
    expect(derive({ status: { ...ready({ ...mismatched, owner_available: true }), phase: 'error' } }).environmentMismatch).toBe(false)
  })

  it('treats a verified flag without account ID as unverified and does not label absent latest balances fresh', () => {
    const account = staleBalances.accounts[0]
    const result = derive({ balances: ready({ ...staleBalances, accounts: [{ ...account, account_id: null,
      balances: [{ ...account.balances[0], stale: false, present_in_latest_snapshot: false }] }] }) })
    expect(result.accounts[0]).toMatchObject({ verification: 'unverified', observation: 'stale' })
  })

  it('exposes loading/empty context and absent permissions without inventing values', () => {
    const result = derive({ status: { phase: 'loading', refreshing: true, error: null } })
    expect(result.botId).toBeNull()
    expect(result.controlVersion).toBeNull()
    expect(result.markets).toEqual([])
    expect(result.actions.start.allowed).toBe(false)
  })
})
