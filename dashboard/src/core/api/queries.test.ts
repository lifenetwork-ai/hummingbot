import { describe, expect, it } from 'vitest'
import { ApiError } from './client'
import { pollingInterval, receiptFinished } from './queries'
import { readRetryDelay, retryRead, createDashboardQueryClient } from './queryClient'
import { invalidateAfterMutation, invalidateAfterReceipt } from './invalidation'
import type { WriteOperation } from './admin'

describe('read and polling policies', () => {
  it('only retries bounded network and explicitly retryable unavailable reads', () => {
    expect(retryRead(0, new TypeError('fetch failed'))).toBe(true)
    expect(retryRead(2, new TypeError('fetch failed'))).toBe(false)
    expect(retryRead(0, new DOMException('aborted', 'AbortError'))).toBe(false)
    expect(retryRead(0, new SyntaxError('invalid JSON'))).toBe(false)
    expect(retryRead(0, new ApiError(503, { retry: 'reconnect' }))).toBe(false)
    expect(retryRead(0, new ApiError(503, { retry: 'same_request' }))).toBe(true)
    expect(readRetryDelay(1, new ApiError(503, { retry_after_seconds: 5 }))).toBe(5000)
    expect(readRetryDelay(9, new TypeError('fetch failed'))).toBe(30_000)
  })
  it('keeps pending receipts polling and distinguishes terminal receipts from report outcomes', () => {
    for (const operation of ['getCommand', 'getCredentialOperation', 'getReconciliationById', 'getRecoveryOperation'] as const) {
      expect(pollingInterval(operation, { status: 'accepted' }, null, 0)).toBe(1000)
      for (const status of ['succeeded', 'failed', 'superseded']) {
        expect(receiptFinished(operation, { status })).toBe(true)
        expect(pollingInterval(operation, { status, report: { state: 'mismatch' } }, null, 0)).toBe(false)
      }
    }
    expect(receiptFinished('getReconciliation', { status: 'succeeded' })).toBe(false)
    expect(receiptFinished('getCommand', null)).toBe(false)
  })
  it('uses observed stale-statistics retry hints and never polls unsupported reads', () => {
    expect(pollingInterval('getDailyStatistics', { freshness: 'stale', retry_after_seconds: 3 }, null, 0)).toBe(3000)
    expect(pollingInterval('getRuntimeStatus', {}, null, 0)).toBe(2000)
    expect(pollingInterval('getDailyStatistics', {}, null, 0)).toBe(30_000)
    expect(pollingInterval('listProfiles', {}, null, 0)).toBe(false)
    expect(pollingInterval('getCommand', {}, new ApiError(401), 0)).toBe(false)
    expect(pollingInterval('getCommand', {}, new SyntaxError('bad payload'), 0)).toBe(false)
    expect(pollingInterval('getCommand', {}, new ApiError(503, { retry_after_seconds: 7 }), 0)).toBe(7000)
  })
})

describe('mutation/receipt cache dependencies', () => {
  it.each<WriteOperation>(['submitCommand', 'setExchangeCredentials', 'disconnectExchangeCredentials',
    'repairCleanupCredentials', 'retryCredentialCleanup', 'requestReconciliation', 'adminLogin',
    'addAdminAccount', 'changeAdminAccount', 'checkRecovery', 'resetRecovery'])('invalidates audit but preserves unrelated daily statistics after %s', async (operation) => {
    const client = createDashboardQueryClient()
    try {
      client.setQueryData(['admin', 'getAdminAudit', {}], {})
      client.setQueryData(['admin', 'getDailyStatistics', {}], { observation: 'retained' })
      await invalidateAfterMutation(client, operation)
      expect(client.getQueryState(['admin', 'getAdminAudit', {}])?.isInvalidated).toBe(true)
      expect(client.getQueryState(['admin', 'getDailyStatistics', {}])?.isInvalidated).toBe(false)
    } finally { client.clear() }
  })
  it.each(['getCommand', 'getCredentialOperation', 'getReconciliationById', 'getRecoveryOperation'] as const)('refreshes dependent observations after terminal %s', async (operation) => {
    const client = createDashboardQueryClient()
    try {
      const target = operation === 'getReconciliationById' ? 'getReconciliation' : 'getRuntimeStatus'
      client.setQueryData(['admin', target, {}], {})
      client.setQueryData(['admin', 'getSessionConfiguration', { path: { session_id: 'session-a' } }], {})
      await invalidateAfterReceipt(client, operation)
      expect(client.getQueryState(['admin', target, {}])?.isInvalidated).toBe(true)
      expect(client.getQueryState(['admin', 'getSessionConfiguration', { path: { session_id: 'session-a' } }])?.isInvalidated)
        .toBe(operation !== 'getReconciliationById')
    } finally { client.clear() }
  })
  it.each(['checkRecovery', 'resetRecovery', 'getRecoveryOperation'] as const)('refreshes recovery state after %s', async (operation) => {
    const client = createDashboardQueryClient()
    try {
      client.setQueryData(['admin', 'getRecovery', {}], {})
      client.setQueryData(['admin', 'getPositions', {}], {})
      if (operation === 'getRecoveryOperation') await invalidateAfterReceipt(client, operation)
      else await invalidateAfterMutation(client, operation)
      expect(client.getQueryState(['admin', 'getRecovery', {}])?.isInvalidated).toBe(true)
      expect(client.getQueryState(['admin', 'getPositions', {}])?.isInvalidated).toBe(operation !== 'checkRecovery')
    } finally { client.clear() }
  })
})
