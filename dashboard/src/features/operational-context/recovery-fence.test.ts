import { describe, expect, it } from 'vitest'
import { deriveRecoveryFence } from './recovery-fence'
import { recoveryCheckReceipt, recoveryResetReceipt } from '../../test/mocks/receiptFixtures'
const ref = { kind: 'recovery' as const, id: 'r1' }
describe('scoped recovery fence', () => {
  it('fences pending/unknown outcomes and an unexpired check review', () => {
    expect(deriveRecoveryFence(ref, { phase: 'loading' }).active).toBe(true)
    expect(deriveRecoveryFence(ref, { phase: 'error' }).phase).toBe('unknown')
    expect(deriveRecoveryFence(ref, { phase: 'ready', data: { ...recoveryCheckReceipt, status: 'applying' } }).active).toBe(true)
    expect(deriveRecoveryFence(ref, { phase: 'ready', data: { ...recoveryCheckReceipt, can_recover: true, expires_at: new Date(2000).toISOString() } }, 1000).active).toBe(true)
  })
  it('releases only a known terminal/expired workflow and never fabricates a global fence', () => {
    expect(deriveRecoveryFence(undefined, { phase: 'loading' }).active).toBe(false)
    expect(deriveRecoveryFence(ref, { phase: 'ready', data: recoveryResetReceipt }).active).toBe(false)
    expect(deriveRecoveryFence(ref, { phase: 'ready', data: { ...recoveryCheckReceipt, status: 'failed' } }).active).toBe(false)
    expect(deriveRecoveryFence(ref, { phase: 'ready', data: { ...recoveryCheckReceipt, can_recover: true, expires_at: new Date(1000).toISOString() } }, 2000).active).toBe(false)
  })
})
