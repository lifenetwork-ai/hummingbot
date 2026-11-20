import { describe, expect, it } from 'vitest'
import { cleanupComplete, cleanupProofs, credentialSetOutcome, deriveCredentialSlot, validateCredentialBundle, type Catalogue, type CredentialStatus, type Identity, type RuntimeStatus, type Source } from './connections'

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

  it('accepts an acknowledged Stop while the operator status reports attention', () => {
    const current = runtime({ operator: { state: 'needs_attention', label: 'Order verification needs review', detail: 'Recovery needs review.', next_action: 'contact_support' } })
    const result = deriveCredentialSlot(identity, catalogue, status, current, 'okx', 'spot')
    expect(result.stopAcknowledged).toBe(true)
    expect(result.decisions.set.allowed).toBe(true)
  })

  it('keeps server configure denial and exposes current runtime guidance', () => {
    const deniedSlot = { ...slot, actions: { ...slot.actions, configure: { allowed: false, detail: 'Stop and confirm order cleanup first.', reason_code: 'bot_must_be_stopped' } } }
    const deniedStatus = { ...status, data: { ...status.data!, slots: [deniedSlot] } }
    const current = runtime({ operator: { state: 'needs_attention', label: 'Order verification needs review', detail: 'Check the stopped runtime before changing credentials.', next_action: 'contact_support' } })
    const result = deriveCredentialSlot(identity, catalogue, deniedStatus, current, 'okx', 'spot')
    expect(result.decisions.set).toMatchObject({ allowed: false, reasonCode: 'bot_must_be_stopped' })
    expect(result.runtimeGuidance).toBe('Check the stopped runtime before changing credentials.')
  })

  it('reports credential load only from the matching successful loaded receipt', () => {
    const receipt = { id: 'set-1', action: 'credentials_set', status: 'succeeded', error: null, result: { stage: 'loaded', reason_code: null } } as unknown as import('../../core/api/admin').ReadResponse<'getCredentialOperation'>
    expect(credentialSetOutcome(receipt, 'set-1')).toMatchObject({ state: 'loaded' })
    expect(credentialSetOutcome({ ...receipt, id: 'old' }, 'set-1')).toBeNull()
    expect(credentialSetOutcome({ ...receipt, result: { ...receipt.result, stage: 'checking' } }, 'set-1')).toMatchObject({ state: 'incomplete' })
    expect(credentialSetOutcome({ ...receipt, status: 'failed', error: 'credential_load_failed' }, 'set-1')).toMatchObject({ state: 'failed', detail: 'credential_load_failed' })
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

  it('surfaces a failed load as connection failed instead of an endless checking state', () => {
    const failedStatus = { ...status, data: { ...status.data!, slots: [{ ...slot, status: 'failed', reason_code: 'exchange_credentials_rejected', detail: 'The saved connection could not be loaded.' }] } }
    expect(deriveCredentialSlot(identity, catalogue, failedStatus, runtime(), 'okx', 'spot').connectionState).toBe('failed')
    // A later verified connected observation overrides the historical failure.
    expect(deriveCredentialSlot(identity, catalogue, status, runtime(), 'okx', 'spot').connectionState).toBe('connected')
  })

  it.each(['catalogue', 'status'] as const)('blocks cleanup mutations when %s credentials are deployment-managed', (source) => {
    const externalCatalogue = source === 'catalogue'
      ? { ...catalogue, data: { ...catalogue.data!, source: 'environment' as const } } : catalogue
    const externalStatus = source === 'status'
      ? { ...status, data: { ...status.data!, source: 'environment' as const } } : status
    const model = deriveCredentialSlot(identity, externalCatalogue, externalStatus, runtime(), 'okx', 'spot')
    for (const action of ['set', 'disconnect', 'retry', 'repair'] as const) {
      expect(model.decisions[action]).toMatchObject({ allowed: false, reasonCode: 'managed_source_required' })
    }
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
