import { describe, expect, it } from 'vitest'
import type { ReadResponse } from '../../core/api/admin'
import { deriveCleanupProgress } from './connections'

type Receipt = ReadResponse<'getCredentialOperation'>
const receipt = (stage: Receipt['result']['stage'], reason: string | null = null, status: Receipt['status'] = 'applying') =>
  ({ status, result: { stage, reason_code: reason, blocked_stage: null, next_retry_at: null } }) as Receipt

describe('credential cleanup progress', () => {
  it('keeps uncertain order cleanup pending, preserving scheduled retry and reason', () => {
    const input = receipt('waiting_for_dependency', 'cleanup_orders_pending')
    input.result.next_retry_at = '2026-11-16T00:00:00Z'
    expect(deriveCleanupProgress(input)).toMatchObject({ stage: 'waiting_for_dependency', label: 'Waiting for order verification', reasonCode: 'cleanup_orders_pending', nextRetryAt: input.result.next_retry_at, complete: false })
  })
  it('never labels removed without successful status and every independent proof', () => {
    const input = receipt('removed', null, 'succeeded')
    expect(deriveCleanupProgress(input).complete).toBe(false)
    expect(deriveCleanupProgress(input).label).not.toBe('Disconnected')
    Object.assign(input.result, { orders_terminal_verified: true, positions_flat_verified: true, ledger_confirmed: true, consumers_detached: true })
    expect(deriveCleanupProgress(input)).toMatchObject({ complete: true, label: 'Disconnected' })
    expect(deriveCleanupProgress({ ...input, status: 'applying' }).complete).toBe(false)
  })
  it('maps stages and retains safe unknown stage details', () => {
    expect(deriveCleanupProgress(undefined)).toMatchObject({ stage: null, label: 'Waiting for operation status', complete: false })
    expect(deriveCleanupProgress(receipt('closing_positions')).label).toBe('Closing perpetual positions')
    expect(deriveCleanupProgress(receipt('action_required')).label).toBe('Action required')
  })
})
