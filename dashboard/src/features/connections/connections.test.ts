import { describe, expect, it } from 'vitest'
import { cleanupComplete, cleanupProofs, deriveCredentialSlot, validateCredentialBundle, type Catalogue, type CredentialStatus, type Identity, type RuntimeStatus, type Source } from './connections'

const identity = { id: 'admin-1', email: null, kind: 'google', role: 'write', role_label: 'Operator', permissions: { manage_credentials: true } } as Identity
const catalogue = { phase: 'ready', error: null, refreshing: false, data: { source: 'postgres_kms', exchanges: [{ exchange: 'okx', environment: 'demo', cleanup_mode: 'managed', connectors: ['spot'], disconnect_available: true, site: null, fields: [{ name: 'api_key', label: 'Key', secret: true, required: true }] }] } } as Source<Catalogue>
const slot = { connector: 'spot', label: 'OKX spot', status: 'connected', detail: '', configured: true, version: 4, changed_at: null, changed_by: null, observed_at: '2026-11-11T00:00:00Z', operation_id: null, operation_version: null, cleanup_operation_id: null, reason_code: null, actions: {
  configure: { allowed: true, detail: 'Configure', reason_code: null }, disconnect: { allowed: true, detail: 'Disconnect', reason_code: null }, retry_cleanup: { allowed: true, detail: 'Retry', reason_code: null }, repair_cleanup_key: { allowed: true, detail: 'Repair', reason_code: null },
} }
const status = { phase: 'ready', error: null, refreshing: false, data: { source: 'postgres_kms', environment: 'demo', exchange: 'okx', slots: [slot] } } as unknown as Source<CredentialStatus>
const runtime = (overrides: Partial<RuntimeStatus> = {}) => ({ phase: 'ready', error: null, refreshing: false, data: {
  desired_state: 'stopped', acknowledged_version: 8, control_version: 8, operator: { state: 'stopped', label: 'Stopped', detail: '', next_action: 'monitor' }, owner_available: true,
  server_time: '2026-11-11T00:00:00Z',
  observation: { observed_at: '2026-11-11T00:00:00Z', stale: false, session_matches: true, reason_code: null },
  ...overrides,
} } as Source<RuntimeStatus>)

describe('Connections workflow logic', () => {
  it('accepts server-connected status only while its owner/version observation remains fresh', () => {
    expect(deriveCredentialSlot(identity, catalogue, status, runtime(), 'okx', 'spot').connectionState).toBe('connected')
    expect(deriveCredentialSlot(identity, catalogue, status, runtime({ server_time: '2026-11-11T00:00:06Z' }), 'okx', 'spot').connectionState).toBe('checking')
    expect(deriveCredentialSlot(identity, catalogue, status, runtime({ server_time: null }), 'okx', 'spot').connectionState).toBe('checking')
  })

  it('expires a cached connected assertion even while the other source remains fresh', () => {
    const now = Date.now()
    const staleCredentials = { ...status, updatedAt: now - 6000 }
    const freshRuntime = { ...runtime(), updatedAt: now }
    expect(deriveCredentialSlot(identity, catalogue, staleCredentials, freshRuntime, 'okx', 'spot').connectionState).toBe('checking')
    const staleRuntime = { ...runtime(), updatedAt: now - 6000 }
    expect(deriveCredentialSlot(identity, catalogue, status, staleRuntime, 'okx', 'spot').connectionState).toBe('checking')
  })

  it('requires permission, server availability, managed source and acknowledged Stop before Set', () => {
    const ready = deriveCredentialSlot(identity, catalogue, status, runtime(), 'okx', 'spot')
    expect(ready.decisions.set.allowed).toBe(true)
    expect(deriveCredentialSlot({ ...identity, permissions: { manage_credentials: false } }, catalogue, status, runtime(), 'okx', 'spot').decisions.set.reasonCode).toBe('permission_required')
    expect(deriveCredentialSlot(identity, catalogue, status, runtime({ acknowledged_version: 7 }), 'okx', 'spot').decisions.set.reasonCode).toBe('stop_not_acknowledged')
    expect(deriveCredentialSlot(identity, catalogue, status, runtime(), 'missing', 'spot').decisions.set.reasonCode).toBe('availability_unknown')
    expect(deriveCredentialSlot(identity, { ...catalogue, data: { ...catalogue.data!, source: 'environment' } }, status, runtime(), 'okx', 'spot').decisions.set.reasonCode).toBe('managed_source_required')
  })

  it('fails closed for stale status and does not infer disconnected from absent configuration', () => {
    const stale = deriveCredentialSlot(identity, catalogue, { ...status, phase: 'error' }, runtime(), 'okx', 'spot')
    expect(stale.decisions.disconnect.reasonCode).toBe('credential_status_unavailable')
    expect(stale.decisions.retry.reasonCode).toBe('credential_status_unavailable')
    expect(stale.disconnectedProof).toBe(false)
  })

  it('validates every required field using catalogue names', () => {
    expect(validateCredentialBundle(catalogue.data!.exchanges[0].fields, { api_key: '  ' })).toEqual(['api_key'])
    expect(validateCredentialBundle(catalogue.data!.exchanges[0].fields, { api_key: 'secret' })).toEqual([])
  })

  it('requires all independent root cleanup proofs and terminal removed status', () => {
    const proof = { orders_terminal_verified: true, positions_flat_verified: true, ledger_confirmed: true, consumers_detached: true }
    const receipt = { status: 'succeeded', result: { stage: 'removed', ...proof } } as unknown as import('../../core/api/admin').ReadResponse<'getCredentialOperation'>
    expect(cleanupComplete(receipt)).toBe(true)
    expect(cleanupComplete({ ...receipt, result: { ...receipt.result, ledger_confirmed: false } })).toBe(false)
    expect(cleanupComplete({ ...receipt, status: 'applying' })).toBe(false)
    expect(cleanupProofs(undefined)).toEqual({ ordersTerminalVerified: false, positionsFlatVerified: false, ledgerConfirmed: false, consumersDetached: false })
  })
})
